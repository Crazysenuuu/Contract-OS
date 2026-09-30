import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class User(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending_verification",
    )

    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    verification_token: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    mfa_secret: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # E.164 phone for the SMS notification channel (spec §44). Optional —
    # users without a registered number are simply skipped by SMS sends.
    phone: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default="false",
    )

    # COPPA compliance: set at signup from the provided date of birth (13+).
    # Only the derived boolean is stored — the DOB itself is never persisted
    # (data minimization).
    is_adult: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default="false",
    )

    is_admin: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default="false",
    )

    # Org resolution runs on every authenticated request (memberships ->
    # organization); keep it eager so post-await access never lazy-loads.
    memberships = relationship(
        "OrganizationMember",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    sessions = relationship(
        "UserSession",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class UserSession(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    logout_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="active",
    )

    user = relationship(
        "User",
        back_populates="sessions",
    )

    organization = relationship("Organization")


class RefreshToken(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A rotating refresh token (spec 1.22).

    Only the SHA-256 hash of the token is stored — the raw token is shown
    to the client exactly once at creation. Every refresh rotates the
    token: the old row is revoked and linked to its replacement, so a
    stolen token is detected the moment it is reused after rotation.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    replaced_by_token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        # Set when this token is rotated; points at the replacement token.
    )

    revoked_reason: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        # 'rotated', 'logout', 'suspected_reuse', 'admin'
    )

    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    user = relationship("User")
    session = relationship("UserSession")


class LoginAttempt(
    UUIDPrimaryKeyMixin,
    Base,
):
    """A login attempt for rate limiting (spec 1.22).

    App-level rate limiting that works without Redis: failures per email
    and per IP within a sliding window are counted in SQL.
    """

    __tablename__ = "login_attempts"

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        index=True,
    )

    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
        index=True,
    )

    success: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    reason: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )