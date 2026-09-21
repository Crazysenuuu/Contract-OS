"""Workflow Automation & Contract Operations Orchestrator models.

Implements spec §2.11 (Agreement Gen.txt lines 52609-54975): a config-driven
workflow definition engine surfaced as event-triggered orchestration.

Table naming: this subsystem is a peer of the older §1.13 state machine
(``app/models/workflow.py``) which already owns ``workflow_definitions`` /
``workflow_transitions`` / ``workflow_instances`` with different semantics.
To avoid colliding with those tables every orchestration table is prefixed
``orch_``. The mapping from the spec's canonical table names to this model is
documented in ``backend/migrations/versions/*_orchestration_engine.py``.

Instance-authoring rules honoured in this module:
  - Workflow instances pin their definition version (step graph is read-only
    once the engine starts an instance).
  - A workflow event wait is a durable table row (spec §2.11.20), not a timer.
  - Action attempts are recorded for traceability and retry policy (§2.11.28).
  - Dead letters hold events that failed routing (§2.11.31).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OrchWorkflowStatus(str, enum.Enum):
    """Lifecycle of a workflow definition (spec §2.11.5)."""

    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class OrchScope(str, enum.Enum):
    """Where a workflow definition is visible (spec §2.11.5)."""

    GLOBAL = "global"
    ORGANIZATION = "organization"
    AGREEMENT_TYPE = "agreement_type"


class OrchInstanceStatus(str, enum.Enum):
    """Lifecycle of a workflow instance (spec §2.11.10)."""

    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUSPENDED = "suspended"
    HUMAN_INTERVENTION_REQUIRED = "human_intervention_required"


class OrchStepType(str, enum.Enum):
    """Step kinds supported by the engine (spec §2.11.11)."""

    TASK = "task"
    APPROVAL = "approval"
    CONDITION = "condition"
    DELAY = "delay"
    EVENT_WAIT = "event_wait"
    ACTION = "action"
    PARALLEL = "parallel"
    SUBWORKFLOW = "subworkflow"


class OrchWorkflowDefinition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A config-driven workflow definition (spec §2.11.7).

    Definitions are immutable once published: publishing creates a new ACTIVE
    version instead of editing the current one (spec §2.11.38). Instances pin
    ``workflow_definition_id`` so later versions never rewrite a live run.
    """

    __tablename__ = "orch_workflow_definitions"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    code = mapped_column(String(150), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[OrchWorkflowStatus] = mapped_column(
        SQLEnum(OrchWorkflowStatus, native_enum=False),
        nullable=False,
        default=OrchWorkflowStatus.DRAFT,
    )
    scope: Mapped[OrchScope] = mapped_column(
        SQLEnum(OrchScope, native_enum=False),
        nullable=False,
        default=OrchScope.GLOBAL,
    )
    trigger: Mapped[dict | None] = mapped_column(
        JSONB, nullable=False, default=dict)
    configuration: Mapped[dict | None] = mapped_column(
        JSONB, nullable=False, default=dict)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "code",
            "version",
            name="uq_orch_workflow_definitions_code_version",
        ),
    )

    steps = None
    transitions = None
    instances = None


class OrchStepDefinition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A single step in an orchestrated workflow (spec §2.11.11)."""

    __tablename__ = "orch_workflow_step_definitions"

    workflow_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orch_workflow_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_key: Mapped[str] = mapped_column(String(150), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    step_type: Mapped[OrchStepType] = mapped_column(
        SQLEnum(OrchStepType, native_enum=False), nullable=False
    )
    configuration: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "workflow_definition_id",
            "step_key",
            name="uq_orch_workflow_step_definitions_definition_step",
        ),
    )


class OrchTransition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A directed edge between steps, optionally gated by a condition.

    The engine evaluates outgoing transitions from the current step in
    ``priority`` order; the first whose condition passes is taken. A
    transition with no condition always passes (spec §2.11.15/2.11.25).
    """

    __tablename__ = "orch_workflow_transitions"

    workflow_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orch_workflow_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_step_key: Mapped[str] = mapped_column(String(150), nullable=False)
    to_step_key: Mapped[str] = mapped_column(String(150), nullable=False)
    condition: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "workflow_definition_id",
            "from_step_key",
            "to_step_key",
            "priority",
            name="uq_orch_workflow_transitions_from_to_priority",
        ),
    )


