"""knowledge_chunks.embedding -> pgvector vector(1536) + HNSW cosine index

Revision ID: b2c3d4e5f6a7
Revises: 89f1bb6ef3b2
Create Date: 2026-09-21

Spec 2.10.6: embeddings stored as native pgvector columns.

- Creates the ``vector`` extension when the server provides it
  (pgvector/pgvector image). When the extension is unavailable (CI runs
  stock postgres:16 for drift checks) the column stays JSONB and the
  application degrades to Python-side scoring — migrations still succeed.
- Legacy 256-dim hash vectors (JSONB) cannot be projected into 1536 dims
  and are dropped; chunks keep their text and are re-indexable at any time
  via index_agreement_version().
- HNSW cosine index only when the column is a real vector column.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.models.knowledge import EMBEDDING_DIM, EmbeddingVectorType

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "89f1bb6ef3b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_vector_extension(conn) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        ).scalar()
    )


def _column_type(conn) -> str:
    result = conn.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'knowledge_chunks' AND column_name = 'embedding'"
        )
    ).scalar()
    return (result or "").lower()


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    try:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception:
        # Server image without pgvector (e.g. CI drift-check postgres:16):
        # keep the JSONB column; retrieval falls back to Python scoring.
        pass

    if _has_vector_extension(conn):
        # Drop legacy 256-dim hash vectors: they cannot be resized into the
        # 1536-dim column, and stale vectors would silently mismatch the
        # active embedding model. Content is retained; re-index to re-embed.
        conn.execute(sa.text("UPDATE knowledge_chunks SET embedding = NULL"))
        op.alter_column(
            "knowledge_chunks",
            "embedding",
            existing_type=sa.JSON(),
            type_=EmbeddingVectorType(EMBEDDING_DIM),
            postgresql_using="embedding::text::vector(1536)",
            nullable=True,
        )
        op.create_index(
            "ix_knowledge_chunks_embedding_hnsw",
            "knowledge_chunks",
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    if _has_vector_extension(conn) and _column_type(conn) in ("vector", "user-defined"):
        op.drop_index(
            "ix_knowledge_chunks_embedding_hnsw", table_name="knowledge_chunks"
        )
        # Vectors serialize back to JSON arrays for the legacy column type.
        op.alter_column(
            "knowledge_chunks",
            "embedding",
            existing_type=EmbeddingVectorType(EMBEDDING_DIM),
            type_=sa.JSON(),
            postgresql_using="embedding::text::jsonb",
            nullable=True,
        )
