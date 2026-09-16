"""approval_definitions.rules JSON column (dynamic DOA rules)

Revision ID: f1a2b3c4d5e6
Revises: e9f0a1b2c3d4
Create Date: 2026-09-10

Closes schema drift caught by ``alembic check``: the dynamic rules engine
(spec 24.2) stores its condition/action JSON in
``approval_definitions.rules``, but the column only existed in the ORM —
fresh databases and autogenerate probes reported it as missing.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "approval_definitions",
        sa.Column("rules", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("approval_definitions", "rules")
