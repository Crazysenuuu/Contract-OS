"""Integration tests for the executed-agreement repository (spec 2.07)."""

import pytest
import pytest_asyncio
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


def _upload(
    client: AsyncClient,
    agreement_id: str,
    *,
    headers: dict | None = None,
    type_code: str = "schedule",
    **kw,
):
    files = {"file": ("schedule1.pdf", PDF_BYTES, "application/pdf")}
    data = {"document_type_code": type_code}
    data.update(kw)
    return client.post(
        f"/api/v1/agreements/{agreement_id}/documents",
        files=files,
        data=data,
        headers=headers,
    )


async def test_list_documents_empty(
    client: AsyncClient, auth_headers: dict, test_org, test_agreement
):
    resp = await client.get(
        f"/api/v1/agreements/{test_agreement.id}/documents", headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["documents"] == []


async def test_repository_summary_executed_document_absent(
    client: AsyncClient, auth_headers: dict, test_agreement
):
    resp = await client.get(
        f"/api/v1/agreements/{test_agreement.id}/repository", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agreement_id"] == str(test_agreement.id)
    assert body["executed_document"] is None
    assert body["execution_summary"] is None
    assert body["legal_hold"]["on_hold"] is False


async def test_upload_schedule_document(
    client: AsyncClient, auth_headers: dict, test_agreement
):
    resp = await _upload(client, str(test_agreement.id), headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["document_type"] == "schedule"
    assert body["immutable"] is False
    assert body["sha256"]


async def test_repository_search_filters_documents(
    client: AsyncClient, auth_headers: dict, test_agreement
):
    await _upload(client, str(test_agreement.id), title="Service Annex", type_code="annex", headers=auth_headers)
    await _upload(
        client,
        str(test_agreement.id),
        title="Risk Certificate",
        type_code="certificate",
        headers=auth_headers,
    )

    resp = await client.get(
        f"/api/v1/agreements/{test_agreement.id}/repository/search",
        params={"q": "Annex"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    titles = [d["title"] for d in resp.json()["documents"]]
    assert titles == ["Service Annex"]

    resp = await client.get(
        f"/api/v1/agreements/{test_agreement.id}/repository/search",
        params={"document_type_code": "certificate"},
        headers=auth_headers,
    )
    titles = [d["title"] for d in resp.json()["documents"]]
    assert titles == ["Risk Certificate"]


async def test_executed_document_type_is_immutable(
    client: AsyncClient, auth_headers: dict, test_agreement
):
    resp = await _upload(client, str(test_agreement.id), type_code="executed", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["immutable"] is True


async def test_patch_immutable_document_rejected(
    client: AsyncClient, auth_headers: dict, test_agreement
):
    resp = await _upload(client, str(test_agreement.id), type_code="executed", headers=auth_headers)
    doc_id = resp.json()["id"]
    resp = await client.patch(
        f"/api/v1/agreements/{test_agreement.id}/documents/{doc_id}",
        json={"title": "Mutated"},
        headers=auth_headers,
    )
    assert resp.status_code == 409


async def test_archive_immutable_document_rejected(
    client: AsyncClient, auth_headers: dict, test_agreement
):
    resp = await _upload(client, str(test_agreement.id), type_code="executed", headers=auth_headers)
    doc_id = resp.json()["id"]
    resp = await client.delete(
        f"/api/v1/agreements/{test_agreement.id}/documents/{doc_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 409


async def test_patch_mutable_document_metadata(
    client: AsyncClient, auth_headers: dict, test_agreement
):
    resp = await _upload(client, str(test_agreement.id), title="Schedule A", headers=auth_headers)
    doc_id = resp.json()["id"]
    resp = await client.patch(
        f"/api/v1/agreements/{test_agreement.id}/documents/{doc_id}",
        json={"title": "Schedule A (revised)"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    detail = await client.get(
        f"/api/v1/agreements/{test_agreement.id}/documents/{doc_id}",
        headers=auth_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["title"] == "Schedule A (revised)"


async def test_archive_mutable_document(
    client: AsyncClient, auth_headers: dict, test_agreement
):
    resp = await _upload(client, str(test_agreement.id), title="Expired Draft", headers=auth_headers)
    doc_id = resp.json()["id"]
    resp = await client.delete(
        f"/api/v1/agreements/{test_agreement.id}/documents/{doc_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"


async def test_secure_download_roundtrip(
    client: AsyncClient, auth_headers: dict, test_agreement
):
    resp = await _upload(
        client,
        str(test_agreement.id),
        title="Evidence PDF",
        type_code="signature_evidence",
        headers=auth_headers,
    )
    doc_id = resp.json()["id"]

    dl = await client.get(
        f"/api/v1/agreements/{test_agreement.id}/documents/{doc_id}/download",
        headers=auth_headers,
    )
    assert dl.status_code == 200
    download_url = dl.json()["download_url"]

    fetched = await client.get(download_url, headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.content == PDF_BYTES


async def test_link_documents_creates_relationship(
    client: AsyncClient, auth_headers: dict, test_agreement
):
    a = await _upload(client, str(test_agreement.id), title="Annex 1", type_code="annex", headers=auth_headers)
    b = await _upload(client, str(test_agreement.id), title="Executed", type_code="executed", headers=auth_headers)
    resp = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/documents/link",
        json={
            "source_document_id": a.json()["id"],
            "target_document_id": b.json()["id"],
            "relationship_type": "ANNEX",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["relationship_type"] == "ANNEX"


async def test_timeline_records_ingest_events(
    client: AsyncClient, auth_headers: dict, test_agreement
):
    await _upload(client, str(test_agreement.id), title="Cert", type_code="certificate", headers=auth_headers)
    resp = await client.get(
        f"/api/v1/agreements/{test_agreement.id}/timeline", headers=auth_headers
    )
    assert resp.status_code == 200
    actions = [e["action"] for e in resp.json()["events"]]
    assert "DOCUMENT_INGESTED" in actions


async def test_evidence_aggregation(
    client: AsyncClient, auth_headers: dict, test_agreement
):
    await _upload(
        client,
        str(test_agreement.id),
        title="Signed PDF",
        type_code="signature_evidence",
        headers=auth_headers,
    )
    resp = await client.get(
        f"/api/v1/agreements/{test_agreement.id}/evidence", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["execution_packages"] == []
    assert len(body["evidence_documents"]) == 1
    assert body["evidence_documents"][0]["document_type"] == "signature_evidence"


async def test_upload_without_auth_token(
    client: AsyncClient, test_agreement
):
    resp = await _upload(client, str(test_agreement.id))
    assert resp.status_code == 401


async def test_orgless_user_blocked_from_upload(
    client: AsyncClient, db_session, test_agreement
):
    """A user with no organization membership cannot access the repository."""
    from app.models.user import User
    from app.core.security import create_access_token, hash_password

    stranger = User(
        email="stranger@example.com",
        name="Stranger",
        password_hash=hash_password("TestPass123!"),
        status="active",
    )
    db_session.add(stranger)
    await db_session.commit()
    await db_session.refresh(stranger)

    headers = {"Authorization": f"Bearer {create_access_token(user_id=stranger.id)}"}
    resp = await _upload(client, str(test_agreement.id), headers=headers)
    assert resp.status_code == 403