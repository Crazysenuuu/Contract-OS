"""DMCA notice intake: add dmca_notices table

Revision ID: e7b9a1c4d5f6
Revises: d5a6f8e1c2b3
Create Date: 2026-09-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e7b9a1c4d5f6'
down_revision: Union[str, Sequence[str], None] = 'd5a6f8e1c2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create dmca_notices for § 512 notice tracking."""
    op.create_table(
        'dmca_notices',
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('reporter_name', sa.String(length=255), nullable=False),
        sa.Column('reporter_email', sa.String(length=320), nullable=False),
        sa.Column('work_description', sa.Text(), nullable=False),
        sa.Column('material_location', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('admin_note', sa.Text(), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("kind IN ('takedown', 'counter')", name='ck_dmca_notices_kind'),
        sa.CheckConstraint(
            "status IN ('received', 'action_taken', 'rejected', 'restored')",
            name='ck_dmca_notices_status',
        ),
    )
    op.create_index(
        'ix_dmca_notices_status', 'dmca_notices', ['status'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_dmca_notices_status', table_name='dmca_notices')
    op.drop_table('dmca_notices')
