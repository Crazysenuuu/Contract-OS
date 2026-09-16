"""Private legal workspace models.

Ensures Party A's internal notes are invisible to Party B.
Each note is scoped to an agreement_party_id.
"""

import uuid

from sqlalchemy import (
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


class LegalPrivateNote(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Party-internal note invisible to opposing party.

    Example:
        Party A Legal Workspace:
            Lawyer: "We should reject this liability language."
            Client: "2x is acceptable."

        Party B CANNOT see this conversation.
    """

    __tablename__ = "legal_private_notes"

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

    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    note_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="legal",  # 'legal', 'strategy', 'risk', 'internal'
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Relationships
    agreement = relationship("Agreement")
    party = relationship("AgreementParty")
    author = relationship("User")


class LegalPrivateComment(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Clause-specific internal discussion within a party.

    Example:
        CLAUSE 12 — LIABILITY

        🔒 Party A Legal Workspace

        Lawyer: "We should not accept this liability cap.
                 Ask the client whether they can accept 2x annual fees."

        Client: "2x is acceptable."

        Party B cannot see this conversation.
    """

    __tablename__ = "legal_private_comments"

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

    clause_identifier: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,  # e.g., 'section.12', 'clause.liability'
    )

    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Relationships
    agreement = relationship("Agreement")
    party = relationship("AgreementParty")
    author = relationship("User")
