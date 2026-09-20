"""Executed-agreement repository document model (spec 2.07.1-2.07.5).

A contract is a legal record made of many documents: the original
negotiated version, the execution package, the executed artifact, schedules,
annexes, certificates, signature evidence, amendments, termination notices,
settlement documents and obligation evidence.

``DocumentType`` lets an administrator configure new document categories
without code change. ``Document`` is the physical/logical artifact record
with integrity + immutability fields. ``DocumentRelationship`` links related
documents (execution package -> executed artifact, agreement -> annex, ...).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentClassification(str):
    """Classification ladder for data-sensitivity controls (spec 1.22 / 56).

    Two orthogonal axes:
    - Purpose: LEGAL_RECORD, EXECUTION_EVIDENCE, SUPPORTING_DOCUMENT
    - Sensitivity: PUBLIC, INTERNAL, CONFIDENTIAL, HIGHLY_CONFIDENTIAL, RESTRICTED

    A document carries *both* — e.g. a signed NDA is LEGAL_RECORD + CONFIDENTIAL.
    """

    # Purpose classifications
    LEGAL_RECORD = "LEGAL_RECORD"
    EXECUTION_EVIDENCE = "EXECUTION_EVIDENCE"
    SUPPORTING_DOCUMENT = "SUPPORTING_DOCUMENT"

    # Sensitivity classifications (spec §56)
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    HIGHLY_CONFIDENTIAL = "HIGHLY_CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"

    # Legacy aliases
    PRIVATE = "PRIVATE"

    @classmethod
    def sensitivity_levels(cls) -> list[str]:
        """Ordered sensitivity levels from least to most restrictive."""
        return [cls.PUBLIC, cls.INTERNAL, cls.CONFIDENTIAL, cls.HIGHLY_CONFIDENTIAL, cls.RESTRICTED]

    @classmethod
    def is_at_least(cls, actual: str | None, required: str) -> bool:
        """True if *actual* sensitivity >= *required*."""
        levels = cls.sensitivity_levels()
        try:
            return levels.index(actual or cls.PUBLIC) >= levels.index(required)
        except ValueError:
            return False


class DocumentStatus(str):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"
    UNDER_LEGAL_HOLD = "UNDER_LEGAL_HOLD"
    DISPOSED = "DISPOSED"


class DocumentType(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Database-backed document category (never a fixed hardcoded list)."""

    __tablename__ = "document_types"

    code: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    configuration: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )


class Document(
    UUIDPrimaryKeyMixin,
    Base,
):
    """A stored artifact in the repository (metadata only, bytes in object storage)."""

    __tablename__ = "documents"

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

    document_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_types.id", ondelete="RESTRICT"),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    filename: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    media_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    storage_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        # Object-storage path; never exposed to clients raw.
    )

    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    classification: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=DocumentClassification.SUPPORTING_DOCUMENT,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=DocumentStatus.ACTIVE,
    )

    # Quarantine flag (spec 2.07: untrusted uploads are held back). A
    # quarantined document is excluded from normal repository listings until
    # an administrator releases it.
    is_quarantined: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    quarantine_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    quarantined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    quarantined_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    immutable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    disposed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    document_type = relationship("DocumentType", lazy="selectin")


class DocumentRelationship(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Directed link between documents (execution package -> artifact, etc.)."""

    __tablename__ = "document_relationships"

    source_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    target_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    # e.g. EXECUTION_PACKAGE, EXECUTED_ARTIFACT, AGREEMENT, SCHEDULE,
    # ANNEX, AMENDMENT, TERMINATION_NOTICE, SETTLEMENT, EVIDENCE
    relationship_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )