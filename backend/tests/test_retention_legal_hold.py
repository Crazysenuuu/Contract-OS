"""Tests for retention engine, legal holds, repository, and object storage."""

import uuid

import pytest
from sqlalchemy import select

from app.models.retention import (
    LegalHold,
    RepositoryRecord,
    RetentionPolicy,
    RetentionRecord,
)
from app.services import document_storage
from app.services.retention_service import (
    apply_policy_to_agreement,
    list_active_holds,
    place_legal_hold,
    release_legal_hold,
    run_retention_worker,
    store_repository_record,
)


@pytest.mark.asyncio
async def test_apply_policy_creates_record(db_session, test_org, test_agreement):
    policy = RetentionPolicy(
        organization_id=test_org.id,
        name="7 year retention",
        scope="all",
        retention_months=84,
        disposition="archive",
    )
    db_session.add(policy)
    await db_session.flush()

    record = await apply_policy_to_agreement(
        db_session,
        org_id=test_org.id,
        agreement=test_agreement,
    )
    assert record is not None
    assert record.status == "active"
    assert record.policy_id == policy.id


@pytest.mark.asyncio
async def test_legal_hold_freezes_retention(db_session, test_org, test_agreement, test_user):
    policy = RetentionPolicy(
        organization_id=test_org.id,
        name="7 year retention",
        scope="all",
        retention_months=84,
        disposition="archive",
    )
    db_session.add(policy)
    await db_session.flush()
    await apply_policy_to_agreement(db_session, org_id=test_org.id, agreement=test_agreement)
    await db_session.flush()

    hold = await place_legal_hold(
        db_session,
        org_id=test_org.id,
        agreement_id=test_agreement.id,
        reason="Pending litigation",
        placed_by=test_user.id,
    )
    await db_session.flush()

    rec = (
        await db_session.execute(
            select(RetentionRecord).where(
                RetentionRecord.agreement_id == test_agreement.id
            )
        )
    ).scalar_one()
    assert rec.status == "held"

    # Worker skips held agreements even when expired.
    from datetime import datetime, timedelta, timezone
    rec.retention_until = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.flush()

    result = await run_retention_worker(db_session, org_id=test_org.id, dry_run=True)
    assert result["held_skipped"] == 1

    # Releasing restores active.
    await release_legal_hold(db_session, hold_id=hold.id, released_by=test_user.id)
    await db_session.flush()
    await db_session.refresh(rec)
    assert rec.status == "active"


@pytest.mark.asyncio
async def test_retention_worker_reports_expired(db_session, test_org, test_agreement):
    policy = RetentionPolicy(
        organization_id=test_org.id,
        name="1 month retention",
        scope="all",
        retention_months=1,
        disposition="delete",
    )
    db_session.add(policy)
    await db_session.flush()
    await apply_policy_to_agreement(db_session, org_id=test_org.id, agreement=test_agreement)
    await db_session.flush()

    from datetime import datetime, timedelta, timezone
    rec = (
        await db_session.execute(
            select(RetentionRecord).where(
                RetentionRecord.agreement_id == test_agreement.id
            )
        )
    ).scalar_one()
    rec.retention_until = datetime.now(timezone.utc) - timedelta(days=40)
    await db_session.flush()

    result = await run_retention_worker(db_session, org_id=test_org.id, dry_run=True)
    assert result["expired_found"] == 1
    assert result["deletion_candidates"] == 1


@pytest.mark.asyncio
async def test_repository_record_store_and_download_token(db_session, test_org, test_agreement):
    content_ref = document_storage.build_content_ref(
        org_id=test_org.id, agreement_id=test_agreement.id, doc_type="executed"
    )
    pdf_bytes = b"%PDF-1.4 fake pdf content"
    document_storage.store_blob(content_ref, pdf_bytes)

    record = await store_repository_record(
        db_session,
        org_id=test_org.id,
        agreement_id=test_agreement.id,
        version_id=None,
        document_type="executed",
        content_ref=content_ref,
        content_hash="abc123",
        size_bytes=len(pdf_bytes),
        is_executed=True,
    )
    await db_session.flush()

    assert record.content_hash == "abc123"
    assert document_storage.load_blob(content_ref) == pdf_bytes
    assert document_storage.blob_size(content_ref) == len(pdf_bytes)

    # Secure token round-trip
    token = document_storage.create_download_token(
        content_ref=content_ref, user_id=test_org.id, org_id=test_org.id
    )
    payload = document_storage.verify_download_token(token)
    assert payload is not None
    assert payload["content_ref"] == content_ref

    # Tampered token rejected
    assert document_storage.verify_download_token(token[:-4] + "xxxx") is None

    # Cleanup
    document_storage.delete_blob(content_ref)
    with pytest.raises(document_storage.StorageError):
        document_storage.load_blob(content_ref)


def test_watermark_html_injects():
    html = "<html><body><p>Agreement</p></body></html>"
    out = document_storage.watermark_html(html, viewer_name="Jane Doe", org_name="Acme")
    assert "contract-watermark" in out
    assert "Jane Doe" in out
    assert "Acme" in out


