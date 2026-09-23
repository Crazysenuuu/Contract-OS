"""termination settlements (spec 1.17 §15-16)

Revision ID: e3f4a5b6c7d8
Revises: d1e2f3a4b5c6
Create Date: 2026-09-23

Adds the settlement closing out a termination:

- ``termination_settlements``: one per termination proceeding (unique FK,
  RESTRICT). Financial position stored as integer minor units + ISO currency
  code — never floating-point (spec 1.17 §16).
- ``termination_settlement_items``: the generated checklist. Each item is
  either REQUIRED BY THE AGREEMENT (traceable to an obligation row or a
  surviving-clause provision) or a RECOMMENDED operational action
  (spec 1.17 §15 distinction).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "termination_settlements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "termination_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "agreement_terminations.id",
                ondelete="RESTRICT",
                name="termination_settlements_termination_id_fkey",
            ),
            nullable=False,
            unique=True,
        ),
        sa.Column("outstanding_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column(
            "obligations_remaining", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default="pending"
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "settled_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", name="termination_settlements_settled_by_fkey"),
            nullable=True,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "termination_settlement_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "settlement_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "termination_settlements.id",
                ondelete="CASCADE",
                name="termination_settlement_items_settlement_id_fkey",
            ),
            nullable=False,
        ),
        sa.Column("item_key", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column(
            "kind", sa.String(length=30), nullable=False, server_default="recommended"
        ),
        sa.Column(
            "blocks_completion", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="open"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "resolved_by",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", name="termination_settlement_items_resolved_by_fkey"
            ),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "settlement_id",
            "source_type",
            "source_id",
            name="uq_settlement_items_source",
        ),
    )

    op.create_index(
        "ix_termination_settlement_items_settlement_id",
        "termination_settlement_items",
        ["settlement_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_termination_settlement_items_settlement_id",
        table_name="termination_settlement_items",
    )
    op.drop_table("termination_settlement_items")
    op.drop_table("termination_settlements")
