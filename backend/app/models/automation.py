"""Automation rule models (spec §3.19.6-14, §3.19.26).

Rules are data, not code: an event trigger, a declarative condition tree and
an action allowlist key. Rules are versioned — every execution records the
rule version that produced it — and the condition engine evaluates a closed
DSL (never arbitrary expressions, §3.19.10).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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


class AutomationRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A policy-driven automation rule (§3.19.6).

    ``trigger_event`` selects the domain event; ``conditions`` is a nested
    DSL tree evaluated against the event context; ``action_key`` must be in
    the orchestrator's action allowlist.
    """

    __tablename__ = "automation_rules"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # e.g. 'agreement.status_changed', 'obligation.overdue', 'monitoring.failed'
    trigger_event: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Condition DSL tree (§3.19.9-11): {"all": [...]}, {"any": [...]},
    # {"field": "...", "op": "eq|ne|gt|lt|in|contains", "value": ...}
    conditions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Action from the orchestrator registry (allowlist enforcement upstream).
    action_key: Mapped[str] = mapped_column(String(60), nullable=False)

    action_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # 'org' | 'system' — system rules are seeded platform defaults that
    # organizations may disable but not edit (§3.19.67-68).
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="org")

    # Rate limiting (§3.19.39): max executions per hour, null = unbounded.
    max_executions_per_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    executions = relationship(
        "AutomationExecution",
        back_populates="rule",
        cascade="all, delete-orphan",
    )


class AutomationExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One matched execution of a rule (§3.19.15-16).

    Idempotent per (rule, event): a replayed event does not re-fire. The
    rule_version snapshot keeps history explainable after rules change.
    """

    __tablename__ = "automation_executions"

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)

    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    event_aggregate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    # 'matched' | 'executed' | 'failed' | 'skipped_rate_limit'
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    rule = relationship("AutomationRule", back_populates="executions")

    __table_args__ = (
        Index(
            "uq_automation_exec_rule_event",
            "rule_id",
            "event_type",
            "event_aggregate_id",
            unique=False,  # dedup enforced in service for multi-fire events
        ),
    )


class HumanCheckpoint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A human decision point inside an automation chain (§3.19.25-27).

    Automation pauses here; a permitted user resolves with a decision and
    optional payload, which routes the chain onward. Also surfaced in the
    Action Center.
    """

    __tablename__ = "automation_checkpoints"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Optional link to an orchestration step instance when embedded in a
    # workflow; standalone rules may create free-standing checkpoints.
    workflow_step_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orch_workflow_step_instances.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 'pending' | 'approved' | 'rejected' | 'cancelled'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    required_permission: Mapped[str | None] = mapped_column(String(100), nullable=True)

    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )

    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