@pytest.mark.asyncio
async def test_retention_api_flow(client, auth_headers, test_org, test_agreement):
    # Create policy
    res = await client.post(
        "/api/v1/retention/policies",
        json={
            "name": "Default retention",
            "scope": "all",
            "retention_months": 12,
            "disposition": "archive",
        },
        headers=auth_headers,
    )
    assert res.status_code == 201

    # Place hold on the agreement
    res = await client.post(
        "/api/v1/retention/holds",
        json={"agreement_id": str(test_agreement.id), "reason": "Audit"},
        headers=auth_headers,
    )
    assert res.status_code == 201
    hold_id = res.json()["id"]

    # List holds
    res = await client.get(
        f"/api/v1/retention/holds?agreement_id={test_agreement.id}",
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert len(res.json()) == 1

    # Run worker dry-run
    res = await client.post("/api/v1/retention/worker/run?dry_run=true", headers=auth_headers)
    assert res.status_code == 200
    assert "expired_found" in res.json()

    # Release hold
    res = await client.post(f"/api/v1/retention/holds/{hold_id}/release", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["status"] == "released"

    # List repository documents for the agreement (empty initially)
    res = await client.get(
        f"/api/v1/repository/{test_agreement.id}/documents", headers=auth_headers
    )
    assert res.status_code == 200


async def _delete_policy_setup(db_session, test_org, test_agreement):
    from datetime import datetime, timedelta, timezone

    policy = RetentionPolicy(
        organization_id=test_org.id,
        name="1 month delete",
        scope="all",
        retention_months=1,
        disposition="delete",
    )
    db_session.add(policy)
    await db_session.flush()
    await apply_policy_to_agreement(db_session, org_id=test_org.id, agreement=test_agreement)
    await db_session.flush()

    content_ref = document_storage.build_content_ref(
        org_id=test_org.id, agreement_id=test_agreement.id, doc_type="executed"
    )
    payload = b"%PDF-1.4 retained artifact"
    document_storage.store_blob(content_ref, payload)
    await store_repository_record(
        db_session,
        org_id=test_org.id,
        agreement_id=test_agreement.id,
        version_id=None,
        document_type="executed",
        content_ref=content_ref,
        content_hash="abc123",
        size_bytes=len(payload),
    )
    await db_session.flush()

    rec = (
        await db_session.execute(
            select(RetentionRecord).where(
                RetentionRecord.agreement_id == test_agreement.id
            )
        )
    ).scalar_one()
    rec.retention_until = datetime.now(timezone.utc) - timedelta(days=40)
    await db_session.flush()
    return rec, content_ref, payload


@pytest.mark.asyncio
async def test_retention_delete_removes_blob(db_session, test_org, test_agreement):
    rec, content_ref, _ = await _delete_policy_setup(db_session, test_org, test_agreement)

    result = await run_retention_worker(db_session, org_id=test_org.id, dry_run=False)

    assert result["deletion_candidates"] == 1
    assert result["blobs_deleted"] == 1
    await db_session.refresh(rec)
    assert rec.status == "deleted"
    with pytest.raises(document_storage.StorageError):
        document_storage.load_blob(content_ref)


@pytest.mark.asyncio
async def test_retention_dry_run_keeps_blob(db_session, test_org, test_agreement):
    rec, content_ref, payload = await _delete_policy_setup(db_session, test_org, test_agreement)

    result = await run_retention_worker(db_session, org_id=test_org.id, dry_run=True)

    assert result["deletion_candidates"] == 1
    assert result["blobs_deleted"] == 0
    await db_session.refresh(rec)
    assert rec.status == "active"
    assert document_storage.load_blob(content_ref) == payload
    document_storage.delete_blob(content_ref)


@pytest.mark.asyncio
async def test_legal_hold_prevents_blob_deletion(
    db_session, test_org, test_agreement, test_user
):
    _, content_ref, payload = await _delete_policy_setup(db_session, test_org, test_agreement)

    await place_legal_hold(
        db_session,
        org_id=test_org.id,
        agreement_id=test_agreement.id,
        reason="Pending litigation",
        placed_by=test_user.id,
    )
    await db_session.flush()

    result = await run_retention_worker(db_session, org_id=test_org.id, dry_run=False)

    assert result["held_skipped"] == 1
    assert result["blobs_deleted"] == 0
    assert document_storage.load_blob(content_ref) == payload
    document_storage.delete_blob(content_ref)


@pytest.mark.asyncio
async def test_delete_failure_keeps_record_retryable(
    db_session, test_org, test_agreement, monkeypatch
):
    rec, content_ref, payload = await _delete_policy_setup(
        db_session, test_org, test_agreement
    )

    def _boom(_content_ref):
        raise document_storage.StorageError("bucket unavailable")

    monkeypatch.setattr(document_storage, "delete_blob", _boom)

    result = await run_retention_worker(db_session, org_id=test_org.id, dry_run=False)

    # The record must stay active (retryable) and the blob must survive.
    assert result["deletion_candidates"] == 1
    assert result["blobs_deleted"] == 0
    await db_session.refresh(rec)
    assert rec.status == "active"

    monkeypatch.undo()
    assert document_storage.load_blob(content_ref) == payload
    document_storage.delete_blob(content_ref)