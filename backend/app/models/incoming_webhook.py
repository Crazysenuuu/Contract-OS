"""Idempotent receipt of verified incoming provider webhooks (spec 22/1.24).

External providers retry webhooks; an ``IncomingWebhookEvent`` row is the
deduplication record. The event id is provider-generated and unique, so a
replay is detected and acknowledged without re-applying the state change.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class IncomingWebhookEvent(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """One verified provider webhook event (dedup key = provider event id)."""

    __tablename__ = "incoming_webhook_events"

    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    event_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        # Provider-generated idempotency key, e.g. Stripe event id.
    )

    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="processed",
        # 'processed', 'failed'
    )

    payload: Mapped[str] = mapped_column(
        Text,
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "event_id",
            name="uq_incoming_webhook_provider_event",
        ),
    )


__all__ = ["IncomingWebhookEvent"]