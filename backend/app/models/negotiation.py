"""Negotiation and redline models.

Tracks clause-level changes, negotiation rounds, and cross-party comments.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
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


class AgreementChange(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A proposed change to an agreement.

    Each change proposal creates a new version with the modifications applied.
    The change tracks what was proposed, by whom, and its current status.
    """

    __tablename__ = "agreement_changes"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agreements.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    base_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agreement_versions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    proposed_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id"),
        nullable=True,  # Set after version is created
    )

    proposed_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    proposing_party_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_parties.id", ondelete="SET NULL"),
        nullable=True,  # Party on whose behalf the change was proposed
        index=True,
    )

    change_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,  # 'amendment', 'redline', 'counter', 'correction'
    )

    explanation: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="proposed",  # 'proposed', 'accepted', 'rejected', 'superseded'
    )

    # Relationships
    agreement = relationship("Agreement")
    base_version = relationship(
        "AgreementVersion",
        foreign_keys=[base_version_id],
    )
    proposed_version = relationship(
        "AgreementVersion",
        foreign_keys=[proposed_version_id],
    )
    proposer = relationship("User")

    items = relationship(
        "AgreementChangeItem",
        back_populates="change",
        cascade="all, delete-orphan",
    )


class AgreementChangeItem(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Individual clause modification within a change proposal.

    Each item represents one specific change: add, remove, modify, or replace.
    The server validates old_content against the immutable base version.
    """

    __tablename__ = "agreement_change_items"

    change_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agreement_changes.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    clause_identifier: Mapped[str] = mapped_column(
        String(255),
        nullable=False,  # e.g., 'section.12', 'clause.liability', 'preamble'
    )

    change_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,  # 'add', 'remove', 'modify', 'replace'
    )

    old_content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,  # Server populates from base version
    )

    new_content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,  # Proposed new content
    )

    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="proposed",  # 'proposed', 'accepted', 'rejected'
    )

    # Relationships
    change = relationship(
        "AgreementChange",
        back_populates="items",
    )


class NegotiationRound(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A round of negotiation between parties.

    Each round can contain multiple change proposals and comments.
    """

    __tablename__ = "negotiation_rounds"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agreements.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    round_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    initiated_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="open",  # 'open', 'awaiting_response', 'completed', 'cancelled'
    )

    # Relationships
    agreement = relationship("Agreement")
    initiator = relationship("User")

    comments = relationship(
        "NegotiationComment",
        back_populates="round",
        cascade="all, delete-orphan",
    )

    actions = relationship(
        "NegotiationAction",
        back_populates="round",
        cascade="all, delete-orphan",
    )


class NegotiationComment(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Cross-party comment visible to all authorized participants.

    For private notes, use LegalPrivateNote (Milestone 1.4).
    """

    __tablename__ = "negotiation_comments"

    round_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "negotiation_rounds.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    agreement_party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_parties.id"),
        nullable=False,
    )

    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    clause_identifier: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,  # Optional: link comment to specific clause
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="active",
    )

    # Relationships
    round = relationship(
        "NegotiationRound",
        back_populates="comments",
    )
    party = relationship("AgreementParty")
    author = relationship("User")


class NegotiationAction(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Explicit negotiation actions: propose, accept, reject, counter, withdraw."""

    __tablename__ = "negotiation_actions"

    round_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "negotiation_rounds.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    action_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,  # 'propose', 'accept', 'reject', 'counter', 'withdraw'
    )

    change_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_changes.id"),
        nullable=True,
    )

    comment: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    round = relationship(
        "NegotiationRound",
        back_populates="actions",
    )
    actor = relationship("User")
    change = relationship("AgreementChange")
