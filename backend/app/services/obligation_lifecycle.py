"""Obligation lifecycle service (spec 2.08).

Implements the obligation state machine, deadline evaluation, assignee
validation, evidence submission/review and event history. Status strings use
the spec uppercase names; legacy lowercase statuses are normalised on read.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.obligation import (
    Obligation,
    ObligationAssignee,
    ObligationDeadline,
    ObligationEvent,
    ObligationEvidence,
)

# --- Status constants (spec 2.08.3 / 2.08.2) ---

CANDIDATE = "CANDIDATE"
CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
CONFIRMED = "CONFIRMED"
ASSIGNED = "ASSIGNED"
OPEN = "OPEN"
IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"
OVERDUE = "OVERDUE"
WAIVED = "WAIVED"
CANCELLED = "CANCELLED"
SUPERSEDED = "SUPERSEDED"

# --- Evidence status (spec 2.08.3) ---

EVIDENCE_NOT_REQUIRED = "NOT_REQUIRED"
EVIDENCE_REQUIRED = "REQUIRED"
EVIDENCE_SUBMITTED = "SUBMITTED"
EVIDENCE_UNDER_REVIEW = "UNDER_REVIEW"
EVIDENCE_VERIFIED = "VERIFIED"
EVIDENCE_REJECTED = "REJECTED"

# Spec 2.08.29 — required transitions.
ALLOWED_TRANSITIONS = {
    CANDIDATE: {CONFIRMATION_REQUIRED},
    CONFIRMATION_REQUIRED: {CONFIRMED, CANCELLED},
    CONFIRMED: {ASSIGNED},
    ASSIGNED: {OPEN},
    OPEN: {IN_PROGRESS, COMPLETED, WAIVED, CANCELLED},
    IN_PROGRESS: {COMPLETED, WAIVED, CANCELLED},
    OVERDUE: {IN_PROGRESS, COMPLETED, WAIVED},
    COMPLETED: set(),
    WAIVED: set(),
    CANCELLED: set(),
    SUPERSEDED: set(),
}

# Legacy lowercase states are mapped onto the spec state machine so older
# rows keep working (spec 2.08.2 normalisation).
_LEGACY_NORMALISATION = {
    "upcoming": OPEN,
    "due": ASSIGNED,
    "overdue": OVERDUE,
    "completed": COMPLETED,
    "waived": WAIVED,
    "disputed": OPEN,
    "cancelled": CANCELLED,
}


def normalise_status(status: str | None) -> str | None:
    if status is None:
        return None
    return _LEGACY_NORMALISATION.get(status, status)


def ensure_valid_transition(current: str | None, target: str):
    current = normalise_status(current) or OPEN
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(f"Invalid transition {current} -> {target}")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def add_event(
    db: AsyncSession,
    obligation: Obligation,
    event_type: str,
    actor_id: UUID | None,
    previous_status: str | None = None,
    new_status: str | None = None,
    **metadata,
):
    event = ObligationEvent(
        obligation_id=obligation.id,
        event_type=event_type,
        actor_id=actor_id,
        previous_status=previous_status,
        new_status=new_status,
        metadata_json=metadata,
        created_at=now_utc(),
    )
    db.add(event)
    return event


async def get_current_deadline(
    db: AsyncSession, obligation: Obligation
) -> ObligationDeadline | None:
    """Earliest OPEN deadline; falls back to the DOJ due_date."""
    if obligation.due_date is not None:
        implied = ObligationDeadline(
            obligation_id=obligation.id,
            deadline_type="due_date",
            due_at=datetime.combine(obligation.due_date, datetime.min.time(), tzinfo=timezone.utc),
            status="OPEN",
        )
        return implied
    result = await db.execute(
        select(ObligationDeadline)
        .where(
            ObligationDeadline.obligation_id == obligation.id,
            ObligationDeadline.status == "OPEN",
        )
        .order_by(ObligationDeadline.due_at.asc())
    )
    return result.scalars().first()


def _aware(dt: datetime) -> datetime:
    """Normalise naive datetimes (SQLite round-trip) to aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def evaluate_overdue(db: AsyncSession, obligation: Obligation) -> bool:
    """Mark OPEN / IN_PROGRESS obligations OVERDUE when past due (2.08.22)."""
    status = normalise_status(obligation.status)
    if status not in (OPEN, IN_PROGRESS):
        return False
    deadline = await get_current_deadline(db, obligation)
    if deadline is None or deadline.due_at is None:
        return False
    if _aware(deadline.due_at) < now_utc():
        previous = obligation.status
        obligation.status = OVERDUE
        await add_event(
            db, obligation, "STATUS_CHANGED", None, previous, OVERDUE,
            rule="OVERDUE_DETECTION",
        )
        return True
    return False


