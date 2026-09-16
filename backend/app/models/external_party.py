"""External party models.

Enables counterparty review without requiring account creation.
Uses opaque access tokens embedded in review links.
"""

import secrets
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
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


class ExternalParty(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """External party (counterparty) for an agreement.

    The counterparty can review, comment, and accept without creating an account.
    Access is via an opaque token embedded in the review link.
    """

    __tablename__ = "external_parties"

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

    company_name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    signatory_name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    signatory_email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )

    signatory_title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    access_token: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
    )

    # Guest-portal link expiry (spec 24: time-bound counterparty access).
    # When set, reviews/accept/sign actions are refused after this instant.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",  # 'pending', 'invited', 'viewed', 'accepted', 'rejected', 'signed'
    )

    # Capabilities
    can_comment: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    can_propose_changes: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    can_accept: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    can_sign: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # ID verification (spec 24.3): when enabled, the guest must complete an
    # email OTP challenge before accepting or signing. 'otp_email' today;
    # a KYC provider integration would add its own method value.
    requires_id_verification: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    id_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    id_verification_method: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        # 'otp_email', 'kyc_provider', 'document_check'
    )

    # Relationships
    agreement = relationship("Agreement")

    agreement_party = relationship("AgreementParty")

    sessions = relationship(
        "ExternalPartySession",
        back_populates="external_party",
        cascade="all, delete-orphan",
    )

    @staticmethod
    def generate_access_token() -> str:
        """Generate a cryptographically secure access token."""
        return secrets.token_urlsafe(96)


class ExternalPartySession(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Track review sessions for external parties.

    Records when and how the external party accessed the agreement.
    """

    __tablename__ = "external_party_sessions"

    external_party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "external_parties.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,  # 'viewed', 'commented', 'accepted', 'rejected', 'signed'
    )

    # Relationships
    external_party = relationship(
        "ExternalParty",
        back_populates="sessions",
    )


class ExternalPartyComment(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Comments from external parties on agreements.

    These are visible to authorized internal participants.
    """

    __tablename__ = "external_party_comments"

    external_party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "external_parties.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    clause_identifier: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="active",  # 'active', 'resolved', 'archived'
    )

    # Relationships
    external_party = relationship("ExternalParty")


class ExternalPartySignature(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Signature capture from external parties.

    Records the signature event with evidence for audit trail.
    """

    __tablename__ = "external_party_signatures"

    external_party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "external_parties.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id"),
        nullable=False,
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id"),
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
        nullable=False,  # The consent disclosure shown at signing
    )

    signature_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,  # Hash binding signer + doc_hash + timestamp
    )

    # Relationships
    external_party = relationship("ExternalParty")
    agreement = relationship("Agreement")
    version = relationship("AgreementVersion")
