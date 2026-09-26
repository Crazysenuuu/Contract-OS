"""Ingestion intelligence tables (spec §3.21)

Revision ID: c3d4e5f6a7b9
Revises: b2c3d4e5f6a8
Create Date: 2026-09-26

- extraction_candidates: source-referenced assertions awaiting verification
- extraction_conflicts: competing values for the same field
- ingestion_batches: bulk ingestion with error isolation
- extraction_provenance: per-stage extraction audit trail with hashes
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b9"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts_cols() -> list:
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


def _org_fk(table: str) -> None:
    op.create_foreign_key(
        f"{table}_organization_id_fkey",
        table,
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )


def upgrade() -> None:
    op.create_table(
        "extraction_candidates",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("candidate_type", sa.String(length=40), nullable=False),
        sa.Column("field_key", sa.String(length=120), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("source_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("verified_by", sa.UUID(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_target", sa.String(length=60), nullable=True),
        sa.Column("promoted_target_id", sa.UUID(), nullable=True),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("extraction_candidates_pkey")),
        sa.ForeignKeyConstraint(
            ["verified_by"],
            ["users.id"],
            name=op.f("extraction_candidates_verified_by_fkey"),
        ),
    )
    _org_fk("extraction_candidates")
    op.create_foreign_key(
        "extraction_candidates_document_id_fkey",
        "extraction_candidates",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_extraction_candidates_organization_id"),
        "extraction_candidates",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_extraction_candidates_document_id"),
        "extraction_candidates",
        ["document_id"],
    )
    op.create_index(
        op.f("ix_extraction_candidates_candidate_type"),
        "extraction_candidates",
        ["candidate_type"],
    )

    op.create_table(
        "extraction_conflicts",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("field_key", sa.String(length=120), nullable=False),
        sa.Column("candidate_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("winner_candidate_id", sa.UUID(), nullable=True),
        sa.Column("resolution_rule", sa.String(length=120), nullable=True),
        sa.Column("resolved_by", sa.UUID(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("extraction_conflicts_pkey")),
        sa.ForeignKeyConstraint(
            ["resolved_by"],
            ["users.id"],
            name=op.f("extraction_conflicts_resolved_by_fkey"),
        ),
    )
    _org_fk("extraction_conflicts")
    op.create_foreign_key(
        "extraction_conflicts_document_id_fkey",
        "extraction_conflicts",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_extraction_conflicts_organization_id"),
        "extraction_conflicts",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_extraction_conflicts_document_id"),
        "extraction_conflicts",
        ["document_id"],
    )
    op.create_index(
        op.f("ix_extraction_conflicts_field_key"),
        "extraction_conflicts",
        ["field_key"],
    )

    op.create_table(
        "ingestion_batches",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("total_documents", sa.Integer(), nullable=False),
        sa.Column("processed_documents", sa.Integer(), nullable=False),
        sa.Column("failed_documents", sa.Integer(), nullable=False),
        sa.Column("policy", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("ingestion_batches_pkey")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("ingestion_batches_created_by_fkey")
        ),
    )
    _org_fk("ingestion_batches")
    op.create_index(
        op.f("ix_ingestion_batches_organization_id"),
        "ingestion_batches",
        ["organization_id"],
    )

    op.create_table(
        "extraction_provenance",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=False),
        sa.Column("parser_version", sa.String(length=40), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("extraction_provenance_pkey")),
    )
    _org_fk("extraction_provenance")
    op.create_foreign_key(
        "extraction_provenance_document_id_fkey",
        "extraction_provenance",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_extraction_provenance_organization_id"),
        "extraction_provenance",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_extraction_provenance_document_id"),
        "extraction_provenance",
        ["document_id"],
    )
    op.create_index(
        "ix_extraction_provenance_doc_stage",
        "extraction_provenance",
        ["document_id", "stage"],
    )


def downgrade() -> None:
    op.drop_table("extraction_provenance")
    op.drop_table("ingestion_batches")
    op.drop_table("extraction_conflicts")
    op.drop_table("extraction_candidates")