async def confirm_obligation(db: AsyncSession, obligation: Obligation, actor_id: UUID):
    previous = normalise_status(obligation.status)
    if previous != CONFIRMATION_REQUIRED:
        raise ValueError("Obligation is not awaiting confirmation.")
    ensure_valid_transition(previous, CONFIRMED)
    obligation.status = CONFIRMED
    await add_event(
        db, obligation, "CONFIRMED", actor_id, previous, CONFIRMED,
    )


async def assign_obligation(
    db: AsyncSession,
    obligation: Obligation,
    actor_id: UUID,
    agreement_party_id: UUID | None,
    member_id: UUID | None,
    responsibility_type: str,
    primary_assignee: bool = False,
) -> ObligationAssignee:
    """Add an assignee and open the obligation (spec 2.08.16)."""
    previous = normalise_status(obligation.status)
    if previous in (CONFIRMED, ASSIGNED):
        ensure_valid_transition(previous, ASSIGNED)
        obligation.status = ASSIGNED
    elif previous not in (OPEN, IN_PROGRESS, OVERDUE):
        ensure_valid_transition(previous, ASSIGNED)

    assignee = ObligationAssignee(
        obligation_id=obligation.id,
        agreement_party_id=agreement_party_id,
        member_id=member_id,
        responsibility_type=responsibility_type,
        primary_assignee=primary_assignee,
    )
    db.add(assignee)
    if primary_assignee:
        # Flush first so ``assignee.id`` is materialised: the WHERE clause
        # below must exclude the new row itself. Built before the flush, the
        # bound ``id != None`` parameter compiles to ``id IS NOT NULL`` and
        # the demote-UPDATE silently clears the very assignee we just added
        # (deterministic under SQLAlchemy >= 2.1, where the pending INSERT
        # autoflushes ahead of the Core UPDATE).
        await db.flush()
        await db.execute(
            ObligationAssignee.__table__.update()
            .where(
                ObligationAssignee.__table__.c.obligation_id == obligation.id,
                ObligationAssignee.__table__.c.id != assignee.id,
            )
            .values(primary_assignee=False)
        )
    await add_event(
        db, obligation, "ASSIGNED", actor_id, previous, obligation.status,
        responsibility_type=responsibility_type,
    )
    return assignee


async def start_obligation(db: AsyncSession, obligation: Obligation, actor_id: UUID):
    previous = normalise_status(obligation.status)
    ensure_valid_transition(previous, IN_PROGRESS)
    obligation.status = IN_PROGRESS
    await add_event(
        db, obligation, "STARTED", actor_id, previous, IN_PROGRESS,
    )


async def complete_obligation(db: AsyncSession, obligation: Obligation, actor_id: UUID):
    previous = normalise_status(obligation.status)
    ensure_valid_transition(previous, COMPLETED)
    evidence_status = obligation.evidence_status or EVIDENCE_NOT_REQUIRED
    if evidence_status == EVIDENCE_REQUIRED:
        verified = (
            await db.execute(
                select(ObligationEvidence.id).where(
                    ObligationEvidence.obligation_id == obligation.id,
                    ObligationEvidence.status == EVIDENCE_VERIFIED,
                )
            )
        ).scalars().first()
        if verified is None:
            raise ValueError("Required evidence has not been verified.")
    obligation.status = COMPLETED
    await add_event(
        db, obligation, "COMPLETED", actor_id, previous, COMPLETED,
    )


