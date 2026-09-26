"""Termination models.

Track termination of an agreement end to end: the right/ground relied
upon, the notice given and its delivery evidence, any applicable cure
period, the effective date, verification that outstanding obligations
have been discharged, and obligations that survive termination.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
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


class AgreementTermination(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A termination proceeding on an agreement."""

    __tablename__ = "agreement_terminations"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    initiated_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    initiated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Ground relied upon for termination.
    reason_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        # 'mutual_agreement', 'material_breach', 'persistent_breach',
        # 'non_payment', 'insolvency', 'bankruptcy', 'for_convenience',
        # 'frustration', 'illegality', 'expiry_non_renewal'
    )

    reason_detail: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Notice
    notice_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    notice_period_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    notice_served: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    notice_evidence: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        # Delivery evidence: {"method": "email", "to": "...", "delivered_at": ...}
    )

    # Cure period (for breaches capable of remedy)
    cure_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    cure_period_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    cure_deadline: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    cured: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )

    # Outcome
    effective_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="draft",
        # 'draft', 'notice_served', 'in_cure', 'pending_effect',
        # 'effective', 'cancelled', 'cured'
    )

    # Verification that outstanding obligations were addressed.
    obligations_check: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        # {"outstanding": [...], "resolved": true/false}
    )

    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    agreement = relationship("Agreement")

    # Termination detail/settle serializers read these after awaits; keep
    # them eager (AsyncSession cannot lazy-load without MissingGreenlet).
    post_termination_obligations = relationship(
        "PostTerminationObligation",
        back_populates="termination",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    settlement = relationship(
        "TerminationSettlement",
        back_populates="termination",
        cascade="all, delete-orphan",
        lazy="selectin",
        uselist=False,
    )


class PostTerminationObligation(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """An obligation of a party that survives termination."""

    __tablename__ = "post_termination_obligations"

    termination_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_terminations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    owner_party: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    obligation_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        # 'return_materials', 'destroy_confidential', 'pay_accrued',
        # 'return_property', 'warranty_survival', 'indemnity_survival',
        # 'transition_assistance', 'audit_rights'
    )

    due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="upcoming",
        # 'upcoming', 'completed', 'waived'
    )

    termination = relationship(
        "AgreementTermination",
        back_populates="post_termination_obligations",
    )


class TerminationSettlement(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """The settlement closing out a termination (spec 1.17 §15-16).

    Exactly one per termination proceeding (unique, RESTRICT FK). Captures
    the outstanding financial position and tracks the generated checklist.
    Financial values are stored as integer minor units (e.g. cents) plus an
    ISO currency code — never floating-point (spec 1.17 §16).
    """

    __tablename__ = "termination_settlements"

    termination_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_terminations.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )

    # Outstanding amount in minor units of `currency` (e.g. cents). A real
    # money/value object replaces ad-hoc string amounts (spec 1.17 §16).
    outstanding_amount_minor: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    currency: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
    )

    # Denormalized count of checklist items that still block completion.
    obligations_remaining: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        # 'pending', 'in_progress', 'settled', 'waived'
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    settled_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    termination = relationship(
        "AgreementTermination",
        back_populates="settlement",
    )

    # Settlement checklist serializer reads items after awaits.
    items = relationship(
        "TerminationSettlementItem",
        back_populates="settlement",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TerminationSettlementItem.position",
    )


class TerminationSettlementItem(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """One row of a termination settlement checklist (spec 1.17 §15).

    Every item is either REQUIRED BY THE AGREEMENT (traceable to an
    obligation row or a surviving-clause provision) or a RECOMMENDED
    operational action (default/manual/AI-suggested). Contractual
    requirements are never invented: required items must carry a source.
    """

    __tablename__ = "termination_settlement_items"
    __table_args__ = (
        UniqueConstraint(
            "settlement_id",
            "source_type",
            "source_id",
            name="uq_settlement_items_source",
        ),
    )

    settlement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("termination_settlements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Stable key so regeneration updates in place instead of duplicating
    # (e.g. "obligation:<uuid>", "surviving:<section>", "manual:<slug>").
    item_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'financial', 'obligation', 'deliverable', 'property_return',
        # 'access', 'confidentiality', 'other'
    )

    # Spec §15: the checklist must distinguish what the agreement requires
    # from what operations merely recommends.
    kind: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="recommended",
        # 'required' (by agreement) | 'recommended' (operational)
    )

    # Whether an open item blocks complete_termination. Required items
    # normally block; surviving-clause items (confidentiality etc.) are
    # required by the agreement but survive termination instead of gating it.
    blocks_completion: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Traceability for required items: the agreement fact that produced them.
    source_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        # 'obligation' (Obligation row), 'surviving_clause' (clause template),
        # 'manual' (entered by a user)
    )

    source_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        # Obligation UUID / clause section key / "manual"
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="open",
        # 'open', 'resolved', 'waived'
    )

    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    resolution_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    settlement = relationship(
        "TerminationSettlement",
        back_populates="items",
    )