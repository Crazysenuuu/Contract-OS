import datetime
import uuid
from datetime import date

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
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


class Agreement(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "agreements"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    agreement_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_types.id"),
        index=True,
        nullable=False,
    )

    # Definition snapshot: the agreement_type.version this contract was
    # created against, so later edits to the type definition cannot silently
    # reinterpret existing agreements (spec 1.4 immutability).
    agreement_type_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    parent_agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id"),
        nullable=True,
        index=True,
        # For hierarchical document sets: an SOW points to its MSA; the MSA's
        # foreign key is null. A child agreement inherits negotiated terms
        # from the parent but cannot outlive a terminated parent.
    )

    child_agreements = relationship(
        "Agreement",
        back_populates="parent_agreement",
    )

    parent_agreement = relationship(
        "Agreement",
        remote_side="Agreement.id",
        back_populates="child_agreements",
    )

    # Server-generated human-readable business reference (spec 2.01 §46):
    # ``AGR-<org prefix>-<seq>``. The UUID primary key stays the database
    # identity; this is display/search only and unique per organization.
    # Assigned by next_agreement_number() at every creation site.
    agreement_number: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="draft",
    )

    governing_law: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    effective_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    currency: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
    )

    execution_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    expiry_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    data: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    # Spec §70: per-answer source tag (USER_PROVIDED / EXTRACTED / INFERRED /
    # SYSTEM_DEFAULT / LEGAL_REQUIREMENT / REVIEW_REQUIRED) keyed by answer key.
    answer_provenance: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        default=dict,
    )

    # Spec §72: synthetic / fixture agreements are flagged so production
    # reporting, obligations and AI never treat them as real contracts.
    is_test_data: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        index=True,
    )

    # eSignature fields
    sealed_document_key: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    sealed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


    organization = relationship(
        "Organization",
        back_populates="agreements",
    )

    # Hot serializer path: list/detail payloads read versions, parties and
    # participants after awaits (sessions are closed mid-request). Default
    # lazy="select" raises MissingGreenlet there; selectin keeps one extra
    # query per collection and makes access always safe.
    versions = relationship(
        "AgreementVersion",
        back_populates="agreement",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    parties = relationship(
        "AgreementParty",
        back_populates="agreement",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    participants = relationship(
        "AgreementParticipant",
        back_populates="agreement",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class AgreementVersion(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "agreement_versions"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agreements.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="draft",
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    locked_at: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # Immutable snapshot of the agreement answers at the moment this version
    # was created (spec §26/§3: editing a draft appends version N+1 and never
    # mutates version N). Enables view/diff/restore of a version's inputs,
    # not just its rendered text.
    data: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Human-readable label describing what this version captures.
    note: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    translation_sync: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    agreement = relationship(
        "Agreement",
        back_populates="versions",
    )


Index(
    "uq_agreements_org_number",
    Agreement.organization_id,
    Agreement.agreement_number,
    unique=True,
    postgresql_where=Agreement.agreement_number.isnot(None),
)
