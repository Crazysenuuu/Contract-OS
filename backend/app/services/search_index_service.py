"""Document full-text search indexing (fixes the missing-module crash).

:func:`sync_pending_documents` is the projection job behind the
``index-pending-documents`` Celery Beat entry (every five minutes). It
copies the text of each newly-committed OCR document into
``document_search_index``, where PostgreSQL can serve it through a GIN
indexed ``tsvector`` instead of scanning ``ocr_documents.extracted_text``
on every keystroke.

Two design points that are easy to get wrong and are load-bearing here:

**Tenant context.** ``document_search_index`` has
``FORCE ROW LEVEL SECURITY`` and its policy reads
``app.current_tenant``. A Celery worker holds no HTTP request, so nothing
has set that variable and every insert would be rejected by the policy.
The sweep therefore groups pending work by organization and sets tenant
context per organization before writing.

**Pending marker.** ``ocr_documents.indexed_at`` is NULL until the
document is projected. Both writers of ``extracted_text``
(``ocr_service.run_ocr`` and ``ocr_service.apply_human_review``) clear that
marker, so a human-corrected document is re-indexed on the next sweep
rather than keeping stale search text forever.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_search_index import DocumentSearchIndex
from app.models.ingestion import OCRDocument
from app.services.tenant_context import clear_tenant_context, set_tenant_context

logger = logging.getLogger(__name__)

#: Only committed documents are indexed. `needs_review` text is provisional
#: and awaiting a human correction; `queued`/`processing` have no text yet;
#: `failed` never produced any.
INDEXABLE_STATUSES = ("committed",)

#: Upper bound on documents touched per sweep. Keeps a cold-start backlog
#: from holding one transaction open long enough to starve the pool, and
#: gives the next tick a chance to interleave.
DEFAULT_BATCH_SIZE = 200

#: Upper bound on characters stored per document. A 900-page OCR run can
#: produce tens of megabytes of text; indexing it whole wastes space and
#: slows the tsvector build without helping a top-of-results search, which
#: only ever ranks the first few hundred characters of any match.
MAX_INDEXED_CHARS = 2_000_000


def _is_postgres(db: AsyncSession) -> bool:
    dialect = getattr(db.bind, "dialect", None)
    return bool(dialect and dialect.name == "postgresql")


def compute_content_hash(title: str, content: str) -> str:
    """Stable digest of the indexed text.

    Title is included so that a rename alone invalidates the index rather
    than leaving a stale ``title`` column and vector weight behind.
    """
    digest = hashlib.sha256()
    digest.update(title.encode("utf-8"))
    digest.update(b"\n")
    digest.update(content.encode("utf-8"))
    return digest.hexdigest()


async def sync_pending_documents(
    db: AsyncSession,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    """Project un-indexed OCR text into the search index.

    Returns a summary dict suitable for logging by the Celery task.
    """
    rows = (
        await db.execute(
            select(
                OCRDocument.id,
                OCRDocument.organization_id,
                OCRDocument.original_filename,
                OCRDocument.extracted_text,
            )
            .where(
                OCRDocument.indexed_at.is_(None),
                OCRDocument.status.in_(INDEXABLE_STATUSES),
                OCRDocument.extracted_text.is_not(None),
            )
            .order_by(OCRDocument.updated_at)
            .limit(batch_size)
        )
    ).all()

    if not rows:
        return {"examined": 0, "indexed": 0, "skipped": 0, "truncated": 0}

    # Group by organization: one transaction can only carry one tenant
    # context at a time, so each organization gets its own set/clear cycle.
    by_org: dict[uuid.UUID, list[Any]] = {}
    for row in rows:
        by_org.setdefault(row.organization_id, []).append(row)

    indexed = 0
    skipped = 0
    truncated = 0

    for org_id, org_rows in by_org.items():
        await set_tenant_context(db, org_id)
        try:
            for row in org_rows:
                title = (row.original_filename or "")[:500]
                content = row.extracted_text or ""
                if len(content) > MAX_INDEXED_CHARS:
                    content = content[:MAX_INDEXED_CHARS]
                    truncated += 1

                digest = compute_content_hash(title, content)
                existing = (
                    await db.execute(
                        select(DocumentSearchIndex.content_hash).where(
                            DocumentSearchIndex.document_id == row.id
                        )
                    )
                ).scalar_one_or_none()

                if existing == digest:
                    # Text unchanged since the last sweep; just re-stamp the
                    # marker so this document stops being re-examined.
                    skipped += 1
                    await _mark_indexed(db, row.id)
                    continue

                await _upsert(
                    db,
                    document_id=row.id,
                    organization_id=org_id,
                    title=title,
                    content=content,
                    content_hash=digest,
                )
                await _mark_indexed(db, row.id)
                indexed += 1
        finally:
            # Clearing matters: the session is reused for the next
            # organization, and a stale tenant would otherwise let the
            # previous organization's context leak into the next block.
            await clear_tenant_context(db)

    summary = {
        "examined": len(rows),
        "indexed": indexed,
        "skipped": skipped,
        "truncated": truncated,
    }
    logger.info("document search index sweep: %s", summary)
    return summary


async def _upsert(
    db: AsyncSession,
    *,
    document_id: uuid.UUID,
    organization_id: uuid.UUID,
    title: str,
    content: str,
    content_hash: str,
) -> None:
    """Insert or update one projection row.

    Uses ``ON CONFLICT`` on PostgreSQL. On SQLite (tests) there is no
    ``search_vector`` to maintain and the upsert is a plain update-then-
    insert, which the unique constraint on ``document_id`` keeps
    unambiguous.
    """
    now = datetime.now(timezone.utc)
    values = {
        "organization_id": organization_id,
        "document_id": document_id,
        "title": title,
        "content": content,
        "content_hash": content_hash,
        "content_length": len(content),
        "indexed_at": now,
        "updated_at": now,
    }

    if _is_postgres(db):
        # search_vector is deliberately omitted: a BEFORE INSERT OR UPDATE
        # trigger on the table derives it from title + content, so letting
        # the application write it would create a second source of truth.
        stmt = pg_insert(DocumentSearchIndex).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[DocumentSearchIndex.document_id],
            set_={
                "organization_id": stmt.excluded.organization_id,
                "title": stmt.excluded.title,
                "content": stmt.excluded.content,
                "content_hash": stmt.excluded.content_hash,
                "content_length": stmt.excluded.content_length,
                "indexed_at": stmt.excluded.indexed_at,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await db.execute(stmt)
        return

    updated = (
        await db.execute(
            DocumentSearchIndex.__table__.update()
            .where(DocumentSearchIndex.__table__.c.document_id == document_id)
            .values(**values)
        )
    ).rowcount

    if not updated:
        await db.execute(DocumentSearchIndex.__table__.insert().values(**values))


async def _mark_indexed(db: AsyncSession, document_id: uuid.UUID) -> None:
    await db.execute(
        OCRDocument.__table__.update()
        .where(OCRDocument.__table__.c.id == document_id)
        .values(indexed_at=datetime.now(timezone.utc))
    )
    # A Core UPDATE does not go through the identity map, so any ORM copy of
    # this row loaded earlier in the session still believes indexed_at is
    # NULL. Expiring the attribute makes the next assignment of
    # `doc.indexed_at = None` register as a real change; without this it is
    # swallowed as a same-value assignment and the document is never
    # re-indexed.
    for obj in db.identity_map.values():
        if isinstance(obj, OCRDocument) and obj.id == document_id:
            db.expire(obj, ["indexed_at"])


async def search_documents(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    query: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Search indexed document text for one organization.

    PostgreSQL uses the GIN-indexed ``tsvector`` with relevance ranking.
    SQLite has neither tsvector nor a GIN index, so it falls back to a LIKE
    scan over the same table; correct, and only used by the test rig.
    """
    query = (query or "").strip()
    if not query:
        return []

    if _is_postgres(db):
        # websearch_to_tsquery never raises on user input, unlike
        # to_tsquery, so a stray operator or unbalanced quote is treated as
        # literal text instead of erroring the whole request.
        tsquery = func.websearch_to_tsquery("english", query)
        rank = func.ts_rank_cd(
            DocumentSearchIndex.search_vector, tsquery
        ).label("rank")

        rows = (
            await db.execute(
                select(
                    DocumentSearchIndex.document_id,
                    DocumentSearchIndex.title,
                    DocumentSearchIndex.content_length,
                    DocumentSearchIndex.indexed_at,
                    rank,
                )
                .where(
                    DocumentSearchIndex.organization_id == organization_id,
                    DocumentSearchIndex.search_vector.op("@@")(tsquery),
                )
                # Index-backed ORDER BY: without this, ts_rank_cd is
                # computed for every row before the LIMIT applies.
                .order_by(rank.desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                "document_id": str(r.document_id),
                "title": r.title,
                "content_length": r.content_length,
                "indexed_at": r.indexed_at,
                "rank": float(r.rank or 0.0),
            }
            for r in rows
        ]

    pattern = f"%{query}%"
    rows = (
        await db.execute(
            select(
                DocumentSearchIndex.document_id,
                DocumentSearchIndex.title,
                DocumentSearchIndex.content_length,
                DocumentSearchIndex.indexed_at,
            )
            .where(
                DocumentSearchIndex.organization_id == organization_id,
                DocumentSearchIndex.title.ilike(pattern)
                | DocumentSearchIndex.content.ilike(pattern),
            )
            .order_by(DocumentSearchIndex.indexed_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "document_id": str(r.document_id),
            "title": r.title,
            "content_length": r.content_length,
            "indexed_at": r.indexed_at,
            "rank": 0.0,
        }
        for r in rows
    ]