import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AuditEvent(
    UUIDPrimaryKeyMixin,
    Base,
):
    """A single audit event in the tamper-evident hash chain.

    Every event carries ``prev_hash`` (hash of the previous event in the
    tenant's chain) and ``event_hash`` (hash of this event's canonical
    payload + its ``prev_hash``). Together with ``sequence_number`` this
    forms a hash chain: tampering with, deleting, or reordering any event
    breaks every subsequent hash and is detectable by the verification
    service. The first event's hash is stored in ``AuditChainRoot``.
    """

    __tablename__ = "audit_events"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )

    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id"),
        nullable=True,
        index=True,
    )

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    actor_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    resource_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    metadata_json: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # --- Hash chain (1.20) ---
    # Monotonic per-tenant sequence assigned at insert time by the audit
    # service (never generated in application memory). Unique per tenant so
    # a concurrent insert collision fails loudly instead of corrupting the
    # chain.
    sequence_number: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    prev_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    event_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    # Optional batching group (Merkle-style batching).
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "sequence_number",
            name="uq_audit_events_tenant_sequence",
        ),
        UniqueConstraint(
            "tenant_id",
            "event_hash",
            name="uq_audit_events_tenant_event_hash",
        ),
    )


class AuditChainRoot(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Root of a tenant's audit hash chain.

    Stores the hash of the *first* event so verification can detect
    truncation of the chain head. One row per tenant.
    """

    __tablename__ = "audit_chain_roots"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    root_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    first_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )


class AuditBatch(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A Merkle-sealed range of the audit chain (spec 1.20.15).

    Sealing computes a Merkle root over the event hashes in a sequence
    range and anchors it externally when a timestamp authority is
    configured (1.20.16). Mutation or removal of any covered event breaks
    verification.
    """

    __tablename__ = "audit_batches"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    root_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    leaf_count: Mapped[int] = mapped_column(Integer, nullable=False)

    first_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    first_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    last_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # External timestamp anchor (RFC 3161 token / notarization receipt).
    anchor_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="internal_only",
        # 'externally_anchored' | 'internal_only' | 'anchor_failed'
    )
    anchor_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    anchored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "first_sequence", name="uq_audit_batch_first_seq"),
    )


class AuditEvidence(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """An evidence snapshot (document hash at a point in time).

    Captures the hash of a document/version plus metadata so later
    verification can prove what existed at a given moment (signing,
    execution, amendment activation, etc.).
    """

    __tablename__ = "audit_evidence"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
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

    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id", ondelete="SET NULL"),
        nullable=True,
    )

    evidence_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'signature', 'document_snapshot', 'execution', 'amendment',
        # 'termination', 'consent', 'identity'
    )

    content_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    content_ref: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        # Storage reference for the underlying blob (object-storage key,
        # internal path, or external provider reference).
    )

    metadata_json: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    # Relationships (lightweight - evidence must not be cascade-deleted)
    agreement = relationship("Agreement")
    version = relationship("AgreementVersion")
    creator = relationship("User")