"""Amendment service.

Turning an amendment proposal into an active legal modification:
  - create_amendment atoms clause changes (change_type + section_key).
  - activate_amendment validates the agreement is eligible, computes the
    consolidated current-terms snapshot (base + all active amendments +
    this one, in order), persists it for immutability, and opens a new
    version in the version stream.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.amendment import AgreementAmendment, AmendmentChange
from app.services.agreement_versioning import create_version
from app.services.lifecycle_service import (
    apply_transition,
    get_current_terms,
)


class AmendmentError(Exception):
    """Raised when an amendment cannot be created or activated."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def next_amendment_number(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> int:
    result = await db.execute(
        select(func.max(AgreementAmendment.amendment_number)).where(
            AgreementAmendment.agreement_id == agreement_id
        )
    )
    current = result.scalar()
    return 1 if current is None else current + 1


async def create_amendment(
    db: AsyncSession,
    *,
    agreement: Agreement,
    title: str,
    description: str | None,
    reason: str | None,
    changes: list[dict],
    proposed_by: uuid.UUID,
) -> AgreementAmendment:
    """Create a new amendment row with its clause changes.

    changes: list of {section_key, change_type ('replace'|'insert'|'delete'),
             old_text, new_text, structured_delta}
    """
    if agreement.status not in ("executed", "active", "expired"):
        raise AmendmentError(
            f"Agreement in status '{agreement.status}' cannot be amended; "
            "it must first be executed"
        )

    if not changes:
        raise AmendmentError("An amendment must contain at least one clause change")

    number = await next_amendment_number(db, agreement.id)

    amendment = AgreementAmendment(
        agreement_id=agreement.id,
        amendment_number=number,
        title=title,
        description=description,
        reason=reason,
        status="proposed",
        version_number=1,
        base_version_number=1,
        proposed_by=proposed_by,
        created_at=now_utc(),
    )
    db.add(amendment)
    await db.flush()

    for c in changes:
        if "section_key" not in c or "new_text" not in c:
            raise AmendmentError("Each change requires section_key and new_text")
        db.add(
            AmendmentChange(
                amendment_id=amendment.id,
                section_key=c["section_key"],
                change_type=c.get("change_type", "replace"),
                old_text=c.get("old_text"),
                new_text=c["new_text"],
                structured_delta=c.get("structured_delta"),
            )
        )
    await db.flush()
    await db.refresh(amendment, attribute_names=["changes"])
    return amendment


async def apply_downstream_invalidation(
    db: AsyncSession,
    *,
    amendment: AgreementAmendment,
    activated_by: uuid.UUID,
) -> dict:
    """Downstream invalidation pipeline (spec §3.22.45-55).

    After an amendment activates, dependent intelligence must refresh or be
    explicitly marked stale — never silently wrong:
      1. obligations tied to superseded clauses are superseded
         (handled by activate_amendment; counted here),
      2. in-flight approvals on the amended agreement are version-locked
         (§3.22.25 signing/approval invalidation),
      3. risk findings are flagged for re-analysis (status stays, a marker
         records staleness),
      4. graph nodes for the agreement are marked for re-projection.
    """
    from app.models.ai_analysis import RiskFinding
    from app.models.approval import ApprovalRecord
    from app.models.risk_graph import RiskGraphNode
    from sqlalchemy import select as _select

    agreement_id = amendment.agreement_id

    # 2. version-lock in-flight approvals
    locked = (
        await db.execute(
            _select(ApprovalRecord).where(
                ApprovalRecord.agreement_id == agreement_id,
                ApprovalRecord.status == "in_progress",
            )
        )
    ).scalars().all()
    from datetime import datetime as _dt, timezone as _tz

    for record in locked:
        record.version_locked_at = _dt.now(_tz.utc)

    # 3. mark risk findings stale for re-analysis
    findings = (
        await db.execute(
            _select(RiskFinding).where(RiskFinding.agreement_id == agreement_id)
        )
    ).scalars().all()
    for finding in findings:
        finding.evidence = dict(finding.evidence or {}) | {"stale_after_amendment": str(amendment.id)}

    # 4. mark graph nodes for re-projection
    nodes = (
        await db.execute(
            _select(RiskGraphNode).where(
                RiskGraphNode.organization_id.is_not(None),
                RiskGraphNode.entity_id == agreement_id,
            )
        )
    ).scalars().all()
    for node in nodes:
        node.properties = dict(node.properties or {}) | {
            "stale_after_amendment": str(amendment.id)
        }

    await db.flush()
    return {
        "amendment_id": str(amendment.id),
        "approvals_version_locked": len(locked),
        "risk_findings_marked_stale": len(findings),
        "graph_nodes_marked_stale": len(nodes),
    }


