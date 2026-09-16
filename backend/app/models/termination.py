"""Termination models.

Track termination of an agreement end to end: the right/ground relied
upon, the notice given and its delivery evidence, any applicable cure
period, the effective date, verification that outstanding obligations
have been discharged, and obligations that survive termination.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
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

    post_termination_obligations = relationship(
        "PostTerminationObligation",
        back_populates="termination",
        cascade="all, delete-orphan",
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