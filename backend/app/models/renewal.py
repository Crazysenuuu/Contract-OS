"""Renewal tracking models for contract lifecycle management.

Tracks automatic and manual renewals, with notification scheduling.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class ContractRenewal(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Tracks renewal terms for a contract."""

    __tablename__ = "contract_renewals"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Renewal Terms
    is_renewable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    auto_renew: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        # True = auto-renew unless notice given
    )

    renewal_term_months: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        # Duration of each renewal term in months
    )

    max_renewals: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        # Maximum number of renewals allowed
    )

    current_renewal_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Dates
    original_expiry_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    current_expiry_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    next_renewal_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    last_renewal_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # Notice Requirements
    notice_period_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=30,
        # Days before expiry to give notice of non-renewal
    )

    notice_given: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    notice_given_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # Pricing Changes on Renewal
    price_increase_percentage: Mapped[float | None] = mapped_column(
        nullable=True,
        # e.g., 5.0 = 5% increase on renewal
    )

    price_fixed_amount: Mapped[float | None] = mapped_column(
        nullable=True,
        # Fixed price for renewal (overrides percentage)
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="active",
        # 'active', 'pending_renewal', 'renewed', 'expired', 'terminated'
    )

    # Renewal History
    renewal_history: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        # [
        #   {"date": "2024-01-15", "action": "auto_renewed", "expiry": "2025-01-15"},
        #   {"date": "2023-12-15", "action": "notice_received", "type": "non_renewal"}
        # ]
    )

    # Relationships
    agreement = relationship("Agreement")
    reminders = relationship(
        "RenewalReminder",
        back_populates="renewal",
        cascade="all, delete-orphan",
    )


class RenewalReminder(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Scheduled reminders for contract renewals."""

    __tablename__ = "renewal_reminders"

    renewal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contract_renewals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    reminder_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    reminder_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'expiry_warning_90', 'expiry_warning_60', 'expiry_warning_30',
        # 'expiry_warning_7', 'notice_deadline', 'renewal_due'
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        # 'pending', 'sent', 'failed'
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    renewal = relationship(
        "ContractRenewal",
        back_populates="reminders",
    )
