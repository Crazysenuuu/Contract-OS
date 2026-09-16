"""Guest ID verification fields on external_parties

Revision ID: e9f0a1b2c3d4
Revises: f7a8b9c0d1e2
Create Date: 2026-09-10

Adds the ID-verification columns (spec 24.3): when
``requires_id_verification`` is enabled on an external party, the guest must
complete a verification challenge (email OTP today) before accepting or
signing; ``id_verified_at``/``id_verification_method`` record the outcome.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "external_parties",
        sa.Column(
            "requires_id_verification",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "external_parties",
        sa.Column("id_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "external_parties",
        sa.Column("id_verification_method", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("external_parties", "id_verification_method")
    op.drop_column("external_parties", "id_verified_at")
    op.drop_column("external_parties", "requires_id_verification")
