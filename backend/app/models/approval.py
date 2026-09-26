"""Approval models for multi-stage configurable approval workflows.

Supports:
- Configurable approval stages per organization
- Stage-specific approvers and permissions
- Approval/ rejection with comments
- Escalation based on agreement value or other criteria
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
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


class ApprovalDefinition(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Defines an approval workflow template.

    Example:
        name: "Standard Contract Approval"
        stages: [Manager, Finance, Legal]
    """

    __tablename__ = "approval_definitions"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Optional: trigger conditions
    min_value: Mapped[float | None] = mapped_column(
        Numeric(15, 2),
        nullable=True,  # Minimum agreement value to trigger
    )

    max_value: Mapped[float | None] = mapped_column(
        Numeric(15, 2),
        nullable=True,  # Maximum agreement value to trigger
    )

    # Dynamic Business Rules (JSON-encoded conditions for business-rules engine)
    from sqlalchemy import JSON
    rules: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Relationships
    organization = relationship("Organization")

    stages = relationship(
        "ApprovalStage",
        back_populates="definition",
        cascade="all, delete-orphan",
        order_by="ApprovalStage.order",
    )


class ApprovalStage(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A stage in an approval workflow.

    Example:
        Stage 1: Manager Approval
        Stage 2: Finance Approval
        Stage 3: Legal Approval
    """

    __tablename__ = "approval_stages"

    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "approval_definitions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    required_role: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,  # e.g., 'manager', 'finance', 'legal', 'director'
    )

    require_all_approvers: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,  # True = all approvers must approve; False = any one
    )

    # DOA execution semantics (spec 24.2): "sequential" stages run one
    # after another; "parallel" stages fan out to every approver at once.
    execution_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="sequential",  # 'sequential' | 'parallel'
    )

    # Quorum semantics (spec §3.6.28): a parallel stage completes once this
    # many approvers have approved. Null = all required approvers (legacy
    # behaviour preserved for existing stages).
    minimum_approvals: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Stage deadline in hours (spec §3.6.29): the deadline scanner escalates
    # (and optionally cancels) stages left undecided past this window.
    deadline_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    definition = relationship(
        "ApprovalDefinition",
        back_populates="stages",
    )

    steps = relationship(
        "ApprovalStep",
        back_populates="stage",
        cascade="all, delete-orphan",
    )


class ApprovalStep(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """An individual approval step within a stage.

    Links a specific user to an approval stage.
    """

    __tablename__ = "approval_steps"

    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "approval_stages.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    is_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Relationships
    stage = relationship(
        "ApprovalStage",
        back_populates="steps",
    )

    user = relationship("User")


class ApprovalRecord(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Records an approval/rejection for an agreement.

    Tracks the full approval history.
    """

    __tablename__ = "approval_records"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agreements.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("approval_definitions.id"),
        nullable=False,
    )

    current_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("approval_stages.id"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",  # 'pending', 'in_progress', 'approved', 'rejected', 'cancelled'
    )

    # The agreement version this approval is bound to. Decisions are only
    # valid while that version is still the current one (spec 1.2/24:
    # approvals must not outlive the terms they reviewed).
    agreement_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Separates legal review from party/signatory approval (lawyer review is
    # a distinct gate from business approvers).
    approval_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="legal_review",  # 'legal_review' | 'party_approval'
    )

    # Stage entered at (spec §3.6.29): set by the engine when the stage
    # becomes current; the deadline scanner compares it against now.
    stage_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Set when the deadline scanner escalates the record (spec §3.6.51).
    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Set when the record is version-locked (spec §3.6.34-35): a lock stops
    # decisions while the underlying version changed under the approval.
    version_locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    agreement = relationship("Agreement")
    definition = relationship("ApprovalDefinition")
    current_stage = relationship("ApprovalStage")

    decisions = relationship(
        "ApprovalDecision",
        back_populates="record",
        cascade="all, delete-orphan",
    )


class ApprovalDecision(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """An individual approval/rejection decision."""

    __tablename__ = "approval_decisions"

    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "approval_records.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("approval_stages.id"),
        nullable=False,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    decision: Mapped[str] = mapped_column(
        String(30),
        nullable=False,  # 'approved', 'rejected', 'escalated'
    )

    comment: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Relationships
    record = relationship(
        "ApprovalRecord",
        back_populates="decisions",
    )

    stage = relationship("ApprovalStage")
    user = relationship("User")
