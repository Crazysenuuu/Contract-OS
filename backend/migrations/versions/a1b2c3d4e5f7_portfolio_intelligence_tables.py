"""Portfolio intelligence tables: metric snapshots, forecasts, scenarios,
action items, external policies (spec §3.17-3.20)

Revision ID: a1b2c3d4e5f7
Revises: 6c8d8f12370c
Create Date: 2026-09-26

Adds the storage foundations for the portfolio-intelligence and external
boundary milestones:
- metric_snapshots / metric_anomalies / executive_insights (§3.17)
- forecast_runs / forecast_predictions / scenario_runs (§3.18)
- action_items, the Action Center (§3.10.13)
- external_workspace_policies / agreement_sharing_policies (§3.20)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, Sequence[str], None] = "6c8d8f12370c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "metric_snapshots",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("metric_key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("is_missing", sa.Integer(), nullable=False),
        sa.Column("missing_reason", sa.String(length=60), nullable=True),
        sa.Column(
            "source_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("metric_snapshots_organization_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("metric_snapshots_pkey")),
        sa.UniqueConstraint(
            "organization_id",
            "snapshot_date",
            "metric_key",
            name="uq_metric_snapshot_org_date_key",
        ),
    )
    op.create_index(
        op.f("ix_metric_snapshots_organization_id"),
        "metric_snapshots",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_metric_snapshots_snapshot_date"),
        "metric_snapshots",
        ["snapshot_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_metric_snapshots_metric_key"),
        "metric_snapshots",
        ["metric_key"],
        unique=False,
    )
    op.create_index(
        "ix_metric_snapshots_org_date",
        "metric_snapshots",
        ["organization_id", "snapshot_date"],
        unique=False,
    )

    op.create_table(
        "metric_anomalies",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("metric_key", sa.String(length=120), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("baseline_mean", sa.Float(), nullable=False),
        sa.Column("baseline_stddev", sa.Float(), nullable=False),
        sa.Column("deviation", sa.Float(), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("metric_anomalies_organization_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("metric_anomalies_pkey")),
        sa.UniqueConstraint(
            "organization_id",
            "metric_key",
            "snapshot_date",
            name="uq_metric_anomaly_org_metric_date",
        ),
    )
    op.create_index(
        op.f("ix_metric_anomalies_organization_id"),
        "metric_anomalies",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_metric_anomalies_metric_key"),
        "metric_anomalies",
        ["metric_key"],
        unique=False,
    )

    op.create_table(
        "executive_insights",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("insight_date", sa.Date(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("generated_by", sa.String(length=10), nullable=False),
        sa.Column(
            "evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("executive_insights_organization_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("executive_insights_pkey")),
    )
    op.create_index(
        op.f("ix_executive_insights_organization_id"),
        "executive_insights",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_executive_insights_insight_date"),
        "executive_insights",
        ["insight_date"],
        unique=False,
    )

    op.create_table(
        "forecast_runs",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("metric_key", sa.String(length=120), nullable=False),
        sa.Column("model_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column(
            "dataset_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("backtest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "baseline_backtest", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("forecast_runs_organization_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("forecast_runs_created_by_fkey")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("forecast_runs_pkey")),
    )
    op.create_index(
        op.f("ix_forecast_runs_organization_id"),
        "forecast_runs",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_forecast_runs_metric_key"),
        "forecast_runs",
        ["metric_key"],
        unique=False,
    )

    op.create_table(
        "forecast_predictions",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("predicted_value", sa.Float(), nullable=False),
        sa.Column("confidence_low", sa.Float(), nullable=True),
        sa.Column("confidence_high", sa.Float(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["forecast_runs.id"],
            name=op.f("forecast_predictions_run_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("forecast_predictions_pkey")),
    )
    op.create_index(
        op.f("ix_forecast_predictions_run_id"),
        "forecast_predictions",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "scenario_runs",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "variables", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "baseline_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("simulation_version", sa.String(length=30), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("scenario_runs_organization_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("scenario_runs_created_by_fkey")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("scenario_runs_pkey")),
    )
    op.create_index(
        op.f("ix_scenario_runs_organization_id"),
        "scenario_runs",
        ["organization_id"],
        unique=False,
    )

    op.create_table(
        "action_items",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("assignee_user_id", sa.UUID(), nullable=True),
        sa.Column("action_type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_system", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("action_url", sa.String(length=500), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("action_items_organization_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assignee_user_id"],
            ["users.id"],
            name=op.f("action_items_assignee_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["completed_by"], ["users.id"], name=op.f("action_items_completed_by_fkey")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("action_items_pkey")),
        sa.UniqueConstraint(
            "source_system",
            "source_id",
            "status",
            name="uq_action_item_source_live",
        ),
    )
    op.create_index(
        op.f("ix_action_items_organization_id"),
        "action_items",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_action_items_assignee_user_id"),
        "action_items",
        ["assignee_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_action_items_action_type"),
        "action_items",
        ["action_type"],
        unique=False,
    )
    op.create_index(
        "ix_action_items_org_status",
        "action_items",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_action_items_assignee_status",
        "action_items",
        ["assignee_user_id", "status"],
        unique=False,
    )

    op.create_table(
        "external_workspace_policies",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("allow_external_access", sa.Boolean(), nullable=False),
        sa.Column("max_link_ttl_days", sa.Integer(), nullable=True),
        sa.Column("required_id_verification", sa.String(length=30), nullable=False),
        sa.Column("external_change_policy", sa.String(length=20), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("external_workspace_policies_organization_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("external_workspace_policies_pkey")),
    )
    op.create_index(
        op.f("ix_external_workspace_policies_organization_id"),
        "external_workspace_policies",
        ["organization_id"],
        unique=True,
    )

    op.create_table(
        "agreement_sharing_policies",
        sa.Column("agreement_id", sa.UUID(), nullable=False),
        sa.Column(
            "shared_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("hide_comments", sa.Boolean(), nullable=False),
        sa.Column("hide_internal_participants", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agreement_id"],
            ["agreements.id"],
            name=op.f("agreement_sharing_policies_agreement_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("agreement_sharing_policies_pkey")),
        sa.UniqueConstraint(
            "agreement_id", name="uq_agreement_sharing_policy_agreement"
        ),
    )
    op.create_index(
        op.f("ix_agreement_sharing_policies_agreement_id"),
        "agreement_sharing_policies",
        ["agreement_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("agreement_sharing_policies")
    op.drop_table("external_workspace_policies")
    op.drop_table("action_items")
    op.drop_table("scenario_runs")
    op.drop_table("forecast_predictions")
    op.drop_table("forecast_runs")
    op.drop_table("executive_insights")
    op.drop_table("metric_anomalies")
    op.drop_table("metric_snapshots")