class OrchWorkflowInstance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A running workflow (spec §2.11.10).

    ``source_event_id`` provides idempotency: the same domain event can never
    start two instances (spec §2.11.32 critical test). ``current_step_instance_id``
    is the engine's cursor so transitions resolve from the step just completed.
    """

    __tablename__ = "orch_workflow_instances"

    workflow_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orch_workflow_definitions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agreements.id", ondelete="CASCADE"), nullable=True
    )
    status: Mapped[OrchInstanceStatus] = mapped_column(
        SQLEnum(OrchInstanceStatus, native_enum=False),
        nullable=False,
        default=OrchInstanceStatus.RUNNING,
    )
    context: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    current_step_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrchStepInstance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A concrete step execution within an instance (spec §2.11.24)."""

    __tablename__ = "orch_workflow_step_instances"

    workflow_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orch_workflow_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orch_workflow_step_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    input_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict)
    output_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict)
    error_data: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True)
    current_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    loop_iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrchDependency(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Fan-in/join edge between step instances in a PARALLEL fork."""

    __tablename__ = "orch_workflow_dependencies"

    workflow_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orch_workflow_instances.id", ondelete="CASCADE"), nullable=False
    )
    source_step_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orch_workflow_step_instances.id", ondelete="CASCADE"), nullable=False
    )
    target_step_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orch_workflow_step_instances.id", ondelete="CASCADE"), nullable=False
    )
    dependency_type: Mapped[str] = mapped_column(String(50), nullable=False, default="join")


class OrchTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A human task produced by a TASK/APPROVAL step (spec §2.11.18).

    ``assignee_user_id`` holds the resolved user while ``assignee_rule`` keeps
    the configuration expression that produced it, so step context survives in
    the audit trail. Completion is gated on the assigned user (spec §2.11.18
    authorization / 2.11.67 critical test).
    """

    __tablename__ = "orch_workflow_tasks"

    workflow_step_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orch_workflow_step_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_type: Mapped[str] = mapped_column(String(100), nullable=False, default="TASK")
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assignee_rule: Mapped[str | None] = mapped_column(String(255), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="open")
    context: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrchTimer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A durable delay from a DELAY step (spec §2.11.22/2.11.23).

    The scheduler sweeps PENDING timers whose ``scheduled_at`` has elapsed and
    re-enters the engine. ``config`` records the delay policy that produced it.
    """

    __tablename__ = "orch_workflow_timers"

    workflow_step_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orch_workflow_step_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrchEventWait(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A durable external-event gate from an EVENT_WAIT step (spec §2.11.20).

    ``active`` becomes False once the watched event arrives; the engine then
    completes the step and continues (spec §2.11.27).
    """

    __tablename__ = "orch_workflow_event_waits"

    workflow_step_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orch_workflow_step_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(150), nullable=False)
    aggregate_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    aggregate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    correlation_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    matched_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )


class OrchActionAttempt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One execution attempt of an ACTION step (spec §2.11.28)."""

    __tablename__ = "orch_workflow_action_attempts"

    step_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orch_workflow_step_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    request_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict)
    response_metadata: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "step_instance_id",
            "attempt_number",
            name="uq_orch_workflow_action_attempts_step_attempt",
        ),
    )


class OrchIncident(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A failure requiring human intervention (spec §2.11.30/2.11.33)."""

    __tablename__ = "orch_workflow_incidents"

    workflow_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orch_workflow_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orch_workflow_step_instances.id", ondelete="SET NULL"), nullable=True
    )
    incident_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False, default="high")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="open")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrchWorkflowEventLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Audit/telemetry feed of instance events (spec §2.11.29)."""

    __tablename__ = "orch_workflow_events"

    workflow_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orch_workflow_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(150), nullable=False)
    step_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orch_workflow_step_instances.id", ondelete="CASCADE"), nullable=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict)


class OrchDeadLetter(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A domain event the router failed to route (spec §2.11.31)."""

    __tablename__ = "orch_workflow_dead_letters"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(150), nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="open")