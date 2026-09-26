"""Ingestion intelligence models (spec §3.21).

Candidates are source-referenced assertions (metadata, lifecycle terms,
obligations) extracted from ingested documents. They are NEVER authoritative
until a human or promotion policy verifies them (§3.21.24-28). Conflicts
record competing values with a deterministic resolution policy. Provenance
keeps the raw extraction trail with source hashes for the trust boundary
(§3.21.85-87).
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
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


class ExtractionCandidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One extracted assertion awaiting verification (§3.21.22)."""

    __tablename__ = "extraction_candidates"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 'metadata' | 'lifecycle_term' | 'obligation' | 'party' | 'clause'
    candidate_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    # e.g. 'effective_date', 'termination_notice_days', 'liability_cap'
    field_key: Mapped[str] = mapped_column(String(120), nullable=False)

    value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Source reference: where in the document this came from (§3.21.23).
    source_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # {page, char_start, char_end, quote}

    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # 'pending' | 'verified' | 'rejected' | 'promoted' | 'superseded'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Set when promoted into a domain table (agreement data, obligation...).
    promoted_target: Mapped[str | None] = mapped_column(String(60), nullable=True)
    promoted_target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )


class ExtractionConflict(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Competing extracted values for the same field (§3.21.44)."""

    __tablename__ = "extraction_conflicts"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    field_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    # Candidate ids competing for the field (2+).
    candidate_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 'open' | 'resolved' — resolution records which candidate won and why.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    winner_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    resolution_rule: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IngestionBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bulk ingestion batch (§3.21.77-78).

    Groups many ingestion jobs with shared settings and a per-batch error
    isolation guarantee: one broken document never fails the batch.
    """

    __tablename__ = "ingestion_batches"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # 'pending' | 'processing' | 'completed' | 'completed_with_errors' | 'failed'
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")

    total_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Shared extraction policy for the batch (§3.21.88).
    policy: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ExtractionProvenance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The extraction audit trail (§3.21.85-87).

    Every pipeline stage that touched a document records its inputs, outputs
    and source hash, so any promoted value can be traced back to immutable
    raw bytes. This is the trust boundary's ledger.
    """

    __tablename__ = "extraction_provenance"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 'ocr' | 'parse' | 'segment' | 'classify' | 'extract' | 'verify'
    stage: Mapped[str] = mapped_column(String(30), nullable=False)

    # Version of the parser/model that produced this stage's output.
    parser_version: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # sha256 of the stage's input and output payloads.
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Stage-specific record: model name, durations, page counts, warnings.
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_extraction_provenance_doc_stage",
            "document_id",
            "stage",
        ),
    )
