"""OTP challenge model for signer step-up authentication (spec 24.4).

Used to satisfy the `identity_verification` execution requirement for
high-value agreements: the signer is sent a one-time code (email/SMS) and
must present it before the signature is recorded. Only the hash of the code
is stored; the raw code is never persisted.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class OTPChallenge(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A single one-time passcode challenge for a signature request."""

    __tablename__ = "otp_challenges"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    signature_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signature_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    channel: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="email",
        # 'email', 'sms'
    )

    # SHA-256 hash of the raw code — raw code never stored.
    code_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
    )

    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    consumed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Delivery metadata (message id for email/SMS) for audit.
    delivery_ref: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )