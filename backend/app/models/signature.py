"""Internal signature model.

Separate from ExternalPartySignature because internal users sign
via JWT auth, not via tokenized links.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class InternalSignature(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Signature from an internal user (authenticated via JWT)."""

    __tablename__ = "internal_signatures"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id"),
        nullable=False,
    )

    signed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    consent_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    signature_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    # Relationships
    agreement = relationship("Agreement")
    user = relationship("User")
    version = relationship("AgreementVersion")