async def activate_amendment(
    db: AsyncSession,
    *,
    amendment: AgreementAmendment,
    activated_by: uuid.UUID,
    effective_date: date | None = None,
    org_id: uuid.UUID,
) -> AgreementAmendment:
    """Activate an amendment and persist the consolidated current terms."""
    if amendment.status == "active":
        raise AmendmentError("Amendment is already active")
    if amendment.status == "superseded":
        raise AmendmentError("Superseded amendments cannot be activated")

    agreement_result = await db.execute(
        select(Agreement).where(Agreement.id == amendment.agreement_id)
    )
    agreement = agreement_result.scalar_one_or_none()
    if agreement is None:
        raise AmendmentError("Agreement not found")

    # Consolidated terms before this amendment, then fold this one in.
    current = await get_current_terms(db, agreement)
    base = current["consolidated_terms"]

    # Conflict detection: refuse to activate an amendment whose base text no
    # longer matches the section as it currently stands. A mismatch means a
    # later amendment or redline already changed that clause, so activating on
    # a stale base would silently overwrite newer terms.
    for change in sorted(amendment.changes, key=lambda c: c.section_key):
        if change.change_type == "insert" or change.old_text is None:
            continue
        existing = _resolve_section_text(base, change.section_key)
        if existing is not None and existing != change.old_text:
            raise AmendmentError(
                f"Conflict on section '{change.section_key}': current terms "
                "differ from this amendment's base; re-propose against the "
                "latest terms"
            )

    for change in sorted(amendment.changes, key=lambda c: c.section_key):
        _fold_change(base, change)

    amendment.status = "active"
    amendment.effective_date = effective_date or date.today()
    amendment.approved_by = activated_by
    amendment.approved_at = now_utc()
    amendment.current_terms_snapshot = base
    await db.flush()

    # Open a version in the stream capturing the amended terms.
    from app.services.lifecycle_service import _record_audit

    consolidated_text = _terms_to_text(base)
    await create_version(
        db,
        agreement,
        content=consolidated_text,
        created_by=activated_by,
        status="amended",
    )
    await db.flush()

    # Re-link obligations: any active obligation sourced from a clause this
    # amendment touches is superseded (its terms no longer hold). Consumers
    # re-extract obligations from the new amended version.
    from app.models.obligation import Obligation
    from app.services.obligation_lifecycle import supersede_obligation

    affected = {_normalise_key(c.section_key) for c in amendment.changes}
    obligation_result = await db.execute(
        select(Obligation).where(
            Obligation.agreement_id == agreement.id,
            Obligation.clause_identifier.is_not(None),
        )
    )
    superseded_count = 0
    for obligation in obligation_result.scalars().all():
        if obligation.status in ("COMPLETED", "WAIVED", "CANCELLED", "SUPERSEDED"):
            continue
        if _normalise_key(obligation.clause_identifier or "") in affected:
            await supersede_obligation(db, obligation, activated_by)
            superseded_count += 1
    if superseded_count:
        await db.flush()

    await _record_audit(
        db,
        tenant_id=org_id,
        agreement_id=agreement.id,
        actor_id=activated_by,
        actor_type="user",
        action="AMENDMENT_ACTIVATED",
        resource_type="agreement_amendment",
        resource_id=amendment.id,
        metadata_json={
            "amendment_number": amendment.amendment_number,
            "effective_date": str(amendment.effective_date),
            "superseded_obligations": superseded_count,
        },
    )
    await db.flush()
    return amendment


def _normalise_key(key: str) -> str:
    return key.strip().lower()


def _resolve_section_text(terms: dict, section_key: str) -> str | None:
    """Resolve a dotted section path to its current text, if present."""
    parts = [p for p in section_key.split(".") if p]
    target = terms
    try:
        for p in parts:
            if not isinstance(target, dict) or p not in target:
                return None
            target = target[p]
    except (TypeError, KeyError):
        return None
    if isinstance(target, dict):
        return "\n".join(str(v) for v in target.values())
    return str(target) if target is not None else None


def _fold_change(terms: dict, change: AmendmentChange) -> None:
    """Apply a structural clause change into a terms mapping."""
    parts = [p for p in change.section_key.split(".") if p]
    target = terms
    for p in parts[:-1]:
        if not isinstance(target.get(p), dict):
            target[p] = {}
        target = target[p]
    leaf = parts[-1]
    if change.change_type == "delete":
        target.pop(leaf, None)
    else:
        target[leaf] = change.new_text


def _terms_to_text(terms: dict) -> str:
    """Render consolidated terms as a stable document body."""
    lines = ["CONSOLIDATED CURRENT TERMS"]
    for key, value in terms.items():
        if isinstance(value, dict):
            lines.append("")
            lines.append(f"{key.upper()}")
            for k, v in value.items():
                lines.append(f"  {k}: {v}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)