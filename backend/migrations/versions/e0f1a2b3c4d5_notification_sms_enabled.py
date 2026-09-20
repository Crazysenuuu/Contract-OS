"""Add sms_enabled to notification_preferences (spec §44 SMS channel).

Opt-in master switch: SMS costs money and requires a registered phone,
so it defaults off. Delivery still checks gateway credentials and the
user's phone number independently.
"""

from alembic import op
import sqlalchemy as sa

revision = "e0f1a2b3c4d5"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_preferences",
        sa.Column(
            "sms_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("notification_preferences", "sms_enabled")
