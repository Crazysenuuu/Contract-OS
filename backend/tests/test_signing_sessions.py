"""Integration tests for signing sessions (spec 2.06)."""

import pytest
import pytest_asyncio
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def signed_request(
    client: AsyncClient,
    auth_headers: dict,
    db_session,
    test_agreement,
    test_user,
):
    from app.models.agreement import AgreementVersion

    version = AgreementVersion(
        agreement_id=test_agreement.id,
        version_number=1,
        content="FINAL NEGOTIATED TEXT",
        content_hash="abc123",
        status="negotiated",
        created_by=test_user.id,
    )
    db_session.add(version)
    await db_session.commit()
    await db_session.refresh(version)
    resp = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/signature-requests",
        json={
            "name": test_user.name,
            "email": test_user.email,
            "version_id": str(version.id),
            "role": "signer",
            "signer_type": "internal",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _start_session(client, auth_headers, request_id: str) -> dict:
    resp = await client.post(
        "/api/v1/signing/sessions",
        json={"signature_request_id": request_id},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "one_time_token" in body
    return body


async def _consent_authenticate(client, auth_headers, agreement_id, session):
    resp = await client.post(
        f"/api/v1/signing/sessions/{session['session_id']}/consent",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "AUTHENTICATION_REQUIRED"

    otp = await client.post(
        f"/api/v1/agreements/{agreement_id}/signature-requests/{session['signature_request_id']}/otp/issue",
        json={"channel": "email"},
        headers=auth_headers,
    )
    assert otp.status_code == 201, otp.text
    auth = await client.post(
        f"/api/v1/signing/sessions/{session['session_id']}/authenticate",
        json={"challenge_id": otp.json()["challenge_id"], "code": otp.json()["debug_code"]},
        headers=auth_headers,
    )
    assert auth.status_code == 200, auth.text
    assert auth.json()["status"] == "READY"


async def test_start_and_exchange_token(client, auth_headers, signed_request):
    session = await _start_session(client, auth_headers, signed_request["id"])

    exch = await client.post(
        "/api/v1/signing/sessions/exchange",
        json={"token": session["one_time_token"]},
        headers=auth_headers,
    )
    assert exch.status_code == 200, exch.text
    assert exch.json()["session_id"] == session["session_id"]
    assert exch.json()["status"] == "CREATED"

    # Token is single-use.
    again = await client.post(
        "/api/v1/signing/sessions/exchange",
        json={"token": session["one_time_token"]},
        headers=auth_headers,
    )
    assert again.status_code == 401


async def test_consent_then_authentication_then_sign(
    client, auth_headers, test_agreement, signed_request
):
    session = await _start_session(client, auth_headers, signed_request["id"])
    await client.post(
        "/api/v1/signing/sessions/exchange",
        json={"token": session["one_time_token"]},
        headers=auth_headers,
    )
    await _consent_authenticate(
        client, auth_headers, str(test_agreement.id), session
    )

    fields = await client.get(
        f"/api/v1/signing/sessions/{session['session_id']}/fields",
        headers=auth_headers,
    )
    assert fields.status_code == 200, fields.text
    placements = fields.json()["placements"]
    assert len(placements) == 1

    sign = await client.post(
        f"/api/v1/signing/sessions/{session['session_id']}/signature",
        json={
            "placement_id": placements[0]["id"],
            "signature_type": "DRAWN",
            "signature_payload": "user-signature-image",
        },
        headers=auth_headers,
    )
    assert sign.status_code == 200, sign.text
    body = sign.json()
    assert body["session_status"] == "SIGNED"
    assert body["signature_hash"]

    detail = await client.get(
        f"/api/v1/signing/sessions/{session['session_id']}",
        headers=auth_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "SIGNED"


async def test_exchange_idempotency_key(client, auth_headers, signed_request):
    session = await _start_session(client, auth_headers, signed_request["id"])
    headers = {**auth_headers, "Idempotency-Key": "abc-123"}
    first = await client.post(
        "/api/v1/signing/sessions/exchange",
        json={"token": session["one_time_token"]},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        "/api/v1/signing/sessions/exchange",
        json={"token": session["one_time_token"]},
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["session_id"] == first.json()["session_id"]


async def test_evidence_ledger_has_events(
    client, auth_headers, test_agreement, signed_request
):
    session = await _start_session(client, auth_headers, signed_request["id"])
    await client.post(
        "/api/v1/signing/sessions/exchange",
        json={"token": session["one_time_token"]},
        headers=auth_headers,
    )
    await _consent_authenticate(
        client, auth_headers, str(test_agreement.id), session
    )
    events = await client.get(
        f"/api/v1/signing/sessions/{session['session_id']}/events",
        headers=auth_headers,
    )
    assert events.status_code == 200, events.text
    types = [e["event_type"] for e in events.json()["events"]]
    assert "REQUEST_OPENED" in types
    assert "CONSENT_GIVEN" in types
    assert "AUTHENTICATION_STARTED" in types
    assert "AUTHENTICATION_PASSED" in types
    # Hash chain is intact.
    hashes = [e["event_hash"] for e in events.json()["events"]]
    assert len(set(hashes)) == len(hashes)


async def test_signing_order_blocked(
    client, auth_headers, db_session, test_agreement, signed_request, test_user
):
    from sqlalchemy import select, update
    from app.models.execution import SignatureRequest

    req2_resp = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/signature-requests",
        json={
            "name": "Second Signer",
            "email": "second@example.com",
            "version_id": signed_request["version_id"],
            "role": "signer",
            "signer_type": "external",
        },
        headers=auth_headers,
    )
    assert req2_resp.status_code == 201, req2_resp.text
    await db_session.execute(
        update(SignatureRequest)
        .where(SignatureRequest.id == req2_resp.json()["id"])
        .values(signing_order=2)
    )
    await db_session.commit()

    late = await _start_session(client, auth_headers, req2_resp.json()["id"])
    await client.post(
        "/api/v1/signing/sessions/exchange",
        json={"token": late["one_time_token"]},
        headers=auth_headers,
    )
    await _consent_authenticate(client, auth_headers, str(test_agreement.id), late)
    fields = (await client.get(
        f"/api/v1/signing/sessions/{late['session_id']}/fields",
        headers=auth_headers,
    )).json()["placements"]

    blocked = await client.post(
        f"/api/v1/signing/sessions/{late['session_id']}/signature",
        json={
            "placement_id": fields[0]["id"],
            "signature_type": "DRAWN",
            "signature_payload": "x",
        },
        headers=auth_headers,
    )
    assert blocked.status_code == 409


async def test_decline_flow(client, auth_headers, signed_request):
    session = await _start_session(client, auth_headers, signed_request["id"])
    await client.post(
        "/api/v1/signing/sessions/exchange",
        json={"token": session["one_time_token"]},
        headers=auth_headers,
    )
    resp = await client.post(
        f"/api/v1/signing/sessions/{session['session_id']}/decline",
        json={"reason": "Business terms unacceptable"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DECLINED"

    completion = await client.get(
        f"/api/v1/signing/sessions/{session['session_id']}/completion",
        headers=auth_headers,
    )
    assert completion.status_code == 200
    assert completion.json()["declined"] is True
    assert "Business terms" in completion.json()["decline_reason"]


async def test_signature_idempotency_key(
    client, auth_headers, test_agreement, signed_request
):
    session = await _start_session(client, auth_headers, signed_request["id"])
    await client.post(
        "/api/v1/signing/sessions/exchange",
        json={"token": session["one_time_token"]},
        headers=auth_headers,
    )
    await _consent_authenticate(
        client, auth_headers, str(test_agreement.id), session
    )
    fields = (await client.get(
        f"/api/v1/signing/sessions/{session['session_id']}/fields",
        headers=auth_headers,
    )).json()["placements"]
    payload = {
        "placement_id": fields[0]["id"],
        "signature_type": "DRAWN",
        "signature_payload": "s",
    }
    idem = {**auth_headers, "Idempotency-Key": "sign-1"}
    first = await client.post(
        f"/api/v1/signing/sessions/{session['session_id']}/signature",
        json=payload,
        headers=idem,
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"/api/v1/signing/sessions/{session['session_id']}/signature",
        json=payload,
        headers=idem,
    )
    assert second.status_code == 200
    assert second.json()["signature_id"] == first.json()["signature_id"]


async def test_session_document_returns_token_url(
    client,
    auth_headers,
    test_agreement,
    test_agreement_type,
    db_session,
    signed_request,
):
    test_agreement_type.template_key = "generic_agreement_lk_v1"
    await db_session.commit()
    session = await _start_session(client, auth_headers, signed_request["id"])
    await client.post(
        "/api/v1/signing/sessions/exchange",
        json={"token": session["one_time_token"]},
        headers=auth_headers,
    )
    doc = await client.get(
        f"/api/v1/signing/sessions/{session['session_id']}/document",
        headers=auth_headers,
    )
    assert doc.status_code == 200, doc.text
    assert doc.json()["url"].startswith("/api/v1/repository/download?token=")
    assert doc.json()["expires_in"] > 0


async def test_signature_rejected_without_consent(
    client, auth_headers, test_agreement, signed_request
):
    session = await _start_session(client, auth_headers, signed_request["id"])
    await client.post(
        "/api/v1/signing/sessions/exchange",
        json={"token": session["one_time_token"]},
        headers=auth_headers,
    )
    fields = (await client.get(
        f"/api/v1/signing/sessions/{session['session_id']}/fields",
        headers=auth_headers,
    )).json()["placements"]
    sign = await client.post(
        f"/api/v1/signing/sessions/{session['session_id']}/signature",
        json={
            "placement_id": fields[0]["id"],
            "signature_type": "DRAWN",
            "signature_payload": "x",
        },
        headers=auth_headers,
    )
    assert sign.status_code == 409