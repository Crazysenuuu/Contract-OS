"""API key model (spec §7).

Stores hashed API keys for programmatic access.  The raw key is shown
once at creation time; only the SHA-256 hash is persisted.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class APIKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A programmatic API key for an organisation member."""

    __tablename__ = "api_keys"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # SHA-256 hash of the raw key — never store plaintext.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    # Prefix shown in UI for identification (first 8 chars of raw key).
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)

    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated permission keys

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
