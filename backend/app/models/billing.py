"""Billing & entitlements models (spec 1.24).

Plans, subscriptions, tenant entitlements, usage metering and invoices.
Pricing is data (plans are rows), feature access is centralized through
the entitlement service, and billing state is kept strictly separate from
legal state — an unpaid bill never mutates an agreement.

Card details are never stored; the billing provider abstraction handles
payment and only provider references are persisted.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class BillingPlan(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A pricing plan. ``features`` maps feature_key → limit/allowance."""

    __tablename__ = "billing_plans"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        # None = system plan template available to all tenants.
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
        # e.g. 'free', 'starter', 'growth', 'enterprise'
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    monthly_price_cents: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    features: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        # feature_key → limit. Examples:
        # {
        #   "agreements": 5,
        #   "ai_analyses": 10,
        #   "signatures": null,      # null = unlimited
        #   "seats": 3,
        #   "webhooks": False,       # boolean feature flag
        # }
    )

    metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )


class Subscription(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A tenant's current subscription."""

    __tablename__ = "subscriptions"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_plans.id"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="active",
        # 'trialing', 'active', 'past_due', 'cancelled', 'expired'
    )

    current_period_start: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    current_period_end: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    seat_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    external_provider: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        # 'stripe', 'chargebee', 'none'
    )

    external_subscription_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    plan = relationship("BillingPlan")


class Entitlement(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A tenant-level override of a plan feature limit."""

    __tablename__ = "entitlements"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    feature_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    value: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        # Either {"limit": N}, {"limit": null} (unlimited) or {"enabled": bool}
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "feature_key",
            name="uq_entitlements_tenant_feature",
        ),
    )


class UsageRecord(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A metered usage event for a tenant + feature."""

    __tablename__ = "usage_records"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    feature_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="api",
        # 'api', 'worker', 'manual', 'webhook'
    )

    source_ref: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        # Reference to the object that generated the usage, e.g. an
        # agreement id or AI analysis run id — usage source traceability.
    )

    metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )


class Invoice(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """An issued invoice (billable amounts, not payment details)."""

    __tablename__ = "invoices"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
    )

    number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
        # 'draft', 'issued', 'paid', 'failed', 'void'
    )

    amount_cents: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
    )

    due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    external_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Invoice serializers read lines after awaits; default lazy load would
    # raise MissingGreenlet on the async session.
    lines = relationship(
        "InvoiceLine",
        back_populates="invoice",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class InvoiceLine(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "invoice_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    description: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    unit_price_cents: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )

    amount_cents: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )

    invoice = relationship("Invoice", back_populates="lines")