"""Ingestion, privacy, knowledge, risk graph, integration, DOA tables

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-09-08

Adds tables for:
- OCR/legacy ingestion pipeline (24.1): ingestion_jobs, ocr_documents,
  human_review_tasks
- Data privacy (24.3): field_encryption_records, redaction_requests,
  erasure_requests
- Semantic retrieval (2.10): knowledge_chunks
- Contract Risk Graph (35): risk_graph_nodes, risk_graph_edges
- Integration connectors (24.6): integration_connectors
- DOA execution mode on approval_stages (24.2)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- DOA: parallel/sequential execution on approval stages (24.2) ----
    op.add_column(
        "approval_stages",
        sa.Column(
            "execution_mode",
            sa.String(length=20),
            nullable=False,
            server_default="sequential",
        ),
    )

    # ---- Integration connectors (24.6) ----
    op.create_table(
        "integration_connectors",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("credentials_ref", sa.String(length=500), nullable=True),
        sa.Column("settings", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(length=50), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_integration_connectors_organization_id",
        "integration_connectors",
        ["organization_id"],
    )

    # ---- Ingestion pipeline (24.1) ----
    op.create_table(
        "ingestion_jobs",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("total_files", sa.Integer(), nullable=False),
        sa.Column("completed_files", sa.Integer(), nullable=False),
        sa.Column("failed_files", sa.Integer(), nullable=False),
        sa.Column("target_agreement_type_key", sa.String(length=100), nullable=True),
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
            ["created_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ingestion_jobs_organization_id",
        "ingestion_jobs",
        ["organization_id"],
    )

    op.create_table(
        "ocr_documents",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column("content_ref", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extracted_metadata", sa.JSON(), nullable=True),
        sa.Column("agreement_id", sa.UUID(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
            ["agreement_id"],
            ["agreements.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["ingestion_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ocr_documents_job_id",
        "ocr_documents",
        ["job_id"],
    )
    op.create_index(
        "ix_ocr_documents_organization_id",
        "ocr_documents",
        ["organization_id"],
    )
    op.create_index(
        "ix_ocr_documents_agreement_id",
        "ocr_documents",
        ["agreement_id"],
    )

    op.create_table(
        "human_review_tasks",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("ocr_document_id", sa.UUID(), nullable=False),
        sa.Column("assigned_to", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("corrected_text", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["assigned_to"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ocr_document_id"],
            ["ocr_documents.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_human_review_tasks_ocr_document_id",
        "human_review_tasks",
        ["ocr_document_id"],
    )
    op.create_index(
        "ix_human_review_tasks_organization_id",
        "human_review_tasks",
        ["organization_id"],
    )

    # ---- Data privacy (24.3) ----
    op.create_table(
        "field_encryption_records",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=False),
        sa.Column("field_path", sa.String(length=200), nullable=False),
        sa.Column("data_class", sa.String(length=30), nullable=False),
        sa.Column("key_envelope_id", sa.String(length=500), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("shredded", sa.Boolean(), nullable=False),
        sa.Column("shredded_at", sa.DateTime(timezone=True), nullable=True),
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
            ["agreement_id"],
            ["agreements.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_field_encryption_records_organization_id",
        "field_encryption_records",
        ["organization_id"],
    )
    op.create_index(
        "ix_field_encryption_records_agreement_id",
        "field_encryption_records",
        ["agreement_id"],
    )

    op.create_table(
        "redaction_requests",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=False),
        sa.Column("document_type", sa.String(length=50), nullable=False),
        sa.Column("targets", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("redacted_content_ref", sa.String(length=500), nullable=True),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("processed_by", sa.UUID(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["agreement_id"],
            ["agreements.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["processed_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_redaction_requests_organization_id",
        "redaction_requests",
        ["organization_id"],
    )
    op.create_index(
        "ix_redaction_requests_agreement_id",
        "redaction_requests",
        ["agreement_id"],
    )

    op.create_table(
        "erasure_requests",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=True),
        sa.Column("data_subject", sa.String(length=500), nullable=False),
        sa.Column("regulation", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("shredded_fields", sa.JSON(), nullable=True),
        sa.Column("preserved_notes", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["agreement_id"],
            ["agreements.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_erasure_requests_organization_id",
        "erasure_requests",
        ["organization_id"],
    )
    op.create_index(
        "ix_erasure_requests_agreement_id",
        "erasure_requests",
        ["agreement_id"],
    )

    # ---- Knowledge chunks (2.10) ----
    op.create_table(
        "knowledge_chunks",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=True),
        sa.Column("clause_id", sa.UUID(), nullable=True),
        sa.Column("agreement_type", sa.String(length=100), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("party_ids", sa.JSON(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("jurisdiction", sa.String(length=20), nullable=True),
        sa.Column("classification", sa.String(length=50), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
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
            ["agreement_id"],
            ["agreements.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["clause_id"],
            ["clauses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["agreement_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agreement_id",
            "version_id",
            "chunk_index",
            name="uq_knowledge_chunk_agreement_version_index",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_organization_id",
        "knowledge_chunks",
        ["organization_id"],
    )
    op.create_index(
        "ix_knowledge_chunks_agreement_id",
        "knowledge_chunks",
        ["agreement_id"],
    )
    op.create_index(
        "ix_knowledge_chunks_agreement_type",
        "knowledge_chunks",
        ["agreement_type"],
    )
    op.create_index(
        "ix_knowledge_chunks_content_hash",
        "knowledge_chunks",
        ["content_hash"],
    )
    op.create_index(
        "ix_knowledge_chunks_is_current",
        "knowledge_chunks",
        ["is_current"],
    )
    op.create_index(
        "ix_knowledge_chunks_effective_from",
        "knowledge_chunks",
        ["effective_from"],
    )
    op.create_index(
        "ix_knowledge_chunks_effective_to",
        "knowledge_chunks",
        ["effective_to"],
    )

    # ---- Contract Risk Graph (35) ----
    op.create_table(
        "risk_graph_nodes",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("node_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=True),
        sa.Column("properties", sa.JSON(), nullable=True),
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
            ["agreement_id"],
            ["agreements.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "node_type",
            "entity_id",
            name="uq_risk_graph_node_type_entity",
        ),
    )
    op.create_index(
        "ix_risk_graph_nodes_organization_id",
        "risk_graph_nodes",
        ["organization_id"],
    )
    op.create_index(
        "ix_risk_graph_nodes_entity_id",
        "risk_graph_nodes",
        ["entity_id"],
    )
    op.create_index(
        "ix_risk_graph_nodes_agreement_id",
        "risk_graph_nodes",
        ["agreement_id"],
    )

    op.create_table(
        "risk_graph_edges",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("source_node_id", sa.UUID(), nullable=False),
        sa.Column("target_node_id", sa.UUID(), nullable=False),
        sa.Column("edge_type", sa.String(length=50), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("properties", sa.JSON(), nullable=True),
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
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_node_id"],
            ["risk_graph_nodes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_node_id"],
            ["risk_graph_nodes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_node_id",
            "target_node_id",
            "edge_type",
            name="uq_risk_graph_edge",
        ),
    )
    op.create_index(
        "ix_risk_graph_edges_organization_id",
        "risk_graph_edges",
        ["organization_id"],
    )
    op.create_index(
        "ix_risk_graph_edges_source_node_id",
        "risk_graph_edges",
        ["source_node_id"],
    )
    op.create_index(
        "ix_risk_graph_edges_target_node_id",
        "risk_graph_edges",
        ["target_node_id"],
    )


def downgrade() -> None:
    op.drop_table("risk_graph_edges")
    op.drop_table("risk_graph_nodes")
    op.drop_table("knowledge_chunks")
    op.drop_table("erasure_requests")
    op.drop_table("redaction_requests")
    op.drop_table("field_encryption_records")
    op.drop_table("human_review_tasks")
    op.drop_table("ocr_documents")
    op.drop_table("ingestion_jobs")
    op.drop_table("integration_connectors")
    op.drop_column("approval_stages", "execution_mode")