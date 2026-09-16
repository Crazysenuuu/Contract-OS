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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class AgreementParty(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Which legal entities are parties to an agreement."""

    __tablename__ = "agreement_parties"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agreements.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_entities.id"),
        nullable=False,
    )

    party_role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,  # 'disclosing', 'receiving', 'disclosing_and_receiving'
    )

    display_name: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    signatory_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Relationships
    agreement = relationship(
        "Agreement",
        back_populates="parties",
    )

    legal_entity = relationship("LegalEntity")

    participants = relationship(
        "AgreementParticipant",
        back_populates="party",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "agreement_id",
            "legal_entity_id",
            name="uq_agreement_party_entity",
        ),
    )


class AgreementParticipant(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Users explicitly authorized on an agreement.

    Being a member of a company does NOT automatically grant access.
    Access must be explicitly granted via this table.
    """

    __tablename__ = "agreement_participants"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agreements.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    agreement_party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agreement_parties.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    participant_role: Mapped[str] = mapped_column(
        String(100),
        nullable=False,  # 'signatory', 'lawyer', 'legal_reviewer', 'business_reviewer', etc.
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="active",
    )

    # Fine-grained permissions
    can_view: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    can_comment: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    can_propose_changes: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    can_approve: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    can_sign: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Relationships
    agreement = relationship(
        "Agreement",
        back_populates="participants",
    )

    party = relationship(
        "AgreementParty",
        back_populates="participants",
    )

    user = relationship("User")

    __table_args__ = (
        UniqueConstraint(
            "agreement_id",
            "user_id",
            name="uq_agreement_participant_user",
        ),
    )


class LegalRepresentative(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Lawyer profile — distinguishes a normal employee from legal counsel."""

    __tablename__ = "legal_representatives"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    professional_name: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    professional_identifier: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,  # Bar number, license ID, etc.
    )

    jurisdiction: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,  # e.g., 'Sri Lanka', 'England & Wales'
    )

    verification_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="unverified",  # 'unverified', 'pending', 'verified', 'rejected'
    )

    # Relationships
    user = relationship("User")

    organization = relationship("Organization")

    assignments = relationship(
        "LegalRepresentativeAssignment",
        back_populates="representative",
        cascade="all, delete-orphan",
    )


class LegalRepresentativeAssignment(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Bind a lawyer to a specific party on an agreement.

    Example:
        Agreement #123
            Party A
                └── Legal Representative #A (Lawyer A)
            Party B
                └── Legal Representative #B (Lawyer B)
    """

    __tablename__ = "legal_representative_assignments"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agreements.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    agreement_party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agreement_parties.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    legal_representative_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "legal_representatives.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="active",
    )

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Relationships
    representative = relationship(
        "LegalRepresentative",
        back_populates="assignments",
    )

    party = relationship("AgreementParty")


class AgreementAccessGrant(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Explicit permission grants for agreement access.

    For advanced permissions beyond participant roles.
    Supports temporary access with expiration.
    """

    __tablename__ = "agreement_access_grants"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agreements.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    permission_key: Mapped[str] = mapped_column(
        String(150),
        nullable=False,  # 'agreement.view', 'agreement.comment', etc.
    )

    granted_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="active",
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    granter = relationship("User", foreign_keys=[granted_by])
