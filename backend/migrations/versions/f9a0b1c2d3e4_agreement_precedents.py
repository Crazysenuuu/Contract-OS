"""Precedent retrieval: cross-contract precedent links (spec 2.10.26-28)

Revision ID: f9a0b1c2d3e4
Revises: e3f4a5b6c7d8
Create Date: 2026-09-24

Adds agreement_precedents, the record of indexed passages (knowledge
chunks) from one agreement that informed another. Suggestions are recorded
with origin='retrieval'; explicit pins use origin='user' with status
'pinned'. Access is always re-verified at query time from the underlying
chunks, so rows grant nothing by themselves (2.10.28).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agreement_precedents",
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
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("source_agreement_id", sa.UUID(), nullable=False),
        sa.Column("source_version_id", sa.UUID(), nullable=True),
        sa.Column("source_chunk_id", sa.UUID(), nullable=True),
        sa.Column("target_agreement_id", sa.UUID(), nullable=False),
        sa.Column("target_clause_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="suggested"),
        sa.Column("origin", sa.String(length=30), nullable=False, server_default="retrieval"),
        sa.Column("score", sa.Numeric(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_agreement_id"], ["agreements.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_agreement_id"], ["agreements.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_chunk_id"], ["knowledge_chunks.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "source_agreement_id",
            "source_chunk_id",
            "target_agreement_id",
            "target_clause_id",
            "origin",
            name="uq_agreement_precedent_link",
        ),
    )
    op.create_index(
        "ix_agreement_precedents_organization_id",
        "agreement_precedents",
        ["organization_id"],
    )
    op.create_index(
        "ix_agreement_precedents_source_agreement_id",
        "agreement_precedents",
        ["source_agreement_id"],
    )
    op.create_index(
        "ix_agreement_precedents_source_version_id",
        "agreement_precedents",
        ["source_version_id"],
    )
    op.create_index(
        "ix_agreement_precedents_target_agreement_id",
        "agreement_precedents",
        ["target_agreement_id"],
    )
    op.create_index(
        "ix_agreement_precedents_target_clause_id",
        "agreement_precedents",
        ["target_clause_id"],
    )
    op.create_index(
        "ix_agreement_precedents_target",
        "agreement_precedents",
        ["target_agreement_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agreement_precedents_target", table_name="agreement_precedents"
    )
    op.drop_index(
        "ix_agreement_precedents_target_clause_id", table_name="agreement_precedents"
    )
    op.drop_index(
        "ix_agreement_precedents_target_agreement_id", table_name="agreement_precedents"
    )
    op.drop_index(
        "ix_agreement_precedents_source_version_id", table_name="agreement_precedents"
    )
    op.drop_index(
        "ix_agreement_precedents_source_agreement_id", table_name="agreement_precedents"
    )
    op.drop_index(
        "ix_agreement_precedents_organization_id", table_name="agreement_precedents"
    )
    op.drop_table("agreement_precedents")
