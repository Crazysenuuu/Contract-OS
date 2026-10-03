"""Tests for the document full-text search projection service.

These cover the sweep's contract rather than PostgreSQL's tsvector
behaviour (SQLite renders the vector column as TEXT and searches fall back
to LIKE). The PostgreSQL-specific paths — the ``ON CONFLICT`` upsert, the
vectorize trigger and the GIN index — can only be exercised against a real
Postgres, which is why the migration guards itself accordingly.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.document_search_index import DocumentSearchIndex
from app.models.ingestion import OCRDocument
from app.services.search_index_service import (
    MAX_INDEXED_CHARS,
    compute_content_hash,
    search_documents,
    sync_pending_documents,
)

pytestmark = pytest.mark.asyncio


async def _make_doc(
    db,
    *,
    organization_id,
    status: str = "committed",
    text: str | None = "liability cap of two million dollars",
    filename: str = "msa-2024.pdf",
    indexed_at: datetime | None = None,
) -> OCRDocument:
    """Insert an OCR document directly; the pipeline is out of scope here."""
    org_id = organization_id or uuid.uuid4()
    doc = OCRDocument(
        id=uuid.uuid4(),
        organization_id=org_id,
        job_id=uuid.uuid4(),
        original_filename=filename,
        content_ref=f"ingest/{uuid.uuid4()}.pdf",
        mime_type="application/pdf",
        provider="mock",
        status=status,
        confidence=0.95,
        extracted_text=text,
        indexed_at=indexed_at,
    )
    db.add(doc)
    await db.flush()
    return doc


async def test_indexes_pending_committed_document(db_session, test_org):
    doc = await _make_doc(
        db_session, organization_id=test_org.id, text="termination for convenience"
    )

    result = await sync_pending_documents(db_session)

    assert result["examined"] == 1
    assert result["indexed"] == 1

    row = (
        await db_session.execute(
            select(DocumentSearchIndex).where(
                DocumentSearchIndex.document_id == doc.id
            )
        )
    ).scalar_one()
    assert row.organization_id == test_org.id
    assert row.content == "termination for convenience"
    assert row.title == "msa-2024.pdf"
    assert row.content_hash == compute_content_hash("msa-2024.pdf", "termination for convenience")
    assert row.indexed_at is not None


async def test_sets_pending_marker_so_sweep_is_idempotent(db_session, test_org):
    await _make_doc(db_session, organization_id=test_org.id)
    await db_session.commit()

    first = await sync_pending_documents(db_session)
    await db_session.commit()
    second = await sync_pending_documents(db_session)
    await db_session.commit()

    assert first["indexed"] == 1
    # Second pass must find nothing: a repeat sweep every five minutes that
    # rewrites every row would be pure write amplification.
    assert second == {"examined": 0, "indexed": 0, "skipped": 0, "truncated": 0}


async def test_skips_non_committed_statuses(db_session, test_org):
    """needs_review text is provisional and must not reach search."""
    for status in ("queued", "processing", "needs_review", "failed"):
        await _make_doc(
            db_session, organization_id=test_org.id, status=status, text="secret"
        )
    await db_session.commit()

    result = await sync_pending_documents(db_session)

    assert result["examined"] == 0
    rows = (await db_session.execute(select(DocumentSearchIndex))).scalars().all()
    assert rows == []


async def test_skips_documents_with_no_extracted_text(db_session, test_org):
    await _make_doc(db_session, organization_id=test_org.id, text=None)
    await db_session.commit()

    result = await sync_pending_documents(db_session)

    assert result["examined"] == 0


async def test_reindexes_when_marker_cleared_but_hash_unchanged(db_session, test_org):
    """A human-corrected doc re-projects without duplicating the row."""
    doc = await _make_doc(db_session, organization_id=test_org.id)
    await db_session.commit()
    await sync_pending_documents(db_session)
    await db_session.commit()

    # Simulate ocr_service clearing the marker with identical text.
    doc.indexed_at = None
    await db_session.commit()

    result = await sync_pending_documents(db_session)

    assert result["skipped"] == 1
    assert result["indexed"] == 0
    rows = (await db_session.execute(select(DocumentSearchIndex))).scalars().all()
    assert len(rows) == 1, "hash-identical reindex must update, not append"


async def test_reindexes_when_text_actually_changed(db_session, test_org):
    doc = await _make_doc(db_session, organization_id=test_org.id, text="old text")
    await db_session.commit()
    await sync_pending_documents(db_session)
    await db_session.commit()

    doc.extracted_text = "corrected indemnification language"
    doc.indexed_at = None
    await db_session.commit()

    result = await sync_pending_documents(db_session)

    assert result["indexed"] == 1
    rows = (await db_session.execute(select(DocumentSearchIndex))).scalars().all()
    assert len(rows) == 1
    assert rows[0].content == "corrected indemnification language"


async def test_hash_changes_with_title(db_session, test_org):
    """A rename alone must invalidate: stale title + vector weight is a bug."""
    doc = await _make_doc(db_session, organization_id=test_org.id, text="same body")
    await db_session.commit()
    await sync_pending_documents(db_session)
    await db_session.commit()

    doc.original_filename = "msa-2025-executed.pdf"
    doc.indexed_at = None
    await db_session.commit()

    result = await sync_pending_documents(db_session)
    assert result["indexed"] == 1


async def test_truncates_oversized_documents(db_session, test_org):
    await _make_doc(
        db_session,
        organization_id=test_org.id,
        text="x" * (MAX_INDEXED_CHARS + 5_000),
    )
    await db_session.commit()

    result = await sync_pending_documents(db_session)

    assert result["truncated"] == 1
    row = (await db_session.execute(select(DocumentSearchIndex))).scalar_one()
    assert row.content_length == MAX_INDEXED_CHARS


async def test_batches_across_organizations(db_session, test_org):
    """Cross-tenant isolation: one org's text never lands in another's row."""
    other_org_id = uuid.uuid4()
    await _make_doc(db_session, organization_id=test_org.id, text="alpha clause")
    await _make_doc(db_session, organization_id=other_org_id, text="beta clause")
    await db_session.commit()

    result = await sync_pending_documents(db_session)

    assert result["examined"] == 2
    rows = (await db_session.execute(select(DocumentSearchIndex))).scalars().all()
    by_org = {r.organization_id: r.content for r in rows}
    assert by_org[test_org.id] == "alpha clause"
    assert by_org[other_org_id] == "beta clause"


async def test_respects_batch_size(db_session, test_org):
    for i in range(5):
        await _make_doc(db_session, organization_id=test_org.id, text=f"clause {i}")
    await db_session.commit()

    result = await sync_pending_documents(db_session, batch_size=2)

    assert result["examined"] == 2
    assert result["indexed"] == 2


async def test_search_matches_content_and_is_org_scoped(db_session, test_org):
    await _make_doc(
        db_session,
        organization_id=test_org.id,
        text="the supplier indemnifies for intellectual property claims",
        filename="ip-indemnity.pdf",
    )
    await _make_doc(
        db_session,
        organization_id=uuid.uuid4(),
        text="the supplier indemnifies for intellectual property claims",
        filename="other-org.pdf",
    )
    await db_session.commit()
    await sync_pending_documents(db_session)
    await db_session.commit()

    hits = await search_documents(
        db_session, organization_id=test_org.id, query="indemnifies"
    )

    assert len(hits) == 1
    assert hits[0]["title"] == "ip-indemnity.pdf"
    assert "rank" in hits[0]


async def test_search_matches_title(db_session, test_org):
    await _make_doc(
        db_session,
        organization_id=test_org.id,
        text="body text with nothing notable",
        filename="supply-agreement.pdf",
    )
    await db_session.commit()
    await sync_pending_documents(db_session)
    await db_session.commit()

    hits = await search_documents(
        db_session, organization_id=test_org.id, query="supply-agreement"
    )
    assert len(hits) == 1


async def test_search_blank_query_returns_nothing(db_session, test_org):
    await _make_doc(db_session, organization_id=test_org.id)
    await db_session.commit()
    await sync_pending_documents(db_session)
    await db_session.commit()

    for query in ("", "   ", None):
        assert await search_documents(
            db_session, organization_id=test_org.id, query=query
        ) == []


async def test_upsert_replaces_hash_and_timestamp(db_session, test_org):
    """Direct exercise of _upsert's SQLite update-then-insert path."""
    from app.services.search_index_service import _upsert

    org_id = uuid.uuid4()
    doc = await _make_doc(db_session, organization_id=org_id)

    await _upsert(
        db_session,
        document_id=doc.id,
        organization_id=org_id,
        title="a.pdf",
        content="first",
        content_hash="h1",
    )
    await _upsert(
        db_session,
        document_id=doc.id,
        organization_id=org_id,
        title="a.pdf",
        content="second",
        content_hash="h2",
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            select(DocumentSearchIndex).where(
                DocumentSearchIndex.document_id == doc.id
            )
        )
    ).scalar_one()
    assert row.content == "second"
    assert row.content_hash == "h2"


