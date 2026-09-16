"""Recurring obligations + organization holiday calendar (spec 2.08.9).

Verifies that:
- Registering a recurrence materialises a concrete deadline and advances
  next_run_at to the following occurrence.
- materialise_pending_instances creates each due occurrence and respects
  max_occurrences (deactivating the recurrence at the cap).
- BUSINESS_DAYS deadline arithmetic skips weekends plus recorded org holidays.
"""

import pytest
import pytest_asyncio
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _obligation(db_session: AsyncSession, test_agreement):
    from app.models.obligation import Obligation

    obligation = Obligation(
        organization_id=test_agreement.organization_id,
        agreement_id=test_agreement.id,
        owner_party="counterparty",
        description="Monthly royalty payment",
        obligation_type="payment",
        frequency="MONTHLY",
        status="OPEN",
        created_by=test_agreement.created_by,
    )
    db_session.add(obligation)
    await db_session.flush()
    await db_session.refresh(obligation)
    return obligation


async def test_register_materialises_first_deadline(db_session, _obligation):
    from app.models.obligation import ObligationDeadline, ObligationRecurrence

    from app.services.recurrence_service import register_recurrence

    anchor = date(2026, 1, 15)
    recurrence = await register_recurrence(
        db_session,
        obligation=_obligation,
        frequency="MONTHLY",
        interval=1,
        anchor_date=anchor,
    )
    await db_session.commit()

    result = await db_session.execute(
        select(ObligationDeadline).where(
            ObligationDeadline.obligation_id == _obligation.id
        )
    )
    deadline = result.scalars().one()
    assert deadline.due_at.date() == anchor
    assert deadline.status == "OPEN"

    await db_session.refresh(recurrence)
    assert recurrence.occurrences_generated == 1
    assert recurrence.next_run_at == date(2026, 2, 15)


async def test_pending_instances_respect_max_occurrences(db_session, _obligation):
    from app.models.obligation import ObligationDeadline, ObligationRecurrence

    from app.services.recurrence_service import (
        materialise_pending_instances,
        register_recurrence,
    )

    recurrence = await register_recurrence(
        db_session,
        obligation=_obligation,
        frequency="WEEKLY",
        anchor_date=date(2026, 1, 1),
        max_occurrences=2,
    )
    await db_session.commit()

    created = await materialise_pending_instances(
        db_session, up_to=date(2026, 1, 15)
    )
    await db_session.commit()
    assert created == 1

    await db_session.refresh(recurrence)
    assert recurrence.occurrences_generated == 2
    assert recurrence.status == "inactive"

    result = await db_session.execute(
        select(ObligationDeadline).where(
            ObligationDeadline.obligation_id == _obligation.id
        )
    )
    assert len(result.scalars().all()) == 2


async def test_business_days_skip_holidays_and_weekends(db_session, test_org):
    from app.models.calendar import OrganizationHoliday
    from app.services.business_calendar import BusinessCalendar

    # Fri Jan 2 2026 is a working day; Mon Jan 5 2026 is a recorded holiday.
    holiday = OrganizationHoliday(
        organization_id=test_org.id,
        holiday_date=date(2026, 1, 5),
        label="Public Holiday",
    )
    db_session.add(holiday)
    await db_session.commit()

    cal = await BusinessCalendar.for_organization(db_session, test_org.id)
    # 1 business day after Fri Jan 2 skips Sat+Sun AND the holiday → Tue Jan 6.
    result = cal.add_business_days(date(2026, 1, 2), 1)
    assert result == date(2026, 1, 6)


async def test_holiday_calendar_feeds_deadline_rules(db_session, test_org):
    from app.models.calendar import OrganizationHoliday
    from app.services.obligation_lifecycle import apply_deadline_rule

    db_session.add(
        OrganizationHoliday(
            organization_id=test_org.id,
            holiday_date=date(2026, 1, 5),
            label="Public Holiday",
        )
    )
    await db_session.commit()

    from app.services.business_calendar import BusinessCalendar

    cal = await BusinessCalendar.for_organization(db_session, test_org.id)
    deadline = apply_deadline_rule(
        {
            "type": "RELATIVE",
            "amount": 1,
            "unit": "DAYS",
            "calendar": "BUSINESS_DAYS",
        },
        datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc),
        holidays=cal.holidays,
    )
    assert deadline.date() == date(2026, 1, 6)