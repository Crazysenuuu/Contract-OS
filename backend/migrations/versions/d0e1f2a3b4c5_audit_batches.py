"""Audit Merkle batches

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-09-13

Adds audit_batches (spec 1.20.15-16): Merkle-sealed audit chain ranges with
optional external timestamp anchors.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_batches",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("root_hash", sa.String(length=128), nullable=False),
        sa.Column("leaf_count", sa.Integer(), nullable=False),
        sa.Column("first_sequence", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("first_event_id", sa.UUID(), nullable=False),
        sa.Column("last_event_id", sa.UUID(), nullable=False),
        sa.Column(
            "anchor_status", sa.String(length=30), nullable=False,
            server_default="internal_only",
        ),
        sa.Column("anchor_token", sa.Text(), nullable=True),
        sa.Column("anchored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "first_sequence", name="uq_audit_batch_first_seq"),
    )
    op.create_index(op.f("ix_audit_batches_tenant_id"), "audit_batches", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("audit_batches")
