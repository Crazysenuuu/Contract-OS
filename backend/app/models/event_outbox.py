"""Event outbox model (spec 1.14).

The outbox is the transactional bridge between domain state and side
effects (notifications, webhooks, integrations). Domain services append an
event in the SAME transaction as the state change, and a dispatcher
publishes + delivers events afterwards. Payloads are deliberately limited
to identifiers + small context — never full documents — so the outbox
stays small and replay-safe.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class OutboxEvent(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "outbox_events"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        # e.g. 'agreement.status_changed', 'negotiation.proposal_created',
        # 'signature.completed', 'notification.required'
    )

    aggregate_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        # e.g. 'agreement', 'change_proposal', 'signature_request'
    )

    aggregate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    payload: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        # Small, authorized context only:
        # {"recipient_user_id": ..., "recipient_email": ..., "subject": ...}
    )

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    event_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
        # 'pending', 'published', 'failed'
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    event_key: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        index=True,
        # Deterministic logical-event key for idempotent publish — the same
        # retried business operation re-publishes with the same key and is
        # deduplicated instead of enqueued twice.
    )

    __table_args__ = (
        UniqueConstraint(
            "event_key",
            name="uq_outbox_events_event_key",
        ),
    )