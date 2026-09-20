"""Add users.phone for the SMS notification channel (spec §44).

Optional nullable column — users without a registered number are skipped
by SMS sends. Length 32 covers E.164 (max 15 digits + '+' + headroom).
"""

from alembic import op
import sqlalchemy as sa

revision = "d9e0f1a2b3c4"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "phone")
