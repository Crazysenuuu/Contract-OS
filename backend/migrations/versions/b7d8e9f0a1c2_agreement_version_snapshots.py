"""agreement version snapshots (spec 26/3)

Revision ID: b7d8e9f0a1c2
Revises: e1f2a3b4c5d6
Create Date: 2026-09-21

Adds answer-snapshot support to ``agreement_versions`` so every version is
self-contained and restorable (spec §26 "never overwrite contracts", §3
"editing a draft creates version N+1, never mutates version N").

``data`` stores an immutable copy of the agreement answers at version time;
``note`` is a human-readable label for the version.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d8e9f0a1c2"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agreement_versions",
        sa.Column("data", sa.JSON(), nullable=True),
    )
    op.add_column(
        "agreement_versions",
        sa.Column("note", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agreement_versions", "note")
    op.drop_column("agreement_versions", "data")
