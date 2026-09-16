"""Document integrity verification tests (spec 2.07.41, 2.08.18).

`verify_document_integrity` recomputes the SHA-256 of the stored blob and
compares it to the registered hash. A tampered storage object or a missing
blob must report ``match = False`` so evidence review and download flows
reject the artifact.
"""

import hashlib

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.services.document_repository import (
    verify_document_integrity,
)
from app.services.document_storage import StorageError, load_blob, store_blob

pytestmark = pytest.mark.asyncio

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


async def _uploaded_document(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, agreement_id: str
) -> Document:
    files = {"file": ("schedule1.pdf", PDF_BYTES, "application/pdf")}
    data = {"document_type_code": "schedule"}
    resp = await client.post(
        f"/api/v1/agreements/{agreement_id}/documents",
        files=files,
        data=data,
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return (
        await db_session.execute(
            select(Document).where(Document.id == resp.json()["id"])
        )
    ).scalars().one()


async def test_integrity_match_for_untouched_blob(
    client: AsyncClient,
    auth_headers: dict,
    db_session: AsyncSession,
    test_agreement,
):
    doc = await _uploaded_document(client, auth_headers, db_session, str(test_agreement.id))
    result = await verify_document_integrity(doc)
    assert result["match"] is True
    assert result["computed_hash"] == result["stored_hash"]


async def test_modified_storage_object_detected(
    client: AsyncClient,
    auth_headers: dict,
    db_session: AsyncSession,
    test_agreement,
):
    """spec 2.07.41: test_modified_storage_object_detected -> match False."""
    doc = await _uploaded_document(client, auth_headers, db_session, str(test_agreement.id))

    # Tamper with the stored bytes directly (simulates a corrupted backend).
    store_blob(doc.storage_key, b"%PDF-1.4 TAMPERED bytes")

    result = await verify_document_integrity(doc)
    assert result["match"] is False
    assert result["computed_hash"] != result["stored_hash"]


async def test_missing_blob_reports_mismatch(
    client: AsyncClient,
    auth_headers: dict,
    db_session: AsyncSession,
    test_agreement,
):
    doc = await _uploaded_document(client, auth_headers, db_session, str(test_agreement.id))

    from app.services.document_storage import delete_blob

    try:
        load_blob(doc.storage_key)
        delete_blob(doc.storage_key)
    except StorageError:
        pass

    result = await verify_document_integrity(doc)
    assert result["match"] is False
    assert result["reason"] == "blob_unavailable"


async def test_integrity_check_roundtrip_via_service_function(
    db_session: AsyncSession, test_agreement
):
    from app.models.document import Document
    from app.services.document_repository import ensure_document_type

    doc_type = await ensure_document_type(db_session, "schedule")
    content = b"%PDF-1.4 integrity roundtrip"
    storage_key = f"{test_agreement.organization_id}/{test_agreement.id}/schedule-integrity.pdf"
    store_blob(storage_key, content)
    doc = Document(
        organization_id=test_agreement.organization_id,
        agreement_id=test_agreement.id,
        document_type_id=doc_type.id,
        title="integrity",
        filename="integrity.pdf",
        media_type="application/pdf",
        storage_key=storage_key,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        status="ACTIVE",
    )
    db_session.add(doc)
    await db_session.commit()

    result = await verify_document_integrity(doc)
    assert result["match"] is True