"""workflow orchestration tables (spec 2.11)

Revision ID: e1f2a3b4c5d6
Revises: b2c3d4e5f6a7
Create Date: 2026-09-21

Creates the config-driven workflow orchestrator (spec 2.11) tables.

Table naming: the §1.13 agreement state machine already owns
``workflow_definitions`` / ``workflow_transitions`` / ``workflow_instances``
with different semantics, so every orchestrator table is prefixed ``orch_``.
Spec 2.11.68 table-name mapping:

    workflow_definitions         -> orch_workflow_definitions
    workflow_step_definitions    -> orch_workflow_step_definitions
    workflow_transitions         -> orch_workflow_transitions
    workflow_instances           -> orch_workflow_instances
    workflow_step_instances      -> orch_workflow_step_instances
    workflow_dependencies        -> orch_workflow_dependencies
    workflow_tasks               -> orch_workflow_tasks
    workflow_timers              -> orch_workflow_timers
    workflow_event_waits         -> orch_workflow_event_waits
    workflow_action_attempts     -> orch_workflow_action_attempts
    workflow_incidents           -> orch_workflow_incidents
    workflow_events              -> orch_workflow_events
    workflow_dead_letters        -> orch_workflow_dead_letters

Enum statuses are stored as VARCHAR with CHECK constraints, matching the ORM
(``native_enum=False``) so no PostgreSQL enum types are created and the schema
stays portable to the SQLite test rig.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
DT = sa.DateTime(timezone=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "orch_workflow_definitions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, nullable=True),
        sa.Column("code", sa.String(length=150), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("scope", sa.String(length=32), nullable=False, server_default="organization"),
        sa.Column("trigger", JSONB, nullable=False),
        sa.Column("configuration", JSONB, nullable=False),
        sa.Column("created_by_user_id", UUID, nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "code", "version", name="uq_orch_workflow_definitions_code_version"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'disabled', 'archived')",
            name="orch_workflow_definitions_status_check",
        ),
        sa.CheckConstraint(
            "scope IN ('global', 'organization', 'agreement_type')",
            name="orch_workflow_definitions_scope_check",
        ),
    )

    op.create_table(
        "orch_workflow_step_definitions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workflow_definition_id", UUID, nullable=False),
        sa.Column("step_key", sa.String(length=150), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "step_type",
            sa.String(length=32),
            nullable=False,
            server_default="task",
        ),
        sa.Column("configuration", JSONB, nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["workflow_definition_id"],
            ["orch_workflow_definitions.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workflow_definition_id",
            "step_key",
            name="uq_orch_workflow_step_definitions_definition_step",
        ),
        sa.CheckConstraint(
            "step_type IN ('task', 'approval', 'condition', 'delay', 'event_wait', "
            "'action', 'parallel', 'subworkflow')",
            name="orch_workflow_step_definitions_step_type_check",
        ),
    )

    op.create_table(
        "orch_workflow_transitions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workflow_definition_id", UUID, nullable=False),
        sa.Column("from_step_key", sa.String(length=150), nullable=False),
        sa.Column("to_step_key", sa.String(length=150), nullable=False),
        sa.Column("condition", JSONB, nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["workflow_definition_id"],
            ["orch_workflow_definitions.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workflow_definition_id",
            "from_step_key",
            "to_step_key",
            "priority",
            name="uq_orch_workflow_transitions_from_to_priority",
        ),
    )

    op.create_table(
        "orch_workflow_instances",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workflow_definition_id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=True),
        sa.Column("agreement_id", UUID, nullable=True),
        sa.Column(
            "status",
            sa.String(length=40),
            nullable=False,
            server_default="running",
        ),
        sa.Column("context", JSONB, nullable=False),
        sa.Column("source_event_id", UUID, nullable=True),
        sa.Column("current_step_instance_id", UUID, nullable=True),
        sa.Column("started_at", DT, nullable=True),
        sa.Column("completed_at", DT, nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["workflow_definition_id"],
            ["orch_workflow_definitions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agreement_id"], ["agreements.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('running', 'waiting', 'completed', 'failed', 'cancelled', "
            "'suspended', 'human_intervention_required')",
            name="orch_workflow_instances_status_check",
        ),
    )
    op.create_index(
        "ix_orch_workflow_instances_workflow_definition_id",
        "orch_workflow_instances",
        ["workflow_definition_id"],
    )
    op.create_index(
        "ix_orch_workflow_instances_source_event_id",
        "orch_workflow_instances",
        ["source_event_id"],
    )

    op.create_table(
        "orch_workflow_step_instances",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workflow_instance_id", UUID, nullable=False),
        sa.Column("step_definition_id", UUID, nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("input_data", JSONB, nullable=False),
        sa.Column("output_data", JSONB, nullable=False),
        sa.Column("error_data", JSONB, nullable=True),
        sa.Column("current_attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("loop_iteration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", DT, nullable=True),
        sa.Column("completed_at", DT, nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["workflow_instance_id"], ["orch_workflow_instances.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["step_definition_id"], ["orch_workflow_step_definitions.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_orch_workflow_step_instances_workflow_instance_id",
        "orch_workflow_step_instances",
        ["workflow_instance_id"],
    )

    op.create_table(
        "orch_workflow_dependencies",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workflow_instance_id", UUID, nullable=False),
        sa.Column("source_step_instance_id", UUID, nullable=False),
        sa.Column("target_step_instance_id", UUID, nullable=False),
        sa.Column("dependency_type", sa.String(length=50), nullable=False, server_default="join"),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["workflow_instance_id"], ["orch_workflow_instances.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_step_instance_id"], ["orch_workflow_step_instances.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_step_instance_id"], ["orch_workflow_step_instances.id"], ondelete="CASCADE"
        ),
    )

    op.create_table(
        "orch_workflow_tasks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workflow_step_instance_id", UUID, nullable=False),
        sa.Column("task_type", sa.String(length=100), nullable=False, server_default="TASK"),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("assignee_user_id", UUID, nullable=True),
        sa.Column("assignee_rule", sa.String(length=255), nullable=True),
        sa.Column("due_at", DT, nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="open"),
        sa.Column("context", JSONB, nullable=False),
        sa.Column("completed_at", DT, nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["workflow_step_instance_id"],
            ["orch_workflow_step_instances.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_orch_workflow_tasks_workflow_step_instance_id",
        "orch_workflow_tasks",
        ["workflow_step_instance_id"],
    )
    op.create_index(
        "ix_orch_workflow_tasks_assignee_user_id", "orch_workflow_tasks", ["assignee_user_id"]
    )

    op.create_table(
        "orch_workflow_timers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workflow_step_instance_id", UUID, nullable=False),
        sa.Column("scheduled_at", DT, nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("config", JSONB, nullable=False),
        sa.Column("fired_at", DT, nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["workflow_step_instance_id"],
            ["orch_workflow_step_instances.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_orch_workflow_timers_workflow_step_instance_id",
        "orch_workflow_timers",
        ["workflow_step_instance_id"],
    )

    op.create_table(
        "orch_workflow_event_waits",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workflow_step_instance_id", UUID, nullable=False),
        sa.Column("event_type", sa.String(length=150), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=True),
        sa.Column("aggregate_id", UUID, nullable=True),
        sa.Column("correlation_key", sa.String(length=255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("matched_at", DT, nullable=True),
        sa.Column("matched_event_id", UUID, nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["workflow_step_instance_id"],
            ["orch_workflow_step_instances.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_orch_workflow_event_waits_workflow_step_instance_id",
        "orch_workflow_event_waits",
        ["workflow_step_instance_id"],
    )
    op.create_index(
        "ix_orch_workflow_event_waits_aggregate_id", "orch_workflow_event_waits", ["aggregate_id"]
    )

    op.create_table(
        "orch_workflow_action_attempts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("step_instance_id", UUID, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("request_metadata", JSONB, nullable=False),
        sa.Column("response_metadata", JSONB, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["step_instance_id"], ["orch_workflow_step_instances.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "step_instance_id",
            "attempt_number",
            name="uq_orch_workflow_action_attempts_step_attempt",
        ),
    )
    op.create_index(
        "ix_orch_workflow_action_attempts_step_instance_id",
        "orch_workflow_action_attempts",
        ["step_instance_id"],
    )

    op.create_table(
        "orch_workflow_incidents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workflow_instance_id", UUID, nullable=False),
        sa.Column("step_instance_id", UUID, nullable=True),
        sa.Column("incident_type", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=50), nullable=False, server_default="high"),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="open"),
        sa.Column("resolved_at", DT, nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["workflow_instance_id"], ["orch_workflow_instances.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["step_instance_id"], ["orch_workflow_step_instances.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_orch_workflow_incidents_workflow_instance_id",
        "orch_workflow_incidents",
        ["workflow_instance_id"],
    )

    op.create_table(
        "orch_workflow_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workflow_instance_id", UUID, nullable=False),
        sa.Column("event_type", sa.String(length=150), nullable=False),
        sa.Column("step_instance_id", UUID, nullable=True),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("data", JSONB, nullable=False),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["workflow_instance_id"], ["orch_workflow_instances.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["step_instance_id"], ["orch_workflow_step_instances.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_orch_workflow_events_workflow_instance_id",
        "orch_workflow_events",
        ["workflow_instance_id"],
    )

    op.create_table(
        "orch_workflow_dead_letters",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("event_type", sa.String(length=150), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="open"),
        sa.Column("created_at", DT, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_orch_workflow_dead_letters_event_id", "orch_workflow_dead_letters", ["event_id"]
    )


def downgrade() -> None:
    op.drop_table("orch_workflow_dead_letters")
    op.drop_table("orch_workflow_events")
    op.drop_table("orch_workflow_incidents")
    op.drop_table("orch_workflow_action_attempts")
    op.drop_table("orch_workflow_event_waits")
    op.drop_table("orch_workflow_timers")
    op.drop_table("orch_workflow_tasks")
    op.drop_table("orch_workflow_dependencies")
    op.drop_table("orch_workflow_step_instances")
    op.drop_table("orch_workflow_instances")
    op.drop_table("orch_workflow_transitions")
    op.drop_table("orch_workflow_step_definitions")
    op.drop_table("orch_workflow_definitions")