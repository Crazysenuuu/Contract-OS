"""Termination service.

Implements the termination lifecycle end to end: initiation on a stated
legal ground, service of notice with delivery evidence, cure periods for
remediable breaches, verification that outstanding obligations are
addressed, and capture of post-termination obligations that survive.
State transitions of the agreement itself run through the lifecycle
service so they are rule-checked.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.obligation import Obligation
from app.models.termination import (
    AgreementTermination,
    PostTerminationObligation,
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