"""COPPA age gate: add users.is_adult derived flag

Revision ID: d5a6f8e1c2b3
Revises: c3d4e5f6a7b9
Create Date: 2026-09-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd5a6f8e1c2b3'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the derived is_adult flag (COPPA age gate).

    The raw date of birth is deliberately NOT stored — only the boolean
    result of the 13+ check at signup time.
    """
    op.add_column(
        'users',
        sa.Column(
            'is_adult',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    op.drop_column('users', 'is_adult')
