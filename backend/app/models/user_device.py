"""User device registry for mobile push notifications (spec M2.02 / 2.06.27).

A device belongs to the authenticated user — the backend never trusts a
raw client-provided user_id (spec section 25). The (user_id, device_id)
pair is unique so push-token rotation on app start/resume upserts the
same row instead of duplicating devices (spec section 26).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserDevice(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "user_devices"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "device_id",
            name="uq_user_devices_user_device",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Client-generated stable identifier for this install/device.
    device_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    # 'ios' | 'android' | 'web'
    platform: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # Provider push token (FCM/APNs/WebPush). Nullable for non-push devices.
    push_token: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )

    app_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    revoked_reason: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        # 'logout', 'replaced', 'security'
    )

    user = relationship("User")