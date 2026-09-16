"""Data privacy models (spec 24.3): cryptographic shredding + redaction.

- FieldEncryptionRecord: which agreement field is encrypted under which key
  envelope, so PII is never stored in plain text within the immutable
  ledger. Cryptographic shredding = deleting the key envelope, which makes
  the ciphertext unreadable while preserving document structural integrity.
- RedactionRequest: a request to permanently redact PII from the visual
  (PDF) representation while leaving commercial terms intact.
- ErasureRequest: a GDPR/CCPA/PDPA "right to be forgotten" workflow —
  records that a data subject requested erasure, what was shredded, and the
  audit trail of what was preserved for legal/evidentiary reasons.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
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


class FieldEncryptionRecord(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Tracks one encrypted field and its key envelope reference."""

    __tablename__ = "field_encryption_records"

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

    # Field path within agreement.data, e.g. "party_a_contact_email".
    field_path: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    # Classification: 'pii', 'financial', 'health', 'other_sensitive'
    data_class: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pii",
    )

    # Reference to the key envelope. Deleting this envelope = cryptographic
    # shredding: ciphertext becomes permanently unreadable.
    key_envelope_id: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    ciphertext: Mapped[Text] = mapped_column(
        Text,
        nullable=False,
    )

    # True after cryptographic shredding (key envelope deleted).
    shredded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    shredded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    metadata_: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    agreement = relationship("Agreement")


class RedactionRequest(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A permanent redaction request on a document's visual representation."""

    __tablename__ = "redaction_requests"

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

    document_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="executed",
    )

    # Target text patterns/field paths to redact.
    targets: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    # {"field_paths": ["party_a_contact_email"],
    #  "text_patterns": ["name@example.com"]}

    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        # 'pending', 'processing', 'completed', 'rejected'
    )

    # Content ref of the redacted document (after processing).
    redacted_content_ref: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    processed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    agreement = relationship("Agreement")


class ErasureRequest(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Right-to-be-forgotten workflow (GDPR/CCPA/PDPA)."""

    __tablename__ = "erasure_requests"

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

    # Data subject identifier: email/name/ID.
    data_subject: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    # Regulation cited: 'gdpr', 'ccpa', 'pdpa', 'other'
    regulation: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="gdpr",
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="received",
        # 'received', 'under_review', 'shredded', 'completed', 'denied'
    )

    # What was cryptographically shredded.
    shredded_fields: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # What was preserved and why (legal/evidentiary carve-out).
    preserved_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    agreement = relationship("Agreement")