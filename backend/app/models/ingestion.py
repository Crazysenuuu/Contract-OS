"""Legacy contract ingestion & OCR pipeline models (spec 24.1).

Enterprise clients bring thousands of historical, non-standard, and often
scanned PDF contracts. The ingestion pipeline:

1. IngestionJob  — one import request (file, source, status).
2. OCRDocument   — per-file OCR run: provider result, confidence, extracted
                   text, and routed-to-HITL state when confidence is low.
3. HumanReviewTask — human-in-the-loop queue entry for degraded OCR docs
                   that must be manually verified before they are committed
                   to the contract repository.

After a document passes (machine or human) verification, the standard
intelligence layer extracts the same metadata (effective date, termination,
renewal, liability cap) as natively generated contracts.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class IngestionJob(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A single legacy-contract ingestion request."""

    __tablename__ = "ingestion_jobs"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="upload",
        # 'upload', 'email', 'drive', 'bulk_import'
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="queued",
        # 'queued', 'processing', 'needs_review', 'completed', 'failed'
    )

    total_files: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
    )

    completed_files: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
    )

    failed_files: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
    )

    # Where the committed agreements should be filed.
    target_agreement_type_key: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    metadata_: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    # Review-queue/summary serializers read documents after awaits.
    documents = relationship(
        "OCRDocument",
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class OCRDocument(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """One scanned document processed by the OCR pipeline."""

    __tablename__ = "ocr_documents"

    __table_args__ = (
        # Partial index over the un-indexed rows only. The Beat sweep asks
        # for exactly `indexed_at IS NULL`, so the (overwhelmingly larger)
        # indexed population should not sit in the index. Declared here, not
        # via column-level index=True, because the predicate and the custom
        # name must match migration 7c2d1e0f4a89 for autogenerate to be
        # diff-clean.
        Index(
            "ix_ocr_documents_pending_index",
            "indexed_at",
            postgresql_where=text("indexed_at IS NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    original_filename: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    content_ref: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        # Object-storage key where the original (scanned) file lives.
    )

    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="application/pdf",
    )

    # --- OCR results ---
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="mock",
        # 'mock', 'tesseract', 'aws_textract', 'google_document_ai',
        # 'azure_form_recognizer'
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="queued",
        # 'queued', 'processing', 'needs_review', 'committed', 'failed'
    )

    confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        # Provider confidence 0.0-1.0; below threshold routes to HITL.
    )

    extracted_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Extracted metadata (effective date, termination, renewal, liability
    # cap, parties) populated by the intelligence layer after OCR.
    extracted_metadata: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # The agreement created/updated after successful ingestion.
    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Set by app.services.search_index_service once this document's text has
    # been projected into `document_search_index`. NULL means "not indexed
    # yet"; the Beat sweep selects on exactly this column, so it doubles as
    # the pending-work queue for document search.
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    job = relationship("IngestionJob", back_populates="documents")


class HumanReviewTask(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A human-in-the-loop verification task for degraded OCR output."""

    __tablename__ = "human_review_tasks"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    ocr_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ocr_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        # 'pending', 'in_progress', 'approved', 'rejected'
    )

    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    review_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Edited/corrected text after human verification.
    corrected_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    ocr_document = relationship("OCRDocument")