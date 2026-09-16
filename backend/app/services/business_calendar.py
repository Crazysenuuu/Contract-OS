"""Business calendar (spec 2.08.9).

Business-day arithmetic that skips weekends and an organization's recorded
holidays. Loads nothing on its own; use ``BusinessCalendar.for_organization``
to pull the org's holiday set from the database.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class BusinessCalendar:
    """Calendar of working days for an organization."""

    WEEKEND_DAYS = frozenset([5, 6])  # Saturday, Sunday

    def __init__(self, holidays: set[date] | None = None):
        self.holidays = holidays or set()

    def add_business_days(self, source: date, amount: int) -> date:
        """Return ``source`` advanced by ``amount`` working days.

        Zero or negative amounts return ``source`` unchanged (callers gate
        positive offsets upstream; budgets are non-negative).
        """
        if amount <= 0:
            return source
        result = source
        remaining = amount
        while remaining > 0:
            result = result + timedelta(days=1)
            if result.weekday() in self.WEEKEND_DAYS or result in self.holidays:
                continue
            remaining -= 1
        return result

    @classmethod
    async def for_organization(
        cls,
        db: AsyncSession,
        organization_id,
    ) -> BusinessCalendar:
        from app.models.calendar import OrganizationHoliday

        result = await db.execute(
            select(OrganizationHoliday.holiday_date).where(
                OrganizationHoliday.organization_id == organization_id
            )
        )
        return cls(holidays={row[0] for row in result.all()})