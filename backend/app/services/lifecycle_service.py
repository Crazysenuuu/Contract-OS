"""Lifecycle service.

The single place that mutates agreement.status. All state changes are
validated against the data-driven table of transition rules
(agreement_status_transitions), so no route/app code sets status
directly — it requests a transition by action key.

Also hosts the current-terms consolidation engine (2.07.26) used to
serve an agreement's merged terms across the base and all active
amendments.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.amendment import AgreementAmendment
from app.models.audit import AuditEvent
from app.models.lifecycle import AgreementState, StatusTransitionRule
from app.services.audit_service import record_event


# Core lifecycle states. These drive the state registry; everything else
# is data. Kept in sync with the seeded agreement_states rows.
CORE_STATES = [
    ("draft", "Draft", False),
    ("negotiating", "Negotiating", False),
    ("internal_review", "Internal Review", False),
    ("approved", "Approved", False),
    ("signing", "In Signing", False),
    ("sent", "Sent to Counterparty", False),
    ("viewed", "Viewed by Counterparty", False),
    ("executed", "Executed", False),
    ("active", "Active", False),
    ("expiring", "Expiring", False),
    ("renewed", "Renewed", False),
    ("expired", "Expired", True),
    ("terminated", "Terminated", True),
    ("superseded", "Superseded", True),
    ("cancelled", "Cancelled", True),
]


class TransitionNotAllowed(Exception):
    """Raised when an agreement cannot undergo a requested transition."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def now_utc():
    return datetime.now(timezone.utc)


IMMUTABLE_STATES = {"executed", "active", "expired", "terminated", "superseded"}


def agreement_is_immutable(agreement: Agreement) -> bool:
    """True once an agreement's terms may no longer be edited directly.

    Beyond these terminal/executed states, edits must go through an
    amendment rather than mutating the base agreement.
    """
    return agreement.status in IMMUTABLE_STATES


async def get_state_keys(db: AsyncSession) -> list[str]:
    """Return all registered lifecycle state keys."""
    result = await db.execute(select(AgreementState.status))
    return [row[0] for row in result.all()]


def _resolve_rule(
    rules: list[StatusTransitionRule],
    org_id: uuid.UUID | None,
    agreement_type_id: uuid.UUID | None,
) -> StatusTransitionRule | None:
    """Pick the most specific matching rule.

    Specificity: org+type > org > type > global.
    """
    for r in rules:
        if r.organization_id is not None and r.agreement_type_id is not None:
            if r.organization_id == org_id and r.agreement_type_id == agreement_type_id:
                return r
    for r in rules:
        if r.organization_id is not None and r.agreement_type_id is None:
            if r.organization_id == org_id:
                return r
    for r in rules:
        if r.organization_id is None and r.agreement_type_id is not None:
            if r.agreement_type_id == agreement_type_id:
                return r
    for r in rules:
        if r.organization_id is None and r.agreement_type_id is None:
            return r
    return None


async def _load_rules(
    db: AsyncSession,
    action_key: str,
    from_status: str,
    org_id: uuid.UUID | None,
    agreement_type_id: uuid.UUID | None,
) -> StatusTransitionRule | None:
    """Load all candidate rules for a transition and resolve by specificity."""
    result = await db.execute(
        select(StatusTransitionRule).where(
            StatusTransitionRule.action_key == action_key,
            StatusTransitionRule.from_status == from_status,
            (
                (StatusTransitionRule.organization_id == org_id)
                | (StatusTransitionRule.organization_id.is_(None))
            ),
            (
                (StatusTransitionRule.agreement_type_id == agreement_type_id)
                | (StatusTransitionRule.agreement_type_id.is_(None))
            ),
        )
    )
    return _resolve_rule(list(result.scalars().all()), org_id, agreement_type_id)


def _conditions_met(
    rule: StatusTransitionRule,
    agreement: Agreement,
) -> bool:
    """Evaluate an optional rule.conditions predicate against agreement."""
    conditions = rule.allowed_conditions or {}
    if not conditions:
        return True

    if conditions.get("status_must_be_executed"):
        if agreement.status not in ("executed", "active"):
            return False
    if conditions.get("status_must_be_active"):
        if agreement.status != "active":
            return False
    if conditions.get("no_parent") and agreement.parent_agreement_id is not None:
        return False
    if conditions.get("must_have_child"):
        if not getattr(agreement, "child_agreements", None):
            return False
    return True


async def _record_audit(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agreement_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    actor_type: str,
    action: str,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    metadata_json: dict | None = None,
    ip_address: str | None = None,
) -> AuditEvent:
    return await record_event(
        db,
        tenant_id=tenant_id,
        agreement_id=agreement_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=metadata_json or {},
        ip_address=ip_address,
    )


def _candidate_transitions(
    db_rules: list[StatusTransitionRule],
    org_id: uuid.UUID | None,
    agreement_type_id: uuid.UUID | None,
) -> list[str]:
    """Return a union of action keys currently available for an agreement."""
    keys: list[str] = []
    for _ in range(len(db_rules)):
        rule = _resolve_rule(db_rules, org_id, agreement_type_id)
        if rule is None:
            break
        if rule.action_key not in keys:
            keys.append(rule.action_key)
        db_rules = [r for r in db_rules if r is not rule]
    return keys


