"""Renewal service.

Wires the ContractRenewal/RenewalReminder models into the lifecycle:
  - derive renewal configuration from the agreement's terms data
  - evaluate whether a renewal is due
  - perform the renewal (extend the term, bump the counter, log history)
  - honour a non-renewal notice by expiring the agreement
  - schedule expiry/notice reminders ahead of the next renewal date
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.renewal import ContractRenewal, RenewalReminder
from app.services.lifecycle_service import apply_transition


class RenewalError(Exception):
    """Raised when a renewal operation is not valid."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# Days before the next renewal date at which reminders are generated.
REMINDER_LEAD_DAYS = [90, 60, 30, 7]
NOTICE_DEADLINE_REMINDER_LEAD = 30


async def get_or_create_renewal(
    db: AsyncSession,
    agreement: Agreement,
) -> ContractRenewal:
    """Return the agreement's renewal config, creating a derived one if absent."""
    result = await db.execute(
        select(ContractRenewal).where(ContractRenewal.agreement_id == agreement.id)
    )
    renewal = result.scalars().first()
    if renewal is not None:
        return renewal

    data = agreement.data or {}
    renewal = ContractRenewal(
        agreement_id=agreement.id,
        is_renewable=bool(data.get("auto_renew", data.get("is_renewable", False))),
        auto_renew=bool(data.get("auto_renew", False)),
        renewal_term_months=_as_int(data.get("renewal_term_months", 12)),
        notice_period_days=_as_int(data.get("notice_period_days", 30)),
        current_renewal_count=0,
        original_expiry_date=agreement.expiry_date,
        current_expiry_date=agreement.expiry_date,
        status="active",
        renewal_history=[],
    )
    renewal.next_renewal_date = _next_renewal_date(
        agreement.effective_date, renewal.renewal_term_months
    )
    db.add(renewal)
    await db.flush()
    return renewal


def _as_int(value: Any, default: int = 12) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _next_renewal_date(start: date | None, term_months: int) -> date | None:
    if start is None:
        return None
    year = start.year + (start.month - 1 + term_months) // 12
    month = (start.month - 1 + term_months) % 12 + 1
    return date(year, month, start.day)


