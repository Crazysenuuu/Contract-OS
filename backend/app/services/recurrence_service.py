"""Recurring obligation materialisation (spec 2.08 recurring duties).

An ObligationRecurrence records a repeating duty; every occurrence is
materialised as a real ObligationDeadline so scheduling, reminders and
compliance views work uniformly on concrete due dates. The scheduler
advances ``next_run_at`` each time until ``max_occurrences`` is reached.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.obligation import (
    Obligation,
    ObligationDeadline,
    ObligationRecurrence,
)

VALID_FREQUENCIES = ("DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY")


def _month_delta(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def next_occurrence(current: date, frequency: str, interval: int) -> date:
    """Compute the next occurrence after ``current`` for a frequency."""
    if frequency == "DAILY":
        return current + timedelta(days=interval)
    if frequency == "WEEKLY":
        return current + timedelta(weeks=interval)
    if frequency == "MONTHLY":
        return _month_delta(current, interval)
    if frequency == "QUARTERLY":
        return _month_delta(current, interval * 3)
    if frequency == "YEARLY":
        return _month_delta(current, interval * 12)
    raise ValueError(f"Unsupported recurrence frequency: {frequency}")


async def register_recurrence(
    db: AsyncSession,
    *,
    obligation: Obligation,
    frequency: str,
    interval: int = 1,
    anchor_date: date | None = None,
    max_occurrences: int | None = None,
) -> ObligationRecurrence:
    """Attach a recurrence schedule to an obligation and materialise the
    first concrete deadline if one is not already present."""
    if frequency not in VALID_FREQUENCIES:
        raise ValueError(f"Unsupported recurrence frequency: {frequency}")

    anchor = anchor_date or date.today()
    recurrence = ObligationRecurrence(
        obligation_id=obligation.id,
        frequency=frequency,
        interval=interval,
        anchor_date=anchor,
        next_run_at=anchor,
        max_occurrences=max_occurrences,
        status="active",
    )
    db.add(recurrence)
    await db.flush()

    if max_occurrences is None or max_occurrences > 0:
        await _materialise(db, obligation, recurrence)
    return recurrence


async def _materialise(
    db: AsyncSession,
    obligation: Obligation,
    recurrence: ObligationRecurrence,
) -> ObligationDeadline:
    from app.services.obligation_lifecycle import create_deadline

    deadline = await create_deadline(
        db,
        obligation,
        deadline_type="RECURRING",
        due_at=datetime.combine(
            recurrence.next_run_at,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ),
    )
    recurrence.occurrences_generated += 1
    recurrence.next_run_at = next_occurrence(
        recurrence.next_run_at, recurrence.frequency, recurrence.interval
    )
    if (
        recurrence.max_occurrences is not None
        and recurrence.occurrences_generated >= recurrence.max_occurrences
    ):
        recurrence.status = "inactive"
    await db.flush()
    return deadline


async def materialise_pending_instances(
    db: AsyncSession,
    *,
    up_to: date | None = None,
    limit: int = 500,
) -> int:
    """Materialise deadlines for all active recurrences due up to ``up_to``.

    Returns the number of deadlines created. Used by the daily beat job.
    """
    horizon = up_to or date.today()
    result = await db.execute(
        select(ObligationRecurrence)
        .where(
            ObligationRecurrence.status == "active",
            ObligationRecurrence.next_run_at <= horizon,
        )
        .order_by(ObligationRecurrence.next_run_at)
        .limit(limit)
    )
    recurrences = result.scalars().all()

    created = 0
    for recurrence in recurrences:
        obligation_result = await db.execute(
            select(Obligation).where(Obligation.id == recurrence.obligation_id)
        )
        obligation = obligation_result.scalar_one_or_none()
        if obligation is None:
            continue
        await _materialise(db, obligation, recurrence)
        created += 1
    if created:
        await db.flush()
    return created