def test_compute_content_hash_is_stable_and_title_sensitive():
    a = compute_content_hash("t", "c")
    assert a == compute_content_hash("t", "c")
    assert a != compute_content_hash("t2", "c")
    assert a != compute_content_hash("t", "c2")
    assert len(a) == 64


# --- HTTP endpoint ---------------------------------------------------------
#
# The service above is only useful if a request can reach it. These cover the
# /search/documents wiring: the projection is queried under the caller's
# tenant, and the request shape is validated by FastAPI.


async def test_documents_endpoint_returns_indexed_hits(
    db_session, client, auth_headers, test_org
):
    doc = await _make_doc(
        db_session,
        organization_id=test_org.id,
        text="unlimited liability exposure in clause 9",
    )
    await db_session.commit()
    await sync_pending_documents(db_session)
    await db_session.commit()

    response = await client.get(
        "/api/v1/search/documents",
        params={"q": "liability"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["items"][0]["document_id"] == str(doc.id)
    assert body["items"][0]["title"] == "msa-2024.pdf"


async def test_documents_endpoint_is_scoped_to_the_active_organization(
    db_session, client, auth_headers, test_org
):
    """A hit indexed for another tenant must never surface for this caller."""
    await _make_doc(
        db_session,
        organization_id=uuid.uuid4(),
        text="liability cap in a foreign tenant",
    )
    await db_session.commit()
    await sync_pending_documents(db_session)
    await db_session.commit()

    response = await client.get(
        "/api/v1/search/documents",
        params={"q": "liability"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["count"] == 0


async def test_documents_endpoint_requires_a_query(client, auth_headers, test_org):
    response = await client.get(
        "/api/v1/search/documents", headers=auth_headers
    )
    assert response.status_code == 422


async def test_documents_endpoint_requires_authentication(client):
    response = await client.get(
        "/api/v1/search/documents", params={"q": "liability"}
    )
    assert response.status_code in (401, 403)