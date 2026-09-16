"""Knowledge retrieval models (spec 2.10).

KnowledgeChunk stores legal-aware chunks of agreement text with the full
retrieval metadata (organization, agreement, version, clause, type, parties,
effective dates, jurisdiction, classification) so permission-filtered and
temporal retrieval are possible. Embeddings are stored as JSON vectors so
the same model works on SQLite (tests) and PostgreSQL (pgvector later);
search_service computes similarity in Python.
"""

import uuid
from datetime import datetime, date

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
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


class KnowledgeChunk(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A chunk of agreement text indexed for semantic retrieval."""

    __tablename__ = "knowledge_chunks"

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
        ForeignKey("agreement_versions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    clause_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extracted_clauses.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    agreement_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Deterministic content hash for change detection / re-indexing.
    content_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )

    # Embedding vector (list[float]) — JSON so tests run on SQLite.
    embedding: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    embedding_model: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # --- Retrieval metadata -------------------------------------------------
    party_ids: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    effective_from: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )

    effective_to: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )

    jurisdiction: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        index=True,
    )

    classification: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="internal",
        # 'public', 'internal', 'confidential', 'restricted'
    )

    is_current: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
        index=True,
    )

    # When this chunk stopped being current (superseded by a new version /
    # amendment). Null means it is still the current term.
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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
            "chunk_index",
            name="uq_knowledge_chunk_agreement_version_index",
        ),
    )

    agreement = relationship("Agreement")