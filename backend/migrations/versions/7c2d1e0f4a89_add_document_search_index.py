"""Document full-text search index

Revision ID: 7c2d1e0f4a89
Revises: e7b9a1c4d5f6
Create Date: 2026-10-02

Creates `document_search_index` (the searchable projection of OCR'd
document text) and the `indexed_at` marker on `ocr_documents` that Beat
polls.

Scope note: RLS is enabled on `document_search_index` only. Broadening
tenant isolation to the ~80 remaining tenant-scoped tables is deliberately
NOT bundled here: Celery workers query tenant tables through raw sessions
with no `app.current_tenant` set, so FORCE ROW LEVEL SECURITY on those
tables would silently turn every background job into a no-op. That
expansion needs the per-tenant worker context first, as its own migration.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "7c2d1e0f4a89"
down_revision: Union[str, Sequence[str], None] = "e7b9a1c4d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :name"
            ),
            {"name": name},
        )
        .scalar()
    )


def upgrade() -> None:
    # ---- 1. Pending-index marker on OCR documents --------------------------
    op.add_column(
        "ocr_documents",
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Partial index: the sweep only ever asks for un-indexed rows, so the
    # 99% of the table that is already indexed should not bloat the index.
    op.create_index(
        "ix_ocr_documents_pending_index",
        "ocr_documents",
        ["indexed_at"],
        postgresql_where=sa.text("indexed_at IS NULL"),
    )

    # ---- 2. The search index table ----------------------------------------
    op.create_table(
        "document_search_index",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_length", sa.Integer(), nullable=False),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["ocr_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id"),
    )
    op.create_index(
        "ix_document_search_index_organization_id",
        "document_search_index",
        ["organization_id"],
    )
    # The UNIQUE(document_id) constraint above already supplies an index for
    # document lookups and for ON CONFLICT (document_id) upserts; adding a
    # second unique index on the same column would only cost write time.
    op.create_index(
        "ix_document_search_index_indexed_at",
        "document_search_index",
        ["indexed_at"],
    )
    # GIN is what makes websearch_to_tsquery index-backed rather than a
    # sequential scan. Only valid on PostgreSQL.
    op.create_index(
        "ix_document_search_index_search_vector",
        "document_search_index",
        ["search_vector"],
        postgresql_using="gin",
    )

    # ---- 3. Maintain the tsvector from title + content -------------------
    # A GENERATED column cannot be used here: coalescing two columns into a
    # tsvector is not immutable, so Postgres rejects it at DDL time.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION document_search_index_vectorize()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(NEW.content, '')), 'B');
            RETURN NEW;
        END; $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER document_search_index_vectorize_trg
        BEFORE INSERT OR UPDATE OF title, content
        ON document_search_index
        FOR EACH ROW
        EXECUTE FUNCTION document_search_index_vectorize()
        """
    )

    # ---- 4. Tenant isolation for the new table ----------------------------
    # Same policy shape as the existing tenant tables (see the RLS migration
    # for agreements/outbox_events): visibility follows the session tenant.
    op.execute("ALTER TABLE document_search_index ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_search_index FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_document_search_index
        ON document_search_index
        USING (
            organization_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_document_search_index "
        "ON document_search_index"
    )
    op.execute("ALTER TABLE document_search_index DISABLE ROW LEVEL SECURITY")

    op.execute(
        "DROP TRIGGER IF EXISTS document_search_index_vectorize_trg "
        "ON document_search_index"
    )
    op.execute("DROP FUNCTION IF EXISTS document_search_index_vectorize()")

    op.drop_index(
        "ix_document_search_index_search_vector",
        table_name="document_search_index",
    )
    op.drop_index(
        "ix_document_search_index_indexed_at",
        table_name="document_search_index",
    )
    op.drop_index(
        "ix_document_search_index_organization_id",
        table_name="document_search_index",
    )
    op.drop_table("document_search_index")

    op.drop_index("ix_ocr_documents_pending_index", table_name="ocr_documents")
    op.drop_column("ocr_documents", "indexed_at")
