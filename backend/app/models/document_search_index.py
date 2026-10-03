"""Full-text search index for ingested document content.

Agreement *metadata* search (title, governing law, party names) is served
straight off ``agreements`` by :mod:`app.api.v1.search`. That works because
the fields are already columns. Document *bodies* are not: OCR output lives
in ``ocr_documents.extracted_text`` as a large blob that cannot be scanned
per keystroke, and it is rewritten whenever a human corrects a degraded OCR
result.

This table is the denormalised, per-document searchable projection of that
text. It is maintained exclusively by
:func:`app.services.search_index_service.sync_pending_documents`, which
Celery Beat runs every five minutes.

Design notes:

- ``content_hash`` lets the sweep skip documents whose text has not changed,
  so the common case (nothing new to ingest) costs one indexed SELECT.
- ``indexed_at`` on ``ocr_documents`` is the "needs indexing" marker, so
  pending documents are found with a partial index rather than a join.
- ``search_vector`` is a Postgres ``tsvector`` with a GIN index. It is
  nullable and unused on SQLite, where the service falls back to LIKE.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class DocumentSearchIndex(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Searchable projection of one ingested document's text."""

    __tablename__ = "document_search_index"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ocr_documents.id", ondelete="CASCADE"),
        nullable=False,
        # One projection per document. Re-indexing is an UPSERT keyed on this
        # column, so the uniqueness is what keeps the table at one row per
        # document across re-OCR. The UNIQUE constraint also supplies the
        # lookup index, so no separate index=True is declared.
        unique=True,
    )

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    #: SHA-256 of ``title + '\\n' + content`` at index time. Lets the sweep
    #: detect "document re-saved but text identical" and skip the write.
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    #: Character length of the indexed text, reported with search results.
    content_length: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        nullable=True,
        # Populated by a generated/maintained trigger on PostgreSQL only;
        # see migration 7c2d1e0f4a89_add_document_search_index.py.
    )

    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
