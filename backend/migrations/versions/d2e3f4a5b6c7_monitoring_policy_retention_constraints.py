"""Monitoring workspace policy, retention/redaction, DB constraints (3.15.36/45/56)

Revision ID: d2e3f4a5b6c7
Revises: f7d3a9c1b2e4
Create Date: 2026-09-25

- monitoring_workspace_policies: workspace-selected automation actions
  (3.15.36). No row = every on_pass action permitted (configurable, not a
  hardcoded default).
- obligation_monitoring.retention_days / redact_fields: rule-scoped external
  data retention and declared-field redaction before storage (3.15.45).
- Composite unique constraints (organization_id, id) on
  integration_connections and obligation_monitoring, plus composite foreign
  keys from every tenant-scoped child so a monitoring rule can never be
  bound to an integration belonging to another organization (3.15.56).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "f7d3a9c1b2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "monitoring_workspace_policies",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "allowed_actions",
            postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("organization_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
    )

    op.add_column(
        "obligation_monitoring",
        sa.Column("retention_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "obligation_monitoring",
        sa.Column("redact_fields", postgresql.JSONB(), nullable=True),
    )

    op.create_unique_constraint(
        "uq_integration_connections_org_id",
        "integration_connections",
        ["organization_id", "id"],
    )
    op.create_unique_constraint(
        "uq_obligation_monitoring_org_id",
        "obligation_monitoring",
        ["organization_id", "id"],
    )

    op.create_foreign_key(
        "fk_obligation_monitoring_integration_org",
        "obligation_monitoring",
        "integration_connections",
        ["organization_id", "integration_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_external_observations_integration_org",
        "external_observations",
        "integration_connections",
        ["organization_id", "integration_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_external_observations_monitoring_org",
        "external_observations",
        "obligation_monitoring",
        ["organization_id", "monitoring_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_monitoring_evaluations_monitoring_org",
        "monitoring_evaluations",
        "obligation_monitoring",
        ["organization_id", "monitoring_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_monitoring_exceptions_monitoring_org",
        "monitoring_exceptions",
        "obligation_monitoring",
        ["organization_id", "monitoring_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_monitoring_runs_monitoring_org",
        "monitoring_runs",
        "obligation_monitoring",
        ["organization_id", "monitoring_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_monitoring_evidence_monitoring_org",
        "monitoring_evidence",
        "obligation_monitoring",
        ["organization_id", "monitoring_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_monitoring_evidence_integration_org",
        "monitoring_evidence",
        "integration_connections",
        ["organization_id", "integration_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_monitoring_webhook_events_integration_org",
        "monitoring_webhook_events",
        "integration_connections",
        ["organization_id", "integration_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_monitoring_webhook_events_integration_org",
        "monitoring_webhook_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_monitoring_evidence_integration_org",
        "monitoring_evidence",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_monitoring_evidence_monitoring_org",
        "monitoring_evidence",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_monitoring_runs_monitoring_org",
        "monitoring_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_monitoring_exceptions_monitoring_org",
        "monitoring_exceptions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_monitoring_evaluations_monitoring_org",
        "monitoring_evaluations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_external_observations_monitoring_org",
        "external_observations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_external_observations_integration_org",
        "external_observations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_obligation_monitoring_integration_org",
        "obligation_monitoring",
        type_="foreignkey",
    )

    op.drop_constraint(
        "uq_obligation_monitoring_org_id",
        "obligation_monitoring",
        type_="unique",
    )
    op.drop_constraint(
        "uq_integration_connections_org_id",
        "integration_connections",
        type_="unique",
    )

    op.drop_column("obligation_monitoring", "redact_fields")
    op.drop_column("obligation_monitoring", "retention_days")

    op.drop_table("monitoring_workspace_policies")