async def get_available_actions(
    db: AsyncSession,
    agreement: Agreement,
    org_id: uuid.UUID | None = None,
) -> list[dict]:
    """List the transitions currently allowed from the agreement's status.

    Returns a list of {action_key, to_status, permission, description}.
    """
    result = await db.execute(
        select(StatusTransitionRule).where(
            StatusTransitionRule.from_status == agreement.status,
            (
                (StatusTransitionRule.organization_id == org_id)
                | (StatusTransitionRule.organization_id.is_(None))
            ),
            (
                (StatusTransitionRule.agreement_type_id == agreement.agreement_type_id)
                | (StatusTransitionRule.agreement_type_id.is_(None))
            ),
        )
    )
    rules = list(result.scalars().all())

    actions: list[dict] = []
    for r in rules:
        if r.action_key in [a["action_key"] for a in actions]:
            continue
        actions.append(
            {
                "action_key": r.action_key,
                "to_status": r.to_status,
                "required_permission": r.required_permission,
                "description": r.description,
                "needs_conditions": bool((r.allowed_conditions or {}) and not _conditions_met(r, agreement)),
            }
        )
    return actions


async def apply_transition(
    db: AsyncSession,
    *,
    agreement: Agreement,
    action_key: str,
    actor_id: uuid.UUID,
    org_id: uuid.UUID,
    actor_type: str = "user",
    ip_address: str | None = None,
    metadata_json: dict | None = None,
) -> Agreement:
    """Validate a requested action and, if permitted, mutate agreement.status.

    Raises TransitionNotAllowed if the action is not permitted from the
    agreement's current status (or required conditions are unmet).
    """
    rule = await _load_rules(
        db, action_key, agreement.status, org_id, agreement.agreement_type_id
    )
    if rule is None:
        raise TransitionNotAllowed(
            f"Transition '{action_key}' is not allowed from status "
            f"'{agreement.status}'"
        )

    if not _conditions_met(rule, agreement):
        raise TransitionNotAllowed(
            f"Transition '{action_key}' requires conditions that are not met"
        )

    # ------------------------------------------------------------------ #
    # QUALITY GATE (MVP gap fix)
    # ------------------------------------------------------------------ #
    QUALITY_GATED_ACTIONS = {"send", "submit_for_review", "submit_for_approval"}
    if rule.action_key in QUALITY_GATED_ACTIONS:
        from app.services.contract_quality import run_quality_checks
        
        # We need the full document text. For MVP, we'll try to extract it from the latest version or data.
        # Ideally, we should use the rendered text. We'll stringify the data dictionary for now if needed.
        # However, the best source is `agreement.versions[-1].content` if loaded.
        import json
        doc_text = json.dumps(agreement.data)
        
        meta = {
            "effective_date": agreement.effective_date,
            "expiry_date": agreement.expiry_date,
            "execution_date": agreement.execution_date,
            # We would extract parties here if they were eagerly loaded
            # "parties": [{"name": p.party_name} for p in agreement.parties] 
        }
        
        report = run_quality_checks(
            document_text=doc_text,
            agreement_meta=meta,
        )
        if report.get("has_blockers"):
            blocker_count = sum(1 for f in report.get("findings", []) if f.get("severity") == "high")
            raise TransitionNotAllowed(f"Quality check blocked: {blocker_count} issue(s).")

    previous_status = agreement.status
    agreement.status = rule.to_status
    await db.flush()

    await _record_audit(
        db,
        tenant_id=org_id,
        agreement_id=agreement.id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=f"STATUS_{rule.action_key.upper()}",
        resource_type="agreement",
        resource_id=agreement.id,
        metadata_json={
            "from_status": previous_status,
            "to_status": rule.to_status,
            **(metadata_json or {}),
        },
        ip_address=ip_address,
    )
    await db.flush()

    return agreement


# ==========================================================================
# Current-terms consolidation (2.07.26)
# ==========================================================================

async def get_current_terms(
    db: AsyncSession,
    agreement: Agreement,
) -> dict:
    """Return the consolidated current terms of an agreement.

    Terms = the base agreement's clause text with each active amendment's
    changes applied in amendment order. The latest activated amendment also
    carries a persisted snapshot for immutability.
    """
    result = await db.execute(
        select(AgreementAmendment)
        .where(
            AgreementAmendment.agreement_id == agreement.id,
            AgreementAmendment.status == "active",
        )
        .order_by(AgreementAmendment.amendment_number)
    )
    active = list(result.scalars().all())

    if not active:
        return {
            "agreement_id": str(agreement.id),
            "base_terms": agreement.data or {},
            "amendments_applied": [],
            "consolidated_terms": agreement.data or {},
        }

    latest = active[-1]
    if latest.current_terms_snapshot is not None:
        return {
            "agreement_id": str(agreement.id),
            "base_terms": agreement.data or {},
            "amendments_applied": [
                {"amendment_id": str(a.id), "amendment_number": a.amendment_number}
                for a in active
            ],
            "consolidated_terms": latest.current_terms_snapshot,
        }

    # Recompute from scratch if no snapshot present.
    consolidated = dict(agreement.data or {})
    for am in active:
        for change in sorted(am.changes, key=lambda c: c.section_key):
            _apply_change(consolidated, change)
    return {
        "agreement_id": str(agreement.id),
        "base_terms": agreement.data or {},
        "amendments_applied": [
            {"amendment_id": str(a.id), "amendment_number": a.amendment_number}
            for a in active
        ],
        "consolidated_terms": consolidated,
    }


def _apply_change(terms: dict, change):
    """Apply a clause change to a terms dict.

    Supports nested section_keys of the form 'a.b.c'. For replaces, sets
    the leaf to change.new_text; for deletes, removes it; for inserts,
    sets it too but keeps old value in an '_added' marker.
    """
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