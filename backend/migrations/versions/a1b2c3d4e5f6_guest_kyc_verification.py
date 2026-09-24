"""Guest KYC provider verification (spec 24.4)

Revision ID: a1b2c3d4e5f6
Revises: f9a0b1c2d3e4
Create Date: 2026-09-24

Adds guest-provider identity verification:

- external_parties: requires_kyc flag plus the active provider session
  (kyc_provider, kyc_session_id, kyc_session_status, kyc_session_url,
  kyc_session_expires_at). The party is marked id_verified_at only when the
  provider reports 'verified' (completion endpoint or webhook), never on
  session creation.
- kyc_verification_attempts: one row per provider session attempt, the
  audit journal of starts, declines and completions. Outcomes and coarse
  check metadata only — never document images or document numbers (the
  provider retains and redacts those).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f9a0b1c2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "external_parties",
        sa.Column(
            "requires_kyc", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "external_parties",
        sa.Column("kyc_provider", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "external_parties",
        sa.Column("kyc_session_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "external_parties",
        sa.Column("kyc_session_status", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "external_parties",
        sa.Column("kyc_session_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "external_parties",
        sa.Column(
            "kyc_session_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_index(
        "ix_external_parties_kyc_session_id",
        "external_parties",
        ["kyc_session_id"],
    )

    op.create_table(
        "kyc_verification_attempts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("external_party_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["external_party_id"],
            ["external_parties.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_kyc_verification_attempts_external_party_id",
        "kyc_verification_attempts",
        ["external_party_id"],
    )
    op.create_index(
        "ix_kyc_attempts_party_session",
        "kyc_verification_attempts",
        ["external_party_id", "session_id"],
    )


def downgrade() -> None:
    op.drop_table("kyc_verification_attempts")
    op.drop_index(
        "ix_external_parties_kyc_session_id", table_name="external_parties"
    )
    op.drop_column("external_parties", "kyc_session_expires_at")
    op.drop_column("external_parties", "kyc_session_url")
    op.drop_column("external_parties", "kyc_session_status")
    op.drop_column("external_parties", "kyc_session_id")
    op.drop_column("external_parties", "kyc_provider")
    op.drop_column("external_parties", "requires_kyc")