def _add_months(base: date | None, months: int) -> date | None:
    if base is None:
        return None
    year = base.year + (base.month - 1 + months) // 12
    month = (base.month - 1 + months) % 12 + 1
    day = min(base.day, [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


async def update_renewal_config(
    db: AsyncSession,
    renewal: ContractRenewal,
    *,
    is_renewable: bool | None = None,
    auto_renew: bool | None = None,
    renewal_term_months: int | None = None,
    max_renewals: int | None = None,
    notice_period_days: int | None = None,
    price_increase_percentage: float | None = None,
    price_fixed_amount: float | None = None,
) -> ContractRenewal:
    """Update renewal configuration fields."""
    if is_renewable is not None:
        renewal.is_renewable = is_renewable
    if auto_renew is not None:
        renewal.auto_renew = auto_renew
    if renewal_term_months is not None:
        renewal.renewal_term_months = renewal_term_months
    if max_renewals is not None:
        renewal.max_renewals = max_renewals
    if notice_period_days is not None:
        renewal.notice_period_days = notice_period_days
    if price_increase_percentage is not None:
        renewal.price_increase_percentage = price_increase_percentage
    if price_fixed_amount is not None:
        renewal.price_fixed_amount = price_fixed_amount
    await db.flush()
    return renewal


async def process_renewal(
    db: AsyncSession,
    *,
    renewal: ContractRenewal,
    agreement: Agreement,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
    force: bool = False,
) -> dict:
    """Evaluate and perform a renewal, or expire the agreement.

    Returns a summary dict describing what happened.
    """
    if renewal.status in ("expired", "terminated"):
        if not force:
            return {"action": "noop", "reason": renewal.status}

    today = date.today()
    if not force:
        if renewal.next_renewal_date is None or renewal.next_renewal_date > today:
            return {
                "action": "not_due",
                "next_renewal_date": str(renewal.next_renewal_date) if renewal.next_renewal_date else None,
            }

    # Non-renewal notice given -> let the term lapse.
    if renewal.notice_given:
        renewal.status = "expired"
        _record_history(renewal, "non_renewed", today, renewal.current_expiry_date)
        await apply_transition(
            db,
            agreement=agreement,
            action_key="expire",
            actor_id=actor_id,
            org_id=org_id,
            metadata_json={"renewal_id": str(renewal.id), "non_renewal": True},
        )
        await db.flush()
        return {"action": "expired", "reason": "non_renewal_notice"}

    if not renewal.is_renewable:
        return {"action": "not_renewable"}

    if renewal.max_renewals is not None and renewal.current_renewal_count >= renewal.max_renewals:
        renewal.status = "expired"
        _record_history(renewal, "max_renewals_reached", today, renewal.current_expiry_date)
        await apply_transition(
            db,
            agreement=agreement,
            action_key="expire",
            actor_id=actor_id,
            org_id=org_id,
            metadata_json={"renewal_id": str(renewal.id), "reason": "max_renewals"},
        )
        await db.flush()
        return {"action": "expired", "reason": "max_renewals"}

    # Perform the renewal.
    old_expiry = renewal.current_expiry_date or agreement.expiry_date
    new_expiry = _add_months(old_expiry or today, renewal.renewal_term_months or 12)
    renewal.current_renewal_count += 1
    renewal.last_renewal_date = today
    renewal.current_expiry_date = new_expiry
    renewal.next_renewal_date = new_expiry
    renewal.status = "active"
    agreement.expiry_date = new_expiry
    _record_history(renewal, "auto_renewed" if renewal.auto_renew else "renewed", today, new_expiry)

    await apply_transition(
        db,
        agreement=agreement,
        action_key="renew",
        actor_id=actor_id,
        org_id=org_id,
        metadata_json={"renewal_id": str(renewal.id), "new_expiry": str(new_expiry)},
    )
    await db.flush()

    return {
        "action": "renewed",
        "renewal_count": renewal.current_renewal_count,
        "new_expiry": str(new_expiry),
    }


def _record_history(
    renewal: ContractRenewal,
    action: str,
    when: date,
    expiry: date | None,
) -> None:
    history = list(renewal.renewal_history or [])
    history.append(
        {
            "date": when.isoformat(),
            "action": action,
            "expiry": expiry.isoformat() if expiry else None,
        }
    )
    renewal.renewal_history = history


async def schedule_reminders(
    db: AsyncSession,
    renewal: ContractRenewal,
) -> list[RenewalReminder]:
    """Create pending reminders for the upcoming renewal cycle."""
    if renewal.next_renewal_date is None:
        return []

    created: list[RenewalReminder] = []
    for lead in REMINDER_LEAD_DAYS:
        if await _has_reminder(db, renewal, f"expiry_warning_{lead}"):
            continue
        reminder = RenewalReminder(
            renewal_id=renewal.id,
            reminder_date=datetime.combine(
                renewal.next_renewal_date - __delta_days(lead), datetime.min.time(), timezone.utc
            ),
            reminder_type=f"expiry_warning_{lead}",
            status="pending",
        )
        db.add(reminder)
        created.append(reminder)

    if not await _has_reminder(db, renewal, "notice_deadline"):
        reminder = RenewalReminder(
            renewal_id=renewal.id,
            reminder_date=datetime.combine(
                renewal.next_renewal_date - __delta_days(min(renewal.notice_period_days, NOTICE_DEADLINE_REMINDER_LEAD)),
                datetime.min.time(), timezone.utc,
            ),
            reminder_type="notice_deadline",
            status="pending",
        )
        db.add(reminder)
        created.append(reminder)

    await db.flush()
    return created


def __delta_days(days: int) -> timedelta:
    return timedelta(days=days)


async def _has_reminder(
    db: AsyncSession,
    renewal: ContractRenewal,
    reminder_type: str,
) -> bool:
    result = await db.execute(
        select(RenewalReminder.id).where(
            RenewalReminder.renewal_id == renewal.id,
            RenewalReminder.reminder_type == reminder_type,
        )
    )
    return result.scalar_one_or_none() is not None


async def mark_notice_given(
    db: AsyncSession,
    renewal: ContractRenewal,
    notice_given_date: date | None = None,
) -> ContractRenewal:
    """Record that a non-renewal notice has been given."""
    renewal.notice_given = True
    renewal.notice_given_date = notice_given_date or date.today()
    _record_history(renewal, "notice_received", date.today(), renewal.current_expiry_date)
    await db.flush()
    return renewal