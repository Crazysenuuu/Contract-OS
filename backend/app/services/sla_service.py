"""SLA monitoring service (spec §11: monitorable service levels).

Obligations extracted with measurable service levels (``obligation_type``
= ``sla`` and ``metadata_json["sla_metrics"]`` populated by the extraction
engine) are monitored here:

- Review deadlines: each SLA obligation gets an OPEN ``ObligationDeadline``
  per measurement period (monthly/weekly/...), so reminders, dashboards and
  the obligation lifecycle track when each service level must be evidenced.
- Breach detection: when an SLA obligation's deadline lapses without
  completion, the lifecycle marks it OVERDUE and this service records an
  ``SLA_BREACH`` event carrying the committed metrics for audit.

Uptime is measured from external monitoring data; this service does not
fabricate measurements. It schedules *when* each level is reviewed and
flags *when a review is missed*, which is what the hourly
``sweep_sla_violations`` beat task needs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.obligation import Obligation, ObligationDeadline
from app.services.obligation_lifecycle import (
    add_event,
    create_deadline,
    evaluate_overdue,
    now_utc,
    normalise_status,
)

# Statuses in which an SLA is being actively tracked.
_ACTIVE_STATUSES = {"CONFIRMED", "ASSIGNED", "OPEN", "IN_PROGRESS", "OVERDUE"}

DEADLINE_TYPE = "SLA_REVIEW"

_DEFAULT_PERIOD = "monthly"

_PERIODS = ("daily", "weekly", "monthly", "quarterly", "annually")


def next_period_end(now: datetime, period: str) -> datetime:
    """Start of the next measurement period after ``now`` (UTC midnight).

    The review for the period containing ``now`` is due when that period
    ends, i.e. at the start of the following period.
    """
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "daily":
        return day_start + timedelta(days=1)
    if period == "weekly":
        days_ahead = 7 - day_start.weekday()  # days until next Monday
        if days_ahead == 0:
            days_ahead = 7
        return day_start + timedelta(days=days_ahead)
    if period == "monthly":
        if now.month == 12:
            return now.replace(
                year=now.year + 1, month=1, day=1,
                hour=0, minute=0, second=0, microsecond=0,
            )
        return now.replace(
            month=now.month + 1, day=1,
            hour=0, minute=0, second=0, microsecond=0,
        )
    if period == "quarterly":
        next_quarter_month = ((now.month - 1) // 3 + 1) * 3 + 1
        if next_quarter_month > 12:
            return now.replace(
                year=now.year + 1, month=1, day=1,
                hour=0, minute=0, second=0, microsecond=0,
            )
        return now.replace(
            month=next_quarter_month, day=1,
            hour=0, minute=0, second=0, microsecond=0,
        )
    if period == "annually":
        return now.replace(
            year=now.year + 1, month=1, day=1,
            hour=0, minute=0, second=0, microsecond=0,
        )
    raise ValueError(f"Unsupported SLA measurement period: {period}")


def sla_metrics_of(obligation: Obligation) -> dict | None:
    """The committed service-level metrics of an obligation, if any."""
    metrics = (obligation.metadata_json or {}).get("sla_metrics")
    if isinstance(metrics, dict) and metrics:
        return metrics
    return None


async def _has_future_review(db: AsyncSession, obligation: Obligation) -> bool:
    result = await db.execute(
        select(ObligationDeadline.id)
        .where(
            ObligationDeadline.obligation_id == obligation.id,
            ObligationDeadline.deadline_type == DEADLINE_TYPE,
            ObligationDeadline.status == "OPEN",
            ObligationDeadline.due_at > now_utc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def ensure_sla_review_deadlines(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Materialise the next review deadline for every tracked SLA obligation.

    Returns the number of deadlines created.
    """
    now = now or now_utc()
    created = 0
    result = await db.execute(
        select(Obligation).where(Obligation.obligation_type == "sla")
    )
    for obligation in result.scalars().all():
        if normalise_status(obligation.status) not in _ACTIVE_STATUSES:
            continue
        metrics = sla_metrics_of(obligation)
        if metrics is None:
            continue
        if await _has_future_review(db, obligation):
            continue
        period = metrics.get("measurement_period") or _DEFAULT_PERIOD
        if period not in _PERIODS:
            period = _DEFAULT_PERIOD
        due_at = next_period_end(now, period)
        await create_deadline(
            db,
            obligation,
            deadline_type=DEADLINE_TYPE,
            due_at=due_at,
            calculation_rule={"measurement_period": period, **metrics},
            source_event="SLA_EXTRACTION",
            timezone_name="UTC",
        )
        created += 1
    if created:
        await db.flush()
    return created


async def detect_sla_breaches(db: AsyncSession, *, now: datetime | None = None) -> list[dict]:
    """Flag lapsed SLA obligations as OVERDUE with a breach event.

    Reuses the standard overdue evaluation (spec 2.08.22); a breach event
    with the committed metrics is added so audit can show which service
    level was missed.
    """
    now = now or now_utc()
    breaches: list[dict] = []
    result = await db.execute(
        select(Obligation).where(
            Obligation.obligation_type == "sla",
            Obligation.status.in_(["OPEN", "IN_PROGRESS"]),
        )
    )
    for obligation in result.scalars().all():
        metrics = sla_metrics_of(obligation) or {}
        was_overdue = await evaluate_overdue(db, obligation)
        if was_overdue:
            await add_event(
                db,
                obligation,
                "SLA_BREACH",
                None,
                sla_metrics=metrics,
                detected_at=now.isoformat(),
            )
            breaches.append(
                {
                    "obligation_id": str(obligation.id),
                    "agreement_id": str(obligation.agreement_id),
                    "sla_metrics": metrics,
                }
            )
    if breaches:
        await db.flush()
    return breaches


async def detect_sla_violations(db: AsyncSession, *, now: datetime | None = None) -> dict:
    """Hourly sweep entry point: schedule reviews and detect breaches."""
    now = now or now_utc()
    deadlines_created = await ensure_sla_review_deadlines(db, now=now)
    breaches = await detect_sla_breaches(db, now=now)
    return {
        "deadlines_created": deadlines_created,
        "breaches": breaches,
        "breach_count": len(breaches),
    }
