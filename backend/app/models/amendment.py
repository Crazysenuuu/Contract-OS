"""Amendment models.

An amendment is a legally effective change to an executed agreement.
Each amendment carries a sequential number, a version, and a set of
clause changes. When an amendment is activated, a snapshot of the
consolidated current terms (base agreement terms + every active
amendment applied in order) is stored so the "current terms" view
(2.07.26) can be served deterministically.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
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


class AgreementAmendment(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A formally proposed or activated amendment to an agreement."""

    __tablename__ = "agreement_amendments"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    amendment_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        # Sequential within the agreement: 1, 2, 3 ...
    )

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="proposed",
        # 'proposed', 'pending_signature', 'active', 'rejected', 'superseded'
    )

    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    effective_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    base_version_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        # AgreementVersion of the base agreement the amendment applies to
    )

    # Snapshot of the consolidated current terms at activation time.
    current_terms_snapshot: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    proposed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    changes = relationship(
        "AmendmentChange",
        back_populates="amendment",
        cascade="all, delete-orphan",
        order_by="AmendmentChange.section_key",
        lazy="selectin",
    )


class AmendmentChange(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A single clause change contained within an amendment."""

    __tablename__ = "amendment_changes"

    amendment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_amendments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    section_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        # Clause identifier, e.g. 'indemnification', 'payment_terms.net30'
    )

    change_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        # 'replace', 'insert', 'delete'
    )

    old_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    new_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Machine-readable structured change (for the current-terms engine).
    structured_delta: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    amendment = relationship(
        "AgreementAmendment",
        back_populates="changes",
    )