async def waive_obligation(db: AsyncSession, obligation: Obligation, actor_id: UUID):
    previous = normalise_status(obligation.status)
    ensure_valid_transition(previous, WAIVED)
    obligation.status = WAIVED
    await add_event(
        db, obligation, "WAIVED", actor_id, previous, WAIVED,
    )


async def cancel_obligation(db: AsyncSession, obligation: Obligation, actor_id: UUID):
    previous = normalise_status(obligation.status)
    ensure_valid_transition(previous, CANCELLED)
    obligation.status = CANCELLED
    await add_event(
        db, obligation, "CANCELLED", actor_id, previous, CANCELLED,
    )


async def supersede_obligation(db: AsyncSession, obligation: Obligation, actor_id: UUID | None):
    previous = obligation.status
    obligation.status = SUPERSEDED
    await add_event(
        db, obligation, "SUPERSEDED", actor_id, previous, SUPERSEDED,
    )


# --- Deadlines (spec 2.08.7 / 2.08.8) ---

def calculate_absolute_deadline(date_value: datetime) -> datetime:
    return date_value


def calculate_relative_deadline(
    source_date: datetime,
    amount: int,
    unit: str,
    calendar: str = "CALENDAR_DAYS",
    holidays: set[date] | None = None,
) -> datetime:
    """Compute a relative deadline.

    ``calendar`` is either CALENDAR_DAYS (count every day) or BUSINESS_DAYS
    (skip weekends and the caller-supplied holidays — the working calendar of
    spec 2.08.9. When no holidays are supplied the default is weekends only).
    """
    if amount < 0:
        raise ValueError("Amount must be non-negative.")
    if calendar == "BUSINESS_DAYS":
        from app.services.business_calendar import BusinessCalendar

        cal = BusinessCalendar(holidays=holidays or set())
        result = cal.add_business_days(source_date.date(), amount)
        return datetime.combine(result, source_date.timetz() or datetime.min.time(), tzinfo=source_date.tzinfo)
    if calendar != "CALENDAR_DAYS":
        raise ValueError(f"Unsupported calendar: {calendar}")

    if unit == "DAYS":
        return source_date + timedelta(days=amount)
    if unit == "HOURS":
        return source_date + timedelta(hours=amount)
    if unit == "WEEKS":
        return source_date + timedelta(weeks=amount)
    if unit == "MONTHS":
        month = source_date.month - 1 + amount
        year = source_date.year + month // 12
        month = month % 12 + 1
        day = min(source_date.day, _days_in_month(year, month))
        return source_date.replace(year=year, month=month, day=day)
    raise ValueError(f"Unsupported time unit: {unit}")


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    return (next_month - datetime(year, month, 1)).days


def apply_deadline_rule(
    rule: dict | None,
    source_date: datetime | None,
    holidays: set[date] | None = None,
) -> datetime | None:
    """Derive a concrete deadline from a structural rule (spec 2.08.7).

    Absolute rule: {"type": "ABSOLUTE", "at": ...}
    Relative rule: {"type": "RELATIVE", "amount", "unit", "calendar",
                    "reference": ...}
    """
    if not rule:
        return None
    rule_type = rule.get("type")
    if rule_type == "ABSOLUTE":
        at = rule.get("at")
        if at is None:
            return None
        if isinstance(at, datetime):
            return at
        if isinstance(at, date):
            return datetime.combine(at, datetime.min.time(), tzinfo=timezone.utc)
        return datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    if rule_type == "RELATIVE":
        if source_date is None:
            return None
        return calculate_relative_deadline(
            source_date=source_date,
            amount=int(rule["amount"]),
            unit=rule.get("unit", "DAYS"),
            calendar=rule.get("calendar", "CALENDAR_DAYS"),
            holidays=holidays,
        )
    raise ValueError(f"Unsupported deadline rule type: {rule_type}")


