"""Webhook notification models for external integrations.

Stores webhook configurations and delivery attempts.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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


class WebhookEndpoint(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A configured webhook endpoint."""

    __tablename__ = "webhook_endpoints"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    url: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    secret: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        # HMAC secret for signature verification
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    events: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        # Which events to subscribe to:
        # ["agreement.created", "agreement.signed", "compliance.checked", etc.]
    )

    headers: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        # Custom headers to include in webhook calls
    )

    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
    )

    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10,
    )

    # Relationships
    organization = relationship("Organization")
    deliveries = relationship(
        "WebhookDelivery",
        back_populates="endpoint",
        cascade="all, delete-orphan",
    )


class WebhookDelivery(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A single webhook delivery attempt."""

    __tablename__ = "webhook_deliveries"

    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    payload: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        # 'pending', 'success', 'failed', 'retrying'
    )

    response_status_code: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    response_body: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    endpoint = relationship(
        "WebhookEndpoint",
        back_populates="deliveries",
    )
