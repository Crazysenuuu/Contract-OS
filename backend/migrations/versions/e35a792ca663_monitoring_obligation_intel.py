"""monitoring_obligation_intel

Spec 3.15 Contract Obligation Intelligence & Automated Monitoring:
integration sources, hashed external observations, obligation monitoring
rules, evaluation history, operational exceptions, idempotent scheduler
runs, verified webhook events. Plus the four monitoring permission keys.

Revision ID: e35a792ca663
Revises: 0a9f8e7d6c5b
Create Date: 2026-09-25

"""

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e35a792ca663"
down_revision: Union[str, Sequence[str], None] = "0a9f8e7d6c5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MONITORING_PERMISSIONS = [
    ("monitoring.manage", "Configure monitoring integrations and credentials"),
    ("monitoring.manage_rules", "Create, edit, pause and delete obligation monitoring rules"),
    ("monitoring.view", "View monitoring definitions, health and evaluation history"),
    ("monitoring.view_data", "View external observations and evidence attached to monitorings"),
]


def _seed_permissions() -> None:
    """Idempotent permission catalog backfill (mirrors seed_data.py)."""
    conn = op.get_bind()
    for key, description in MONITORING_PERMISSIONS:
        conn.execute(
            sa.text(
                "INSERT INTO permissions (id, key, description) "
                "VALUES (:id, :key, :description) "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, f"permission:{key}"),
                "key": key,
                "description": description,
            },
        )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "integration_connections",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("integration_type", sa.String(length=64), nullable=False),
        sa.Column("provider_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("configuration", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_by_name", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("integration_connections_created_by_fkey")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("integration_connections_organization_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("integration_connections_pkey")),
    )
    op.create_index("ix_integration_connections_org_status", "integration_connections", ["organization_id", "status"], unique=False)
    op.create_index(op.f("ix_integration_connections_organization_id"), "integration_connections", ["organization_id"], unique=False)

    op.create_table(
        "integration_credentials",
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("secret_reference", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_connections.id"], name=op.f("integration_credentials_integration_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("integration_credentials_pkey")),
    )
    op.create_index(op.f("ix_integration_credentials_integration_id"), "integration_credentials", ["integration_id"], unique=False)

    op.create_table(
        "integration_health",
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("last_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_connections.id"], name=op.f("integration_health_integration_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("integration_id", name=op.f("integration_health_pkey")),
    )

    op.create_table(
        "monitoring_webhook_events",
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=True),
        sa.Column("signature", sa.String(length=256), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_connections.id"], name=op.f("monitoring_webhook_events_integration_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("monitoring_webhook_events_organization_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("monitoring_webhook_events_pkey")),
        sa.UniqueConstraint("integration_id", "provider_event_id", "payload_hash", name="uq_monitoring_webhook_dedup"),
    )
    op.create_index(op.f("ix_monitoring_webhook_events_integration_id"), "monitoring_webhook_events", ["integration_id"], unique=False)
    op.create_index(op.f("ix_monitoring_webhook_events_organization_id"), "monitoring_webhook_events", ["organization_id"], unique=False)

    op.create_table(
        "obligation_monitoring",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("obligation_id", sa.UUID(), nullable=False),
        sa.Column("source_version_id", sa.UUID(), nullable=False),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("pause_reason", sa.String(length=50), nullable=True),
        sa.Column("query_definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evaluation_definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schedule_definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("automation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", sa.String(length=16), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("obligation_monitoring_created_by_fkey")),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_connections.id"], name=op.f("obligation_monitoring_integration_id_fkey"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["obligation_id"], ["obligations.id"], name=op.f("obligation_monitoring_obligation_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("obligation_monitoring_organization_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_version_id"], ["agreement_versions.id"], name=op.f("obligation_monitoring_source_version_id_fkey"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("obligation_monitoring_pkey")),
    )
    op.create_index("ix_obligation_monitoring_due", "obligation_monitoring", ["status", "next_run_at"], unique=False)
    op.create_index("ix_obligation_monitoring_obligation", "obligation_monitoring", ["obligation_id", "status"], unique=False)
    op.create_index(op.f("ix_obligation_monitoring_obligation_id"), "obligation_monitoring", ["obligation_id"], unique=False)
    op.create_index(op.f("ix_obligation_monitoring_organization_id"), "obligation_monitoring", ["organization_id"], unique=False)

    op.create_table(
        "external_observations",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("monitoring_id", sa.UUID(), nullable=False),
        sa.Column("external_id", sa.String(length=500), nullable=False),
        sa.Column("resource_type", sa.String(length=255), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_reference", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_connections.id"], name=op.f("external_observations_integration_id_fkey"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["monitoring_id"], ["obligation_monitoring.id"], name=op.f("external_observations_monitoring_id_fkey"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("external_observations_organization_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("external_observations_pkey")),
        sa.UniqueConstraint("integration_id", "monitoring_id", "external_id", "observed_at", "payload_hash", name="uq_external_observations_dedup"),
    )
    op.create_index("ix_external_observations_lookup", "external_observations", ["organization_id", "monitoring_id", "observed_at"], unique=False)
    op.create_index(op.f("ix_external_observations_monitoring_id"), "external_observations", ["monitoring_id"], unique=False)
    op.create_index(op.f("ix_external_observations_organization_id"), "external_observations", ["organization_id"], unique=False)

    op.create_table(
        "monitoring_runs",
        sa.Column("monitoring_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scheduled_period", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["monitoring_id"], ["obligation_monitoring.id"], name=op.f("monitoring_runs_monitoring_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("monitoring_runs_organization_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("monitoring_runs_pkey")),
        sa.UniqueConstraint("idempotency_key", name=op.f("monitoring_runs_idempotency_key_key")),
    )
    op.create_index(op.f("ix_monitoring_runs_monitoring_id"), "monitoring_runs", ["monitoring_id"], unique=False)
    op.create_index(op.f("ix_monitoring_runs_organization_id"), "monitoring_runs", ["organization_id"], unique=False)

    op.create_table(
        "monitoring_evaluations",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("monitoring_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("obligation_instance_id", sa.UUID(), nullable=True),
        sa.Column("evaluation_definition_hash", sa.String(length=64), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("observation_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["monitoring_id"], ["obligation_monitoring.id"], name=op.f("monitoring_evaluations_monitoring_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("monitoring_evaluations_organization_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["monitoring_runs.id"], name=op.f("monitoring_evaluations_run_id_fkey"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("monitoring_evaluations_pkey")),
    )
    op.create_index("ix_monitoring_evaluations_lookup", "monitoring_evaluations", ["monitoring_id", "evaluated_at"], unique=False)
    op.create_index(op.f("ix_monitoring_evaluations_monitoring_id"), "monitoring_evaluations", ["monitoring_id"], unique=False)
    op.create_index(op.f("ix_monitoring_evaluations_organization_id"), "monitoring_evaluations", ["organization_id"], unique=False)

    op.create_table(
        "monitoring_exceptions",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("monitoring_id", sa.UUID(), nullable=False),
        sa.Column("evaluation_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resolved_by", sa.UUID(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_comment", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["evaluation_id"], ["monitoring_evaluations.id"], name=op.f("monitoring_exceptions_evaluation_id_fkey"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["monitoring_id"], ["obligation_monitoring.id"], name=op.f("monitoring_exceptions_monitoring_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("monitoring_exceptions_organization_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], name=op.f("monitoring_exceptions_resolved_by_fkey")),
        sa.PrimaryKeyConstraint("id", name=op.f("monitoring_exceptions_pkey")),
    )
    op.create_index(op.f("ix_monitoring_exceptions_organization_id"), "monitoring_exceptions", ["organization_id"], unique=False)
    op.create_index("ix_monitoring_exceptions_status", "monitoring_exceptions", ["status", "created_at"], unique=False)

    _seed_permissions()


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_monitoring_exceptions_status", table_name="monitoring_exceptions")
    op.drop_index(op.f("ix_monitoring_exceptions_organization_id"), table_name="monitoring_exceptions")
    op.drop_table("monitoring_exceptions")
    op.drop_index("ix_monitoring_evaluations_lookup", table_name="monitoring_evaluations")
    op.drop_index(op.f("ix_monitoring_evaluations_monitoring_id"), table_name="monitoring_evaluations")
    op.drop_index(op.f("ix_monitoring_evaluations_organization_id"), table_name="monitoring_evaluations")
    op.drop_table("monitoring_evaluations")
    op.drop_index(op.f("ix_monitoring_runs_monitoring_id"), table_name="monitoring_runs")
    op.drop_index(op.f("ix_monitoring_runs_organization_id"), table_name="monitoring_runs")
    op.drop_table("monitoring_runs")
    op.drop_index("ix_external_observations_lookup", table_name="external_observations")
    op.drop_index(op.f("ix_external_observations_monitoring_id"), table_name="external_observations")
    op.drop_index(op.f("ix_external_observations_organization_id"), table_name="external_observations")
    op.drop_table("external_observations")
    op.drop_index(op.f("ix_obligation_monitoring_organization_id"), table_name="obligation_monitoring")
    op.drop_index(op.f("ix_obligation_monitoring_obligation_id"), table_name="obligation_monitoring")
    op.drop_index("ix_obligation_monitoring_obligation", table_name="obligation_monitoring")
    op.drop_index("ix_obligation_monitoring_due", table_name="obligation_monitoring")
    op.drop_table("obligation_monitoring")
    op.drop_index(op.f("ix_monitoring_webhook_events_organization_id"), table_name="monitoring_webhook_events")
    op.drop_index(op.f("ix_monitoring_webhook_events_integration_id"), table_name="monitoring_webhook_events")
    op.drop_table("monitoring_webhook_events")
    op.drop_table("integration_health")
    op.drop_index(op.f("ix_integration_credentials_integration_id"), table_name="integration_credentials")
    op.drop_table("integration_credentials")
    op.drop_index("ix_integration_connections_org_status", table_name="integration_connections")
    op.drop_index(op.f("ix_integration_connections_organization_id"), table_name="integration_connections")
    op.drop_table("integration_connections")

    conn = op.get_bind()
    for key, _description in MONITORING_PERMISSIONS:
        conn.execute(
            sa.text(
                "DELETE FROM permissions WHERE key = :key"
            ),
            {"key": key},
        )