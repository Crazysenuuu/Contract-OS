import uuid
from datetime import date

from sqlalchemy import (
    Date,
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

    organization = relationship(
        "Organization",
        back_populates="agreements",
    )

    versions = relationship(
        "AgreementVersion",
        back_populates="agreement",
        cascade="all, delete-orphan",
    )

    parties = relationship(
        "AgreementParty",
        back_populates="agreement",
        cascade="all, delete-orphan",
    )

    participants = relationship(
        "AgreementParticipant",
        back_populates="agreement",
        cascade="all, delete-orphan",
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

    translation_sync: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    agreement = relationship(
        "Agreement",
        back_populates="versions",
    )
