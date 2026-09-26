"""Action-item model — the Action Center (spec §3.10.13-19).

An action item is a *required human decision* surfaced on the dashboard,
distinct from a notification (informational). Every item records its source
system and a stable source reference so the lifecycle can be validated
against the originating record, and the resolution is idempotent.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class ActionItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One required action for one user, from one source system."""

    __tablename__ = "action_items"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        # null = actionable by any user holding the source permission.
    )

    # 'approval_request' | 'review_request' | 'obligation_due' |
    # 'obligation_overdue' | 'renewal_decision' | 'exception' |
    # 'checkpoint' | 'anomaly'
    action_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Which subsystem produced this item, and the row it points at.
    source_system: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # 'pending' | 'completed' | 'dismissed' | 'expired'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    priority: Mapped[int] = mapped_column(
        # Higher sorts first in the action center (§3.10.17).
        nullable=False,
        default=50,
        type_=None,  # keep SQLAlchemy default Integer binding
    )

    action_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    __table_args__ = (
        # One live item per source record: re-emitting the same source event
        # updates rather than duplicates (§3.10.18-19 idempotency).
        UniqueConstraint(
            "source_system",
            "source_id",
            "status",
            name="uq_action_item_source_live",
        ),
        Index(
            "ix_action_items_org_status",
            "organization_id",
            "status",
        ),
        Index(
            "ix_action_items_assignee_status",
            "assignee_user_id",
            "status",
        ),
    )
