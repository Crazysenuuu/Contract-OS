"""Obligation models for post-signing contract management.

Tracks obligations extracted from executed contracts.

Spec 2.08 lifecycle: CANDIDATE → CONFIRMATION_REQUIRED → CONFIRMED →
ASSIGNED → OPEN → IN_PROGRESS → COMPLETED, with OVERDUE / WAIVED /
CANCELLED / SUPERSEDED branches. Completion is distinct from evidence
verification.
"""

import uuid
from datetime import datetime, date

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
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


class Obligation(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """An obligation extracted from an executed contract."""

    __tablename__ = "obligations"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    source_clause_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id"),
        nullable=True,
    )

    source_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    source_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    title: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
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
        # 'payment', 'delivery', 'reporting', 'compliance',
        # 'notification', 'maintenance', 'insurance', 'other'
    )

    amount: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    currency: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
    )

    frequency: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        # 'once', 'daily', 'weekly', 'monthly', 'quarterly', 'annually'
    )

    due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="upcoming",
        # Spec 2.08: 'CANDIDATE', 'CONFIRMATION_REQUIRED', 'CONFIRMED',
        # 'ASSIGNED', 'OPEN', 'IN_PROGRESS', 'COMPLETED', 'OVERDUE',
        # 'WAIVED', 'CANCELLED', 'SUPERSEDED'
        # Legacy: 'upcoming', 'due', 'overdue', 'completed', 'waived',
        # 'disputed'
    )

    criticality: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="MEDIUM",
        # 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
    )

    evidence_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="NOT_REQUIRED",
        # 'NOT_REQUIRED', 'REQUIRED', 'SUBMITTED', 'UNDER_REVIEW',
        # 'VERIFIED', 'REJECTED'
    )

    metadata_json: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    evidence: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    clause_identifier: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # Relationships
    agreement = relationship("Agreement")

    reminders = relationship(
        "ObligationReminder",
        back_populates="obligation",
        cascade="all, delete-orphan",
    )

    deadlines = relationship(
        "ObligationDeadline",
        back_populates="obligation",
        cascade="all, delete-orphan",
    )

    evidence_items = relationship(
        "ObligationEvidence",
        back_populates="obligation",
        cascade="all, delete-orphan",
    )

    assignees = relationship(
        "ObligationAssignee",
        back_populates="obligation",
        cascade="all, delete-orphan",
    )

    events = relationship(
        "ObligationEvent",
        back_populates="obligation",
        cascade="all, delete-orphan",
    )

    recurrence = relationship(
        "ObligationRecurrence",
        back_populates="obligation",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "ix_obligations_agreement_status",
            "agreement_id",
            "status",
        ),
    )


class ObligationReminder(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Scheduled reminders for an obligation."""

    __tablename__ = "obligation_reminders"

    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("obligations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    reminder_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    reminder_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'advance', 'due', 'overdue', 'escalation'
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        # 'pending', 'sent', 'failed'
    )

    # Relationships
    obligation = relationship(
        "Obligation",
        back_populates="reminders",
    )


class ObligationDeadline(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A tracked deadline for an obligation (spec 2.08.6)."""

    __tablename__ = "obligation_deadlines"

    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("obligations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    deadline_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    calculation_rule: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    source_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    source_event: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    timezone: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="OPEN",
        # 'OPEN', 'SATISFIED', 'SUPERSEDED'
    )

    # Relationships
    obligation = relationship(
        "Obligation",
        back_populates="deadlines",
    )

    __table_args__ = (
        Index(
            "ix_obligation_deadlines_due",
            "due_at",
        ),
    )


class ObligationEvidence(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Evidence submitted against an obligation (spec 2.08.17)."""

    __tablename__ = "obligation_evidence"

    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("obligations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id"),
        nullable=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    submitted_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="SUBMITTED",
        # 'SUBMITTED', 'UNDER_REVIEW', 'VERIFIED', 'REJECTED'
    )

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    review_comment: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    obligation = relationship(
        "Obligation",
        back_populates="evidence_items",
    )

    __table_args__ = (
        Index(
            "ix_obligation_evidence_status",
            "status",
        ),
    )


class ObligationAssignee(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """An assignee for an obligation (spec 2.08.5)."""

    __tablename__ = "obligation_assignees"

    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("obligations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    agreement_party_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_parties.id"),
        nullable=True,
    )

    member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    responsibility_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        # 'PERFORMER', 'APPROVER', 'REVIEWER', 'RECIPIENT',
        # 'INTERNAL_OWNER', 'COUNTERPARTY_OWNER'
    )

    primary_assignee: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Relationships
    obligation = relationship(
        "Obligation",
        back_populates="assignees",
    )

    __table_args__ = (
        Index(
            "ix_obligation_assignees_member",
            "member_id",
        ),
    )


class ObligationEvent(
    UUIDPrimaryKeyMixin,
    Base,
):
    """Operational history for an obligation (spec 2.08.28)."""

    __tablename__ = "obligation_events"

    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("obligations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    previous_status: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    new_status: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    metadata_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Relationships
    obligation = relationship(
        "Obligation",
        back_populates="events",
    )

    __table_args__ = (
        Index(
            "ix_obligation_events_created",
            "created_at",
        ),
    )


class ObligationRecurrence(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Recurring obligation schedule (spec 2.08 recurring/renewal tracking).

    Drives materialisation of ObligationDeadline instances for duties that
    repeat (monthly payments, quarterly filings, yearly renewals). Each
    occurrence is materialised as a real deadline; the scheduler advances
    ``next_run_at`` each time.
    """

    __tablename__ = "obligation_recurrences"

    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("obligations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    frequency: Mapped[str] = mapped_column(
        String(20),
        nullable=False,  # 'DAILY' | 'WEEKLY' | 'MONTHLY' | 'QUARTERLY' | 'YEARLY'
    )

    interval: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    anchor_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    next_run_at: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    occurrences_generated: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    max_occurrences: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",  # 'active' | 'inactive'
    )

    obligation = relationship("Obligation", back_populates="recurrence")

    __table_args__ = (
        Index("ix_obligation_recurrences_due", "next_run_at"),
    )


class ExtractionRun(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Provenance record for an AI obligation-extraction run (spec 1.16.21).

    Every automated extraction over an agreement version is recorded so that
    candidate obligations are traceable to the exact model prompt, the
    source version and the rule configuration that produced them. Nothing
    an extraction run produces becomes a live obligation until a human
    confirms it (spec 1.16.23).
    """

    __tablename__ = "obligation_extraction_runs"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    source_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    method: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="hybrid",  # 'ai' | 'deterministic' | 'hybrid'
    )

    model_id: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    prompt_version: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    content_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    candidate_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    confirmed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="COMPLETED",
        # 'COMPLETED' | 'FAILED' | 'PARTIAL'
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    result_json: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )
