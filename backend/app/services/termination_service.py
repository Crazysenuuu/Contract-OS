"""Termination service.

Implements the termination lifecycle end to end: initiation on a stated
legal ground, service of notice with delivery evidence, cure periods for
remediable breaches, verification that outstanding obligations are
addressed, and capture of post-termination obligations that survive.
State transitions of the agreement itself run through the lifecycle
service so they are rule-checked.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.obligation import Obligation
from app.models.termination import (
    AgreementTermination,
    PostTerminationObligation,
    TerminationSettlement,
    TerminationSettlementItem,
)
from app.services.lifecycle_service import apply_transition


class TerminationError(Exception):
    """Raised when a termination step is not valid."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# Post-termination obligations that commonly survive an agreement, per
# section key. Returned to the caller to populate PostTerminationObligation.
DEFAULT_SURVIVING_OBLIGATIONS = [
    {
        "obligation_type": "return_materials",
        "description": "Return or destroy all Confidential Information and copies in accordance with the confidentiality provisions",
        "due_in_days": 30,
        "party": "receiving_party",
    },
    {
        "obligation_type": "pay_accrued",
        "description": "Pay all amounts accrued and unpaid as of the effective date of termination",
        "due_in_days": 30,
        "party": "client",
    },
    {
        "obligation_type": "return_property",
        "description": "Return all property, equipment, and materials belonging to the other party",
        "due_in_days": 30,
        "party": "both",
    },
    {
        "obligation_type": "transition_assistance",
        "description": "Provide reasonable transition assistance and ongoing cooperation for a period following termination",
        "due_in_days": 60,
        "party": "provider",
    },
]


async def initiate_termination(
    db: AsyncSession,
    *,
    agreement: Agreement,
    initiated_by: uuid.UUID,
    reason_code: str,
    reason_detail: str | None,
    notice_period_days: int | None = None,
    cure_required: bool = False,
    cure_period_days: int | None = None,
    org_id: uuid.UUID,
) -> AgreementTermination:
    """Open a termination proceeding on an executed/active agreement."""
    if agreement.status not in ("executed", "active", "expired"):
        raise TerminationError(
            f"Agreement in status '{agreement.status}' cannot be terminated"
        )

    # Verify outstanding obligations so onset is grounded in the record.
    obligations = await _outstanding_obligations(db, agreement.id)

    termination = AgreementTermination(
        agreement_id=agreement.id,
        initiated_by=initiated_by,
        initiated_at=now_utc(),
        reason_code=reason_code,
        reason_detail=reason_detail,
        notice_period_days=notice_period_days,
        cure_required=cure_required,
        cure_period_days=cure_period_days,
        status="draft",
        obligations_check={
            "outstanding": [
                {
                    "obligation_id": str(o.id),
                    "description": o.description,
                    "owner_party": o.owner_party,
                    "status": o.status,
                    "due_date": str(o.due_date) if o.due_date else None,
                }
                for o in obligations
            ],
            "resolved": len(obligations) == 0,
        },
    )
    db.add(termination)
    await db.flush()

    from app.services.lifecycle_service import _record_audit

    await _record_audit(
        db,
        tenant_id=org_id,
        agreement_id=agreement.id,
        actor_id=initiated_by,
        actor_type="user",
        action="TERMINATION_INITIATED",
        resource_type="agreement_termination",
        resource_id=termination.id,
        metadata_json={
            "reason_code": reason_code,
            "cure_required": cure_required,
        },
    )

    # Spec 1.17 §15: requesting termination generates the settlement
    # checklist (required-by-agreement vs recommended operational items).
    await generate_settlement(
        db,
        termination=termination,
        agreement=agreement,
    )
    await db.flush()
    return termination


async def issue_notice(
    db: AsyncSession,
    *,
    termination: AgreementTermination,
    notice_date: date | None = None,
    evidence: dict | None = None,
    actor_id: uuid.UUID,
    org_id: uuid.UUID,
) -> AgreementTermination:
    """Serve termination notice and record delivery evidence."""
    if termination.status not in ("draft", "notice_served"):
        raise TerminationError(
            f"Notice cannot be issued from status '{termination.status}'"
        )

    termination.notice_date = notice_date or date.today()
    termination.notice_served = True
    termination.notice_evidence = evidence or {}
    termination.status = "notice_served"

    if termination.cure_required:
        termination.status = "in_cure"
        if termination.cure_period_days:
            termination.cure_deadline = (
                termination.notice_date
                + timedelta(days=termination.cure_period_days)
            )
    await db.flush()

    from app.services.lifecycle_service import _record_audit

    await _record_audit(
        db,
        tenant_id=org_id,
        agreement_id=termination.agreement_id,
        actor_id=actor_id,
        actor_type="user",
        action="TERMINATION_NOTICE_SERVED",
        resource_type="agreement_termination",
        resource_id=termination.id,
        metadata_json={
            "notice_date": str(termination.notice_date),
            "cure_deadline": str(termination.cure_deadline) if termination.cure_deadline else None,
        },
    )
    await db.flush()
    return termination


async def record_cure(
    db: AsyncSession,
    *,
    termination: AgreementTermination,
    actor_id: uuid.UUID,
    org_id: uuid.UUID,
    cured: bool = True,
) -> AgreementTermination:
    """Record whether the breach underlying the termination was cured."""
    if termination.status != "in_cure":
        raise TerminationError(
            f"Cure can only be recorded while in cure (status '{termination.status}')"
        )

    termination.cured = cured
    termination.status = "pending_effect" if not cured else "notice_served"
    await db.flush()

    from app.services.lifecycle_service import _record_audit

    await _record_audit(
        db,
        tenant_id=org_id,
        agreement_id=termination.agreement_id,
        actor_id=actor_id,
        actor_type="user",
        action="TERMINATION_CURE_RECORDED",
        resource_type="agreement_termination",
        resource_id=termination.id,
        metadata_json={"cured": cured},
    )
    await db.flush()
    return termination


async def complete_termination(
    db: AsyncSession,
    *,
    termination: AgreementTermination,
    agreement: Agreement,
    completed_by: uuid.UUID,
    org_id: uuid.UUID,
    effective_date: date | None = None,
    force: bool = False,
) -> AgreementTermination:
    """Effect the termination on the agreement.

    Refuses to terminate while obligations remain outstanding unless
    explicitly waived by setting force=True.
    """
    if termination.status in ("effective", "cancelled"):
        raise TerminationError(
            f"Termination is already '{termination.status}'"
        )

    outstanding = await _outstanding_obligations(db, agreement.id)
    if outstanding and not force:
        raise TerminationError(
            "Termination cannot be completed while obligations remain "
            f"outstanding ({len(outstanding)} found)"
        )

    # Spec 1.17 §15: the settlement checklist gates completion. Required
    # items (open obligations) must be resolved or explicitly waived on the
    # checklist; force=True waives them implicitly but records that fact.
    settlement = await generate_settlement(
        db,
        termination=termination,
        agreement=agreement,
        replace_existing=True,
    )
    if settlement.obligations_remaining > 0 and not force:
        raise TerminationError(
            "Termination cannot be completed while settlement items remain "
            f"open ({settlement.obligations_remaining} found)"
        )
    if force and settlement.obligations_remaining > 0:
        settlement.status = "waived"
        settlement.notes = (
            (settlement.notes + " | " if settlement.notes else "")
            + f"Completion forced with {settlement.obligations_remaining} "
            "settlement item(s) open"
        )
        await db.flush()

    termination.effective_date = effective_date or date.today()
    termination.status = "effective"
    termination.completed_by = completed_by
    termination.completed_at = now_utc()
    termination.obligations_check["resolved"] = True
    await db.flush()

    # Rule-checked transition of the agreement itself.
    await apply_transition(
        db,
        agreement=agreement,
        action_key="terminate",
        actor_id=completed_by,
        org_id=org_id,
        actor_type="user",
        metadata_json={"termination_id": str(termination.id)},
    )

    # Populate surviving obligations.
    for ob in DEFAULT_SURVIVING_OBLIGATIONS:
        db.add(
            PostTerminationObligation(
                termination_id=termination.id,
                owner_party=ob["party"],
                description=ob["description"],
                obligation_type=ob["obligation_type"],
                due_date=(
                    termination.effective_date
                    + timedelta(days=ob["due_in_days"])
                    if termination.effective_date
                    else None
                ),
                status="upcoming",
            )
        )
    await db.flush()

    from app.services.lifecycle_service import _record_audit

    await _record_audit(
        db,
        tenant_id=org_id,
        agreement_id=agreement.id,
        actor_id=completed_by,
        actor_type="user",
        action="TERMINATION_COMPLETED",
        resource_type="agreement_termination",
        resource_id=termination.id,
        metadata_json={"effective_date": str(termination.effective_date)},
    )
    await db.flush()
    return termination


async def cancel_termination(
    db: AsyncSession,
    *,
    termination: AgreementTermination,
    actor_id: uuid.UUID,
    org_id: uuid.UUID,
) -> AgreementTermination:
    if termination.status == "effective":
        raise TerminationError("An effective termination cannot be cancelled")
    termination.status = "cancelled"
    await db.flush()

    from app.services.lifecycle_service import _record_audit

    await _record_audit(
        db,
        tenant_id=org_id,
        agreement_id=termination.agreement_id,
        actor_id=actor_id,
        actor_type="user",
        action="TERMINATION_CANCELLED",
        resource_type="agreement_termination",
        resource_id=termination.id,
        metadata_json={},
    )
    await db.flush()
    return termination


async def _outstanding_obligations(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> list[Obligation]:
    result = await db.execute(
        select(Obligation).where(
            Obligation.agreement_id == agreement_id,
            Obligation.status.in_(["upcoming", "due", "overdue", "disputed"]),
        )
    )
    return list(result.scalars().all())


# ===========================================================================
# Termination settlement (spec 1.17 §15-16)
# ===========================================================================

# Clauses that commonly survive termination. These are REQUIRED BY THE
# AGREEMENT (kind='required') but they do not block completion: they remain
# binding after the agreement ends, which is the point of survival.
SURVIVING_CLAUSE_ITEMS = [
    {"section": "confidentiality", "description": "Confidentiality obligations survive termination"},
    {"section": "indemnification", "description": "Indemnities survive termination"},
    {"section": "limitation_of_liability", "description": "Limitations of liability survive termination"},
    {"section": "governing_law", "description": "Governing law and dispute resolution survive termination"},
    {"section": "intellectual_property", "description": "IP ownership and licenses granted survive termination"},
]

_MONEY_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _parse_amount_minor(text: str | None) -> int | None:
    """Parse a human obligation amount into integer minor units.

    Obligation amounts arrive as display strings ("12,500.75"). They are
    parsed with Decimal and converted to integer minor units (cents) — never
    through float (spec 1.17 §16: no floating-point for settlement sums).
    A trailing unit word (e.g. "k", "thousand") is not interpreted; the raw
    digits are taken at face value.
    """
    if not text:
        return None
    match = _MONEY_RE.search(text)
    if not match:
        return None
    try:
        value = Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None
    cents = value * Decimal("100")
    return int(cents.to_integral_value())


def _obligation_category(obligation_type: str) -> str:
    mapping = {
        "payment": "financial",
        "pay_accrued": "financial",
        "delivery": "deliverable",
        "reporting": "obligation",
        "compliance": "obligation",
        "notification": "obligation",
        "maintenance": "obligation",
        "insurance": "obligation",
        "return_materials": "property_return",
        "return_property": "property_return",
        "confidentiality": "confidentiality",
        "audit_rights": "obligation",
    }
    return mapping.get(obligation_type, "other")


async def _settlement_for(db: AsyncSession, termination_id: uuid.UUID) -> TerminationSettlement:
    result = await db.execute(
        select(TerminationSettlement).where(
            TerminationSettlement.termination_id == termination_id
        )
    )
    settlement = result.scalar_one_or_none()
    if settlement is None:
        raise TerminationError("No settlement exists for this termination")
    return settlement


async def generate_settlement(
    db: AsyncSession,
    *,
    termination: AgreementTermination,
    agreement: Agreement,
    actor_id: uuid.UUID | None = None,
    replace_existing: bool = False,
) -> TerminationSettlement:
    """Generate (or regenerate) the settlement checklist for a termination.

    Items come from three sources, each traceable:

    - open obligations  -> kind='required', blocks completion
    - surviving clauses -> kind='required', do NOT block (they survive)
    - manual defaults   -> kind='recommended' (access removal, final invoice)

    Contractual requirements always reference their source; recommended
    items are operational suggestions the parties may dismiss.
    """
    existing = await db.execute(
        select(TerminationSettlement).where(
            TerminationSettlement.termination_id == termination.id
        )
    )
    settlement = existing.scalar_one_or_none()
    if settlement is not None and not replace_existing:
        return settlement
    if settlement is None:
        settlement = TerminationSettlement(
            termination_id=termination.id,
            status="pending",
        )
        db.add(settlement)
        await db.flush()

    outstanding = await _outstanding_obligations(db, agreement.id)

    # --- Financial position (integer minor units; Decimal only) ---
    total_minor = 0
    currency: str | None = None
    for ob in outstanding:
        if ob.obligation_type not in ("payment", "pay_accrued"):
            continue
        minor = _parse_amount_minor(ob.amount)
        if minor is None:
            continue
        total_minor += minor
        currency = ob.currency or currency
    settlement.outstanding_amount_minor = total_minor or None
    settlement.currency = currency

    # --- Checklist ---
    open_statuses = {"open", "resolved", "waived"}
    previous: dict[str, TerminationSettlementItem] = {}
    if replace_existing:
        old_items = await db.execute(
            select(TerminationSettlementItem).where(
                TerminationSettlementItem.settlement_id == settlement.id
            )
        )
        for item in old_items.scalars().all():
            if item.status in open_statuses:
                previous[item.item_key] = item
            await db.delete(item)
        await db.flush()

    items: list[TerminationSettlementItem] = []
    position = 0

    def _upsert(key: str) -> TerminationSettlementItem:
        """Reuse a still-open item from the previous generation, else new."""
        reused = previous.pop(key, None)
        if _is_reusable(reused):
            return reused
        return TerminationSettlementItem(
            settlement_id=settlement.id,
            item_key=key,
        )

    # 1. Outstanding obligations: required by the agreement, blocking.
    for ob in outstanding:
        item = _upsert(f"obligation:{ob.id}")
        item.description = ob.description
        item.category = _obligation_category(ob.obligation_type)
        item.kind = "required"
        item.blocks_completion = True
        item.source_type = "obligation"
        item.source_id = str(ob.id)
        item.position = position
        position += 1
        items.append(item)

    # 2. Surviving clauses: required by the agreement but they survive
    #    termination — they are acknowledged, not discharged, before completion.
    for sc in SURVIVING_CLAUSE_ITEMS:
        item = _upsert(f"surviving:{sc['section']}")
        item.description = sc["description"]
        item.category = "confidentiality" if sc["section"] == "confidentiality" else "other"
        item.kind = "required"
        item.blocks_completion = False
        item.source_type = "surviving_clause"
        item.source_id = sc["section"]
        item.position = position
        position += 1
        items.append(item)

    # 3. Recommended operational actions (not required by the agreement).
    for rec in (
        {"key": "access_removal", "description": "Revoke the other party's system access and credentials", "category": "access"},
        {"key": "final_invoice", "description": "Issue the final invoice / credit note for accrued amounts", "category": "financial"},
    ):
        item = _upsert(f"recommended:{rec['key']}")
        item.description = rec["description"]
        item.category = rec["category"]
        item.kind = "recommended"
        item.blocks_completion = False
        item.source_type = "manual"
        item.source_id = rec["key"]
        item.position = position
        position += 1
        items.append(item)

    for item in items:
        db.add(item)

    _recount_blocking(settlement, items)
    await db.flush()
    return settlement


def _is_reusable(instance: TerminationSettlementItem | None) -> bool:
    """True when a previous-generation item can be re-added safely.

    Regeneration deletes old items, and re-adding a deleted ORM instance
    raises InvalidRequestError on flush — only open items loaded from the
    database before deletion remain reusable (they are never flushed as
    deleted until the next flush, so we snapshot the reusable set before
    issuing deletes and rely on instance identity here).
    """
    from sqlalchemy import inspect as sa_inspect

    if instance is None:
        return False
    state = sa_inspect(instance)
    return not (state.deleted or state._deleted or state.was_deleted)


async def _load_settlement_items(
    db: AsyncSession,
    settlement_id: uuid.UUID,
) -> list[TerminationSettlementItem]:
    result = await db.execute(
        select(TerminationSettlementItem)
        .where(TerminationSettlementItem.settlement_id == settlement_id)
        .order_by(TerminationSettlementItem.position)
    )
    return list(result.scalars().all())


def _recount_blocking(
    settlement: TerminationSettlement,
    items: list[TerminationSettlementItem],
) -> None:
    """Refresh the denormalized blocking count and status.

    Items must be passed in explicitly (async sessions cannot lazy-load)."""
    remaining = sum(
        1 for i in items if i.blocks_completion and i.status == "open"
    )
    settlement.obligations_remaining = remaining
    if settlement.status in ("pending", "in_progress"):
        # Any open blocking work means the settlement is in progress; a
        # checklist with nothing left to do stays/becomes 'pending' (ready
        # to be marked settled). 'settled'/'waived' are terminal here.
        settlement.status = "in_progress" if remaining > 0 else "pending"


async def update_settlement_item(
    db: AsyncSession,
    *,
    item: TerminationSettlementItem,
    actor_id: uuid.UUID,
    status: str,
    resolution_note: str | None = None,
) -> TerminationSettlementItem:
    """Resolve, re-open or waive a single checklist item."""
    if status not in ("open", "resolved", "waived"):
        raise TerminationError(
            f"Invalid item status '{status}'"
        )
    item.status = status
    item.resolution_note = resolution_note
    if status in ("resolved", "waived"):
        item.resolved_by = actor_id
        item.resolved_at = now_utc()
    else:
        item.resolved_by = None
        item.resolved_at = None

    await db.flush()

    # Explicit loads: async sessions cannot lazy-load relationships.
    settlement_result = await db.execute(
        select(TerminationSettlement).where(
            TerminationSettlement.id == item.settlement_id
        )
    )
    settlement = settlement_result.scalar_one()

    # Keep the source of truth in sync: resolving (not waiving) a checklist
    # item that traces to an obligation discharges that obligation, so the
    # raw-obligations gate and the checklist gate cannot diverge.
    if item.source_type == "obligation" and item.source_id:
        try:
            source = await db.get(Obligation, uuid.UUID(item.source_id))
        except ValueError:
            source = None
        if source is not None and source.status not in (
            "completed",
            "COMPLETED",
            "waived",
            "WAIVED",
        ):
            from app.services.obligation_lifecycle import (
                COMPLETED as OB_COMPLETED,
                add_event as ob_add_event,
                normalise_status,
            )

            previous_status = normalise_status(source.status) or "OPEN"
            source.status = (
                OB_COMPLETED if status == "resolved" else "WAIVED"
            )
            await ob_add_event(
                db,
                source,
                "COMPLETED" if status == "resolved" else "WAIVED",
                actor_id,
                previous_status,
                source.status,
                settlement_item_id=str(item.id),
            )

    items = await _load_settlement_items(db, settlement.id)
    _recount_blocking(settlement, items)

    # Keep the parent termination's obligations_check in sync so the
    # termination record reflects the settlement state.
    termination_result = await db.execute(
        select(AgreementTermination).where(
            AgreementTermination.id == settlement.termination_id
        )
    )
    termination = termination_result.scalar_one_or_none()
    if termination is not None and termination.obligations_check is not None:
        termination.obligations_check = {
            **termination.obligations_check,
            "settlement_items_remaining": settlement.obligations_remaining,
            "resolved": settlement.obligations_remaining == 0,
        }
    await db.flush()
    return item


async def settle_termination(
    db: AsyncSession,
    *,
    termination: AgreementTermination,
    actor_id: uuid.UUID,
    org_id: uuid.UUID,
    notes: str | None = None,
) -> TerminationSettlement:
    """Mark the settlement settled once every blocking item is closed."""
    settlement = await _settlement_for(db, termination.id)
    if settlement.status == "settled":
        raise TerminationError("Settlement is already settled")

    items = await _load_settlement_items(db, settlement.id)
    _recount_blocking(settlement, items)
    if settlement.obligations_remaining > 0:
        raise TerminationError(
            "Settlement cannot be marked settled while "
            f"{settlement.obligations_remaining} required item(s) remain open"
        )

    settlement.status = "settled"
    settlement.notes = notes or settlement.notes
    settlement.settled_by = actor_id
    settlement.settled_at = now_utc()
    await db.flush()

    from app.services.lifecycle_service import _record_audit

    await _record_audit(
        db,
        tenant_id=org_id,
        agreement_id=termination.agreement_id,
        actor_id=actor_id,
        actor_type="user",
        action="TERMINATION_SETTLEMENT_SETTLED",
        resource_type="termination_settlement",
        resource_id=settlement.id,
        metadata_json={
            "termination_id": str(termination.id),
            "items": len(items),
        },
    )
    await db.flush()
    return settlement