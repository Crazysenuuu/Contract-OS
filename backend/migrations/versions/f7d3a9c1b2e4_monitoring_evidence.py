"""monitoring_evidence

Spec 3.15.37-38 traceable evidence chain: every monitoring evaluation is
backed by evidence records (source, source identifier, observed at/received
at, payload hash, integration + run provenance) so the chain External
Observation -> Evidence Record -> Obligation stays reconstructable and
tamper-evident.

Revision ID: f7d3a9c1b2e4
Revises: e35a792ca663
Create Date: 2026-09-25

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f7d3a9c1b2e4"
down_revision: Union[str, Sequence[str], None] = "e35a792ca663"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "monitoring_evidence",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("monitoring_id", sa.UUID(), nullable=False),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("monitoring_run_id", sa.UUID(), nullable=True),
        sa.Column("obligation_id", sa.UUID(), nullable=True),
        sa.Column("evidence_type", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("source_identifier", sa.String(length=500), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attached_to_obligation", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_connections.id"], name=op.f("monitoring_evidence_integration_id_fkey"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["monitoring_id"], ["obligation_monitoring.id"], name=op.f("monitoring_evidence_monitoring_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["monitoring_run_id"], ["monitoring_runs.id"], name=op.f("monitoring_evidence_monitoring_run_id_fkey"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["obligation_id"], ["obligations.id"], name=op.f("monitoring_evidence_obligation_id_fkey"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("monitoring_evidence_organization_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("monitoring_evidence_pkey")),
    )
    op.create_index("ix_monitoring_evidence_lookup", "monitoring_evidence", ["organization_id", "monitoring_id", "received_at"], unique=False)
    op.create_index(op.f("ix_monitoring_evidence_integration_id"), "monitoring_evidence", ["integration_id"], unique=False)
    op.create_index(op.f("ix_monitoring_evidence_monitoring_id"), "monitoring_evidence", ["monitoring_id"], unique=False)
    op.create_index(op.f("ix_monitoring_evidence_monitoring_run_id"), "monitoring_evidence", ["monitoring_run_id"], unique=False)
    op.create_index(op.f("ix_monitoring_evidence_obligation_id"), "monitoring_evidence", ["obligation_id"], unique=False)
    op.create_index(op.f("ix_monitoring_evidence_organization_id"), "monitoring_evidence", ["organization_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_monitoring_evidence_organization_id"), table_name="monitoring_evidence")
    op.drop_index(op.f("ix_monitoring_evidence_obligation_id"), table_name="monitoring_evidence")
    op.drop_index(op.f("ix_monitoring_evidence_monitoring_run_id"), table_name="monitoring_evidence")
    op.drop_index(op.f("ix_monitoring_evidence_monitoring_id"), table_name="monitoring_evidence")
    op.drop_index(op.f("ix_monitoring_evidence_integration_id"), table_name="monitoring_evidence")
    op.drop_index("ix_monitoring_evidence_lookup", table_name="monitoring_evidence")
    op.drop_table("monitoring_evidence")