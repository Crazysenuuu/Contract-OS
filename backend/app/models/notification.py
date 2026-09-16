"""Notification models for tracking sent emails.

Stores notification history for audit and retry purposes.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
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


class Notification(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A sent notification record."""

    __tablename__ = "notifications"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    notification_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'review_invitation', 'agreement_viewed', 'change_requested',
        # 'agreement_accepted', 'signature_completed', 'approval_request',
        # 'workflow_transition', 'compliance_violation', 'obligation_reminder'
    )

    to_email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    subject: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="sent",
        # 'pending', 'sent', 'failed', 'logged'
    )

    message_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        # SendGrid message ID
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    metadata_: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        # Additional context: actor_name, previous_state, etc.
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # When the recipient acknowledged the notification (null = unread).
    # Used by the dashboard notification badge (spec 2.13 / real-time UX).
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    # Relationships
    organization = relationship("Organization")
    agreement = relationship("Agreement")


class NotificationPreference(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """User notification preferences."""

    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Notification type toggles
    email_review_invitation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_agreement_viewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_change_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_agreement_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_signature_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_approval_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_workflow_transition: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_compliance_violation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_obligation_reminder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Digest settings
    digest_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    digest_frequency: Mapped[str] = mapped_column(String(20), nullable=False, default="daily")
    # 'daily', 'weekly'

    # Relationships
    user = relationship("User")
    organization = relationship("Organization")
