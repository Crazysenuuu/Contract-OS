"""Execution evidence models (spec 1.15 / 2.06).

Captures the full evidence trail around electronic execution:
signature requests and per-signer records, execution requirements that must
be satisfied before sealing, and a sealed execution package containing
verifiable evidence items (document hash, consent, identity, signatures).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class SignatureRequest(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A request for a specific person to sign an agreement version."""

    __tablename__ = "signature_requests"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
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

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id", ondelete="CASCADE"),
        nullable=False,
    )

    party_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_parties.id", ondelete="SET NULL"),
        nullable=True,
        # Which party this signer represents (prevents cross-party signing).
    )

    # Sequential/composite signing order (spec 2.06.19, 2.06.20): a signer
    # whose order is N cannot sign until every signer with a lower order
    # on the same agreement has completed. Parallel signers share an order.
    signing_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="signer",
        # 'signer', 'representative', 'witness'
    )

    signer_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="external",
        # 'internal' (JWT-authenticated user), 'external' (tokenized link)
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        # 'pending', 'sent', 'signed', 'declined', 'cancelled', 'expired'
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    signed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Relationships
    agreement = relationship("Agreement")
    version = relationship("AgreementVersion")
    party = relationship("AgreementParty")
    signers = relationship(
        "SignerRecord",
        back_populates="signature_request",
        cascade="all, delete-orphan",
    )


class SignerRecord(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """One completed signature with evidence."""

    __tablename__ = "signer_records"

    signature_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signature_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id", ondelete="CASCADE"),
        nullable=False,
    )

    signer_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        # 'internal', 'external'
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    email: Mapped[str] = mapped_column(
        String(320),
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

    identity_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    identity_method: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        # 'none', 'otp', 'mfa', 'kyc_provider'
    )

    signature_request = relationship(
        "SignatureRequest",
        back_populates="signers",
    )


class ExecutionRequirement(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A requirement that must hold before an agreement may be executed.

    Configured per tenant / agreement type / agreement. Satisfaction is
    recorded with who satisfied it and when.
    """

    __tablename__ = "execution_requirements"

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

    agreement_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_types.id", ondelete="CASCADE"),
        nullable=True,
    )

    requirement_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'signatory_authority', 'consent', 'identity_verification',
        # 'witness', 'notarization', 'quorum', 'document_finalized'
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="required",
        # 'required', 'optional'
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        # 'pending', 'satisfied', 'waived', 'failed'
    )

    satisfied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    satisfied_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )


class ExecutionPackage(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Sealed, verifiable package of all execution evidence."""

    __tablename__ = "execution_packages"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
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

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id", ondelete="CASCADE"),
        nullable=False,
    )

    final_document_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    package_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
        # 'draft', 'sealed'
    )

    sealed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    sealed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    items = relationship(
        "ExecutionEvidenceItem",
        back_populates="package",
        cascade="all, delete-orphan",
        order_by="ExecutionEvidenceItem.created_at",
    )


class ExecutionEvidenceItem(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A single piece of evidence inside an execution package."""

    __tablename__ = "execution_evidence_items"

    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    evidence_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'signature', 'consent', 'identity', 'document_hash',
        # 'provider_certificate', 'authority_check'
    )

    data_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    content_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    package = relationship(
        "ExecutionPackage",
        back_populates="items",
    )