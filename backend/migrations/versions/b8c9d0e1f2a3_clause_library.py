"""Clause library tables

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-13

Adds the clause library (spec 1.8): clauses, immutable clause_versions,
clause_variables, clause_conditions, clause_jurisdictions,
agreement_type_clause_bindings and agreement_version_clauses provenance.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The AI-analysis 'Clause' model previously occupied the 'clauses' table.
    # The approved clause library (spec 1.8) owns that canonical name, so the
    # analysis table is renamed first. Postgres FKs track the table OID, so
    # existing references (clause_similarities, contract_clause_translations)
    # keep working.
    op.execute("ALTER TABLE clauses RENAME TO extracted_clauses")
    # RENAME TO keeps the old constraint/index names (clauses_pkey etc.).
    # They must be renamed too, or creating the new canonical 'clauses'
    # table fails with a duplicate-relation error on the primary key.
    op.execute("ALTER INDEX clauses_pkey RENAME TO extracted_clauses_pkey")

    op.create_table(
        "clauses",
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("key", sa.String(length=150), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "key", name="uq_clause_org_key"),
    )
    op.create_index(op.f("ix_clauses_key"), "clauses", ["key"])
    op.create_index(op.f("ix_clauses_status"), "clauses", ["status"])
    op.create_index(op.f("ix_clauses_organization_id"), "clauses", ["organization_id"])

    op.create_table(
        "clause_versions",
        sa.Column("clause_id", sa.UUID(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.Column("approved_by", sa.UUID(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["clause_id"], ["clauses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("clause_id", "version_number", name="uq_clause_version_number"),
    )
    op.create_index(op.f("ix_clause_versions_clause_id"), "clause_versions", ["clause_id"])
    op.create_index(op.f("ix_clause_versions_status"), "clause_versions", ["status"])

    op.create_table(
        "clause_variables",
        sa.Column("clause_version_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=150), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("data_type", sa.String(length=50), nullable=False),
        sa.Column("source_path", sa.String(length=500), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("configuration", sa.JSON(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["clause_version_id"], ["clause_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("clause_version_id", "key", name="uq_clause_variable"),
    )
    op.create_index(op.f("ix_clause_variables_clause_version_id"), "clause_variables", ["clause_version_id"])

    op.create_table(
        "clause_conditions",
        sa.Column("clause_version_id", sa.UUID(), nullable=False),
        sa.Column("condition", sa.JSON(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["clause_version_id"], ["clause_versions.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_clause_conditions_clause_version_id"), "clause_conditions", ["clause_version_id"])

    op.create_table(
        "clause_jurisdictions",
        sa.Column("clause_version_id", sa.UUID(), nullable=False),
        sa.Column("jurisdiction_id", sa.UUID(), nullable=False),
        sa.Column("applicable", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["clause_version_id"], ["clause_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["jurisdiction_id"], ["jurisdictions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("clause_version_id", "jurisdiction_id", name="uq_clause_jurisdiction"),
    )
    op.create_index(op.f("ix_clause_jurisdictions_clause_version_id"), "clause_jurisdictions", ["clause_version_id"])
    op.create_index(op.f("ix_clause_jurisdictions_jurisdiction_id"), "clause_jurisdictions", ["jurisdiction_id"])

    op.create_table(
        "agreement_type_clause_bindings",
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("agreement_type_id", sa.UUID(), nullable=False),
        sa.Column("clause_id", sa.UUID(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("configuration", sa.JSON(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agreement_type_id"], ["agreement_types.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["clause_id"], ["clauses.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("agreement_type_id", "clause_id", name="uq_agreement_type_clause"),
    )
    op.create_index(op.f("ix_agreement_type_clause_bindings_agreement_type_id"), "agreement_type_clause_bindings", ["agreement_type_id"])
    op.create_index(op.f("ix_agreement_type_clause_bindings_clause_id"), "agreement_type_clause_bindings", ["clause_id"])
    op.create_index(op.f("ix_agreement_type_clause_bindings_organization_id"), "agreement_type_clause_bindings", ["organization_id"])

    op.create_table(
        "agreement_version_clauses",
        sa.Column("agreement_version_id", sa.UUID(), nullable=False),
        sa.Column("clause_id", sa.UUID(), nullable=False),
        sa.Column("clause_version_id", sa.UUID(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["agreement_version_id"], ["agreement_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["clause_id"], ["clauses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["clause_version_id"], ["clause_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "agreement_version_id", "display_order", name="uq_agreement_version_clause_order"
        ),
    )
    op.create_index(
        op.f("ix_agreement_version_clauses_agreement_version_id"), "agreement_version_clauses", ["agreement_version_id"]
    )
    # Explicit composite-named index declared in AgreementVersionClause.__table_args__.
    op.create_index(
        "ix_agreement_version_clauses_version", "agreement_version_clauses", ["agreement_version_id"]
    )


def downgrade() -> None:
    op.drop_table("agreement_version_clauses")
    op.drop_table("agreement_type_clause_bindings")
    op.drop_table("clause_jurisdictions")
    op.drop_table("clause_conditions")
    op.drop_table("clause_variables")
    op.drop_table("clause_versions")
    op.drop_table("clauses")
    op.execute("ALTER TABLE extracted_clauses RENAME TO clauses")
    op.execute("ALTER INDEX extracted_clauses_pkey RENAME TO clauses_pkey")
