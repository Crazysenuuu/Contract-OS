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
    Index,
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

    # Extensible state bag (spec §3.20.28-30): evidence requests and other
    # per-party operational state that does not warrant its own table yet.
    party_metadata: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=dict
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
    # 'kyc_provider' is set by the KYC provider flow (spec 24.4).
    requires_id_verification: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Full KYC provider flow (spec 24.4): when set together with
    # requires_id_verification, verification is delegated to the configured
    # identity provider (document scan + selfie) instead of the email OTP
    # challenge. Intended for high-value agreements.
    requires_kyc: Mapped[bool] = mapped_column(
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
        # 'otp_email', 'kyc_provider'
    )

    # KYC provider session (spec 24.4). Set when verification is delegated
    # to the configured identity provider; cleared never — superseded
    # sessions stay on KycVerificationAttempt for the audit trail.
    kyc_provider: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    kyc_session_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    kyc_session_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
        # 'requires_input', 'processing', 'verified', 'canceled', 'failed'
    )

    # Spec 24.4 session start career (spec 31/25/26 alignment):
    # - kyc_session_url is the hosted flow URL the guest is redirected to
    #   (Stripe hosted page, or the mock's synthetic URL). None when the
    #   provider is embedded-only.
    # - kyc_session_expires_at is the host-side session TTL; the party is
    #   refused sign/accept past this instant (the host flow URL may also
    #   verify against it on return).
    # - kyc_session_auth_method records how the guest proved identity so the
    #   audit ledger can answer "who signed what, with which auth".
    kyc_session_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    kyc_session_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    kyc_session_auth_method: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        # 'none' | 'otp_email' | 'kyc_provider'
    )

    # Signature event stream (spec §31). One row per consequential action the
    # guest performed: accepted / signed. Captures the identity method used
    # (otp_email | kyc_provider) and the document hash at the time so the
    # audit ledger can answer "who signed what, when, and with what hash".
    signature_events = relationship(
        "SignatureEvent",
        back_populates="external_party",
        cascade="all, delete-orphan",
        order_by="SignatureEvent.created_at",
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


class SignatureEvent(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Audit event for external-party signature actions (spec §31).

    Captures who did what, how they authenticated, and the document hash at
    the time of the action. No raw identity documents are stored — only
    coarse evidence for the audit ledger (spec 33).
    """

    __tablename__ = "external_party_signature_events"

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
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Action / event type
    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'accepted', 'commented', 'rejected', 'signed'
    )

    # Identity / authentication evidence (coarse, for audit only)
    authentication_method: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        # 'none' | 'otp_email' | 'kyc_provider'
    )

    # Document hash at the time of the action (spec §25).
    document_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    # Consent disclosure text (spec §31)
    consent_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    external_party = relationship("ExternalParty")
    agreement = relationship("Agreement")


class KycVerificationAttempt(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Journal of KYC verification attempts (spec 24.4).

    One row per provider session attempt, preserving the full history of
    starts, declines and completions for the audit trail. Stores outcomes
    and coarse check metadata only — never document images or document
    numbers (provider retains and redacts those; spec 33 data boundary).
    """

    __tablename__ = "kyc_verification_attempts"

    __table_args__ = (
        Index("ix_kyc_attempts_party_session", "external_party_id", "session_id"),
    )

    external_party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "external_parties.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    session_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        # 'requires_input', 'processing', 'verified', 'canceled', 'failed'
    )

    failure_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Coarse provider metadata (checks performed, livemode, report id...).
    # Must not contain raw document images or document numbers.
    details: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    external_party = relationship("ExternalParty")


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

    # Spec 31/25/26 alignment: each review session is addressable by a
    # stable session id so the host can hand the guest a single session key
    # for the review link, and the host can look up the session (and its
    # expiry, auth method, last action) from it.
    user_session_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
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
