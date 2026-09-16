"""Retention, legal hold, repository + clause lifecycle governance

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-09-08

Adds:
  - retention_policies, retention_records, legal_holds, repository_records
    (spec 1.22 / 2.07)
  - clause_library.lifecycle_status / published_by / published_at /
    deprecation_reason (spec 24.8)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "retention_policies",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scope", sa.String(length=30), nullable=False),
        sa.Column("agreement_type_key", sa.String(length=100), nullable=True),
        sa.Column("retention_months", sa.Integer(), nullable=False),
        sa.Column("disposition", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_retention_policies_organization_id", "retention_policies", ["organization_id"]
    )

    op.create_table(
        "retention_records",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=False),
        sa.Column("policy_id", sa.UUID(), nullable=True),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("anchor_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
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
        sa.ForeignKeyConstraint(["agreement_id"], ["agreements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["retention_policies.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agreement_id", name="uq_retention_records_agreement"),
    )
    op.create_index(
        "ix_retention_records_organization_id", "retention_records", ["organization_id"]
    )
    op.create_index(
        "ix_retention_records_retention_until", "retention_records", ["retention_until"]
    )

    op.create_table(
        "legal_holds",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("hold_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("placed_by", sa.UUID(), nullable=True),
        sa.Column("released_by", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["agreement_id"], ["agreements.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["placed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["released_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_legal_holds_organization_id", "legal_holds", ["organization_id"])
    op.create_index("ix_legal_holds_agreement_id", "legal_holds", ["agreement_id"])

    op.create_table(
        "repository_records",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=True),
        sa.Column("document_type", sa.String(length=50), nullable=False),
        sa.Column("content_ref", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("classification", sa.String(length=50), nullable=False),
        sa.Column("is_executed", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["agreement_id"], ["agreements.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["agreement_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agreement_id",
            "version_id",
            "document_type",
            name="uq_repository_agreement_version_type",
        ),
    )
    op.create_index(
        "ix_repository_records_organization_id", "repository_records", ["organization_id"]
    )
    op.create_index(
        "ix_repository_records_agreement_id", "repository_records", ["agreement_id"]
    )

    # Clause lifecycle governance columns (spec 24.8)
    op.add_column(
        "clause_library",
        sa.Column("lifecycle_status", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "clause_library",
        sa.Column("published_by", sa.UUID(), nullable=True),
    )
    op.add_column(
        "clause_library",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "clause_library",
        sa.Column("deprecation_reason", sa.Text(), nullable=True),
    )
    op.execute(
        "UPDATE clause_library SET lifecycle_status = 'active' "
        "WHERE lifecycle_status IS NULL"
    )
    op.alter_column("clause_library", "lifecycle_status", nullable=False)


def downgrade() -> None:
    op.drop_column("clause_library", "deprecation_reason")
    op.drop_column("clause_library", "published_at")
    op.drop_column("clause_library", "published_by")
    op.drop_column("clause_library", "lifecycle_status")

    op.drop_index("ix_repository_records_agreement_id", table_name="repository_records")
    op.drop_index(
        "ix_repository_records_organization_id", table_name="repository_records"
    )
    op.drop_table("repository_records")

    op.drop_index("ix_legal_holds_agreement_id", table_name="legal_holds")
    op.drop_index("ix_legal_holds_organization_id", table_name="legal_holds")
    op.drop_table("legal_holds")

    op.drop_index(
        "ix_retention_records_retention_until", table_name="retention_records"
    )
    op.drop_index(
        "ix_retention_records_organization_id", table_name="retention_records"
    )
    op.drop_table("retention_records")

    op.drop_index(
        "ix_retention_policies_organization_id", table_name="retention_policies"
    )
    op.drop_table("retention_policies")