async def create_deadline(
    db: AsyncSession,
    obligation: Obligation,
    deadline_type: str,
    *,
    due_at: datetime | None = None,
    calculation_rule: dict | None = None,
    source_date: datetime | None = None,
    source_event: str | None = None,
    timezone_name: str | None = None,
) -> ObligationDeadline:
    """Create a deadline, deriving due_at from the calculation rule if needed."""
    if due_at is None:
        due_at = apply_deadline_rule(calculation_rule, source_date)
    deadline = ObligationDeadline(
        obligation_id=obligation.id,
        deadline_type=deadline_type,
        due_at=due_at,
        calculation_rule=calculation_rule,
        source_date=source_date,
        source_event=source_event,
        timezone=timezone_name,
        status="OPEN",
    )
    db.add(deadline)
    if obligation.status in (CONFIRMATION_REQUIRED, CONFIRMED, ASSIGNED):
        if normalise_status(obligation.status) == CONFIRMED:
            ensure_valid_transition(CONFIRMED, ASSIGNED)
            obligation.status = ASSIGNED
        if normalise_status(obligation.status) == ASSIGNED:
            ensure_valid_transition(ASSIGNED, OPEN)
            obligation.status = OPEN
    await add_event(
        db, obligation, "DEADLINE_SET", None,
        metadata={"deadline_type": deadline_type},
    )
    return deadline


# --- Evidence (spec 2.08.17 / 2.08.18 / 2.08.30) ---

async def submit_evidence(
    db: AsyncSession,
    obligation: Obligation,
    submitted_by: UUID,
    document_id: UUID | None,
    description: str | None,
) -> ObligationEvidence:
    if obligation.status == COMPLETED:
        raise ValueError("Obligation is already completed.")
    evidence = ObligationEvidence(
        obligation_id=obligation.id,
        document_id=document_id,
        description=description,
        submitted_by=submitted_by,
        status=EVIDENCE_SUBMITTED,
        submitted_at=now_utc(),
    )
    db.add(evidence)
    obligation.evidence_status = EVIDENCE_SUBMITTED
    await add_event(
        db, obligation, "EVIDENCE_SUBMITTED", submitted_by,
        metadata={"evidence_id": str(evidence.id)},
    )
    return evidence


async def review_evidence(
    db: AsyncSession,
    evidence: ObligationEvidence,
    obligation: Obligation,
    reviewer_id: UUID,
    *,
    approve: bool,
    comment: str | None = None,
    document_ok: bool = True,
):
    """Review evidence; on approval marks obligation verified (spec 2.08.18)."""
    if evidence.status not in (EVIDENCE_SUBMITTED, EVIDENCE_UNDER_REVIEW):
        raise ValueError("Evidence is not awaiting review.")
    if not approve and not document_ok:
        evidence.status = EVIDENCE_REJECTED
        evidence.reviewed_by = reviewer_id
        evidence.reviewed_at = now_utc()
        evidence.review_comment = comment or "Evidence document integrity check failed."
        await add_event(
            db, obligation, "EVIDENCE_REJECTED", reviewer_id,
            metadata={"evidence_id": str(evidence.id)},
        )
        return evidence
    if approve:
        evidence.status = EVIDENCE_VERIFIED
        evidence.reviewed_by = reviewer_id
        evidence.reviewed_at = now_utc()
        evidence.review_comment = comment
        obligation.evidence_status = EVIDENCE_VERIFIED
        await add_event(
            db, obligation, "EVIDENCE_VERIFIED", reviewer_id,
            metadata={"evidence_id": str(evidence.id)},
        )
    return evidence