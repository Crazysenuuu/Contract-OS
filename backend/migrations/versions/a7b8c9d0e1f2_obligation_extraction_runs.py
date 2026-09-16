"""Obligation extraction runs

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-09-13

Adds obligation_extraction_runs (spec 1.16.21): provenance for AI obligation
extraction - which model, prompt version, source version and content hash
produced each candidate obligation.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "obligation_extraction_runs",
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("agreement_id", sa.UUID(), nullable=False),
        sa.Column("source_version_id", sa.UUID(), nullable=True),
        sa.Column("method", sa.String(length=30), nullable=False, server_default="hybrid"),
        sa.Column("model_id", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=50), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirmed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="COMPLETED"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_json", postgresql.JSONB(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["agreement_id"], ["agreements.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        op.f("ix_obligation_extraction_runs_agreement_id"),
        "obligation_extraction_runs",
        ["agreement_id"],
    )
    op.create_index(
        op.f("ix_obligation_extraction_runs_organization_id"),
        "obligation_extraction_runs",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_obligation_extraction_runs_organization_id"), table_name="obligation_extraction_runs"
    )
    op.drop_index(
        op.f("ix_obligation_extraction_runs_agreement_id"),
        table_name="obligation_extraction_runs",
    )
    op.drop_table("obligation_extraction_runs")
