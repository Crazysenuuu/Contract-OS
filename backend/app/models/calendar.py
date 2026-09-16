"""Organization holiday calendar (spec 2.08.9 working calendar).

Business-day deadline and recurrence calculations skip weekends plus the
organization's public/bank holidays so due dates land on working days.
"""

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class OrganizationHoliday(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "organization_holidays"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    holiday_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    label: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    organization = relationship("Organization")

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "holiday_date",
            name="uq_organization_holiday_date",
        ),
    )