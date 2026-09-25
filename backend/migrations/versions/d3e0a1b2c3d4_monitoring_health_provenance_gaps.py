"""Monitoring gap fixes: credential_status, circuit breaker, version provenance (3.15.26/30/38/59/62)

Revision ID: d3e0a1b2c3d4
Revises: d2e3f4a5b6c7
Create Date: 2026-09-25

- integration_health.credential_status: surfaced on the health row (3.15.26).
- integration_health.circuit_open_until: light circuit breaker state used to
  skip fetches against an unhealthy source (3.15.62).
- monitoring_evaluations.source_version_id: denormalized immutable contract
  version the rule was evaluated against (3.15.59).
- monitoring_evidence.source_version_id: version carried when the evidence was
  minted (3.15.38, 3.15.59).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d3e0a1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "integration_health",
        sa.Column("credential_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "integration_health",
        sa.Column("circuit_open_until", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column(
        "monitoring_evaluations",
        sa.Column("source_version_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_monitoring_evaluations_version",
        "monitoring_evaluations",
        "agreement_versions",
        ["source_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "monitoring_evidence",
        sa.Column("source_version_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_monitoring_evidence_version",
        "monitoring_evidence",
        "agreement_versions",
        ["source_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_monitoring_evidence_version",
        "monitoring_evidence",
        type_="foreignkey",
    )
    op.drop_column("monitoring_evidence", "source_version_id")

    op.drop_constraint(
        "fk_monitoring_evaluations_version",
        "monitoring_evaluations",
        type_="foreignkey",
    )
    op.drop_column("monitoring_evaluations", "source_version_id")

    op.drop_column("integration_health", "circuit_open_until")
    op.drop_column("integration_health", "credential_status")