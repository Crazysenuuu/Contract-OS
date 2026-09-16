"""SSO connections, SCIM tokens, IdP events

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-13

Enterprise identity (spec 1.21.8-1.21.9): sso_connections,
scim_tokens, identity_provider_events.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts_columns() -> list:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "sso_connections",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("protocol", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("issuer", sa.String(length=500), nullable=True),
        sa.Column("client_id", sa.String(length=255), nullable=True),
        sa.Column("client_secret_ref", sa.String(length=500), nullable=True),
        sa.Column("idp_metadata_url", sa.String(length=500), nullable=True),
        sa.Column("idp_certificate", sa.Text(), nullable=True),
        sa.Column("sp_entity_id", sa.String(length=500), nullable=True),
        sa.Column("acs_url", sa.String(length=500), nullable=True),
        sa.Column("domains", sa.JSON(), nullable=True),
        sa.Column("default_role", sa.String(length=100), nullable=True),
        sa.Column("enforce_sso", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_ts_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_sso_connections_organization_id"), "sso_connections", ["organization_id"])

    op.create_table(
        "scim_tokens",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_scim_tokens_organization_id"), "scim_tokens", ["organization_id"])
    op.create_index(op.f("ix_scim_tokens_token_hash"), "scim_tokens", ["token_hash"])

    op.create_table(
        "identity_provider_events",
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("idp_user_id", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("succeeded", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("detail", sa.JSON(), nullable=True),
        *_ts_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_identity_provider_events_organization_id"), "identity_provider_events", ["organization_id"])
    op.create_index(op.f("ix_identity_provider_events_idp_user_id"), "identity_provider_events", ["idp_user_id"])
    op.create_index(
        "ix_idp_events_org_type", "identity_provider_events", ["organization_id", "event_type"]
    )


def downgrade() -> None:
    op.drop_table("identity_provider_events")
    op.drop_table("scim_tokens")
    op.drop_table("sso_connections")
