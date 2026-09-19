"""Answer provenance (spec §70) and synthetic-data flag (spec §72)

Revision ID: a9b8c7d6e5f4
Revises: 3374fdfaa40d
Create Date: 2026-09-19

- agreements.answer_provenance: JSON map answer_key -> AnswerSource so the
  system can tell user facts from assumptions.
- agreements.is_test_data: fixture / demo agreements are flagged so
  production analytics, obligations and AI exclude them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, Sequence[str], None] = "3374fdfaa40d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agreements",
        sa.Column("answer_provenance", sa.JSON(), nullable=True),
    )
    op.add_column(
        "agreements",
        sa.Column(
            "is_test_data",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_agreements_is_test_data",
        "agreements",
        ["is_test_data"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agreements_is_test_data", table_name="agreements")
    op.drop_column("agreements", "is_test_data")
    op.drop_column("agreements", "answer_provenance")
