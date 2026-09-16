"""Signing-session models (spec 2.06.1, 2.06.7, 2.06.13-2.06.18).

A signing session is a short-lived, single-signer context created for a
signature request. It carries a one-time opaque token (stored only as a
SHA-256 hash), enforces consent + step-up authentication before a signature
can be placed, and records every state change in a chained ``SigningEvent``
evidence ledger whose rows are hash-bound to their predecessor.

``SignaturePlacement`` materialises the signer's signature field(s) on the
document so the server, not the client, decides where a signature may go.

``IdempotencyKey`` makes the dangerous signature/decline/exchange mutations
safe to retry: a duplicate key returns the stored result instead of
processing the mutation twice.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SigningSessionStatus(str):
    CREATED = "CREATED"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    READY = "READY"
    SIGNING = "SIGNING"
    SIGNED = "SIGNED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class SigningEventType(str):
    REQUEST_OPENED = "REQUEST_OPENED"
    DOCUMENT_VIEWED = "DOCUMENT_VIEWED"
    CONSENT_GIVEN = "CONSENT_GIVEN"
    AUTHENTICATION_STARTED = "AUTHENTICATION_STARTED"
    AUTHENTICATION_PASSED = "AUTHENTICATION_PASSED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    SIGNATURE_PREPARED = "SIGNATURE_PREPARED"
    SIGNED = "SIGNED"
    DECLINED = "DECLINED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    SESSION_CANCELLED = "SESSION_CANCELLED"


class SignatureType(str):
    TYPED = "TYPED"
    DRAWN = "DRAWN"
    IMAGE = "IMAGE"
    PROVIDER = "PROVIDER"


def canonical_json(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def evidence_hash(*, previous_hash: str | None, event: dict) -> str:
    """Hash an event into the per-session evidence chain (2.06.18)."""
    payload = {"previous_hash": previous_hash, "event": event}
    return hashlib.sha256(canonical_json(payload)).hexdigest()


class SigningSession(
    UUIDPrimaryKeyMixin,
    Base,
):
    __tablename__ = "signing_sessions"

    signature_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signature_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default=SigningSessionStatus.CREATED,
    )

    # One-time opaque token — only its SHA-256 hash is stored (2.06.7).
    token_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        unique=True,
    )
    token_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    token_revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    authenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    consented_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    signed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    declined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    decline_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    signature_request = relationship("SignatureRequest")
    events = relationship(
        "SigningEvent",
        back_populates="session",
        order_by="SigningEvent.created_at",
    )
    placements = relationship(
        "SignaturePlacement",
        back_populates="session",
        cascade="all, delete-orphan",
    )


class SignaturePlacement(
    UUIDPrimaryKeyMixin,
    Base,
):
    __tablename__ = "signature_placements"

    signing_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signing_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    page_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    width: Mapped[float] = mapped_column(Float, nullable=False)
    height: Mapped[float] = mapped_column(Float, nullable=False)

    field_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    signature_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=SignatureType.TYPED,
    )

    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )

    session = relationship("SigningSession", back_populates="placements")


class SigningEvent(
    Base,
):
    """Hash-chained evidence ledger for a signing session (2.06.17-2.06.18)."""

    __tablename__ = "signing_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    signing_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signing_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    event_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    event_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    prev_event_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    __table_args__ = (
        UniqueConstraint(
            "signing_session_id",
            "event_hash",
            name="uq_signing_event_hash",
        ),
    )

    session = relationship("SigningSession", back_populates="events")


class IdempotencyKey(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Request-scoped idempotency store for risky mutations (2.06.16).

    Only the SHA-256 of the client's key is stored. A replayed mutation
    returns the previously computed result instead of executing again.
    """

    __tablename__ = "idempotency_keys"

    key_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
    )

    method: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    response_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


def hash_signing_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_idempotency_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()