"""Retention, legal hold, and document repository models (spec 1.22 / 2.07).

Retention policies define how long agreement documents must be kept before
archive/delete; legal holds freeze retention for agreements involved in
litigation or investigation; repository records track stored document
artifacts (content refs into object storage, hashes, classification).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class RetentionPolicy(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """How long agreement documents are retained before archive/delete."""

    __tablename__ = "retention_policies"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # 'agreement_type' (applies to a specific type key) or 'all'
    scope: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="all",
    )

    agreement_type_key: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    retention_months: Mapped[int] = mapped_column(
        nullable=False,
        default=84,  # 7 years
    )

    # Action when retention expires: 'archive' or 'delete'
    disposition: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="archive",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class RetentionRecord(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Per-agreement retention calculation."""

    __tablename__ = "retention_records"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("retention_policies.id", ondelete="SET NULL"),
        nullable=True,
    )

    retention_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        # 'active', 'held', 'expired', 'archived', 'deleted', 'retained'
    )

    # Reference anchor date the retention clock started from.
    anchor_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    metadata_: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    agreement = relationship("Agreement")


class LegalHold(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A legal hold freezing retention on an agreement or the whole org."""

    __tablename__ = "legal_holds"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # 'litigation', 'investigation', 'regulatory', 'audit', 'manual'
    hold_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="manual",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        # 'active', 'released'
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    placed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    released_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    agreement = relationship("Agreement")


class RepositoryRecord(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A stored document artifact in the executed-agreement repository."""

    __tablename__ = "repository_records"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id", ondelete="SET NULL"),
        nullable=True,
    )

    document_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'executed', 'amendment', 'evidence_package', 'final_pdf'
    )

    content_ref: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        # Object-storage key / internal path.
    )

    content_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="application/pdf",
    )

    size_bytes: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    classification: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="internal",
        # 'public', 'internal', 'confidential', 'restricted'
    )

    is_executed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    metadata_: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "agreement_id",
            "version_id",
            "document_type",
            name="uq_repository_agreement_version_type",
        ),
    )

    agreement = relationship("Agreement")