"""Tests for e-signature provider webhook processing (spec 24.5)."""

import json
import uuid

import pytest

from app.models.execution import SignatureRequest
from app.services.esign_webhook_service import (
    EsignWebhookError,
    extract_provider_event,
    normalize_provider_status,
    process_esign_webhook,
)


def _docusign_payload(
    envelope_id: str,
    *,
    event_id: str = "evt-1",
    envelope_status: str | None = None,
    recipient_email: str | None = None,
    recipient_status: str | None = None,
) -> dict:
    payload: dict = {
        "event": "envelope-status-changed",
        "eventId": event_id,
        "data": {
            "envelopeId": envelope_id,
            "envelopeStatus": envelope_status or "completed",
        },
    }
    if recipient_email:
        payload["data"]["recipientStatuses"] = [
            {"email": recipient_email, "status": recipient_status}
        ]
    return payload


async def _make_request(db, org_id, agreement_id, envelope_id: str, created_by) -> SignatureRequest:
    req = SignatureRequest(
        tenant_id=org_id,
        agreement_id=agreement_id,
        version_id=uuid.uuid4(),  # not FK-checked in SQLite test path
        name="Jane Signer",
        email="jane@counterparty.com",
        status="sent",
        created_by=created_by,
        metadata_json={"provider_envelope_id": envelope_id},
    )
    db.add(req)
    await db.flush()
    return req


def test_normalize_provider_status_docusign():
    assert normalize_provider_status("Completed") == "signed"
    assert normalize_provider_status("Declined") == "declined"
    assert normalize_provider_status("OUT_FOR_SIGNATURE") == "sent"


def test_extract_docusign_event():
    parsed = extract_provider_event(
        _docusign_payload(
            "env-1",
            recipient_email="jane@counterparty.com",
            recipient_status="Completed",
        )
    )
    assert parsed["provider"] == "docusign"
    assert parsed["envelope_id"] == "env-1"
    assert parsed["status"] == "signed"
    assert parsed["recipient_email"] == "jane@counterparty.com"


def test_extract_adobe_event():
    parsed = extract_provider_event(
        {
            "agreementId": "agr-9",
            "status": "SIGNED",
        }
    )
    assert parsed["provider"] == "adobe_sign"
    assert parsed["envelope_id"] == "agr-9"
    assert parsed["status"] == "signed"


@pytest.mark.asyncio
async def test_webhook_marks_signer_signed_and_emits_outbox(
    db_session, test_org, test_agreement, test_user
):
    envelope = f"env-{uuid.uuid4().hex[:8]}"
    req = await _make_request(
        db_session, test_org.id, test_agreement.id, envelope, test_user.id
    )

    summary = await process_esign_webhook(
        db_session,
        payload=_docusign_payload(
            envelope,
            recipient_email="jane@counterparty.com",
            recipient_status="Completed",
        ),
    )
    assert summary["applied"] is True
    assert summary["to_status"] == "signed"
    assert summary["signature_request_id"] == str(req.id)

    await db_session.refresh(req)
    assert req.status == "signed"
    assert req.signed_at is not None


@pytest.mark.asyncio
async def test_webhook_is_idempotent(db_session, test_org, test_agreement, test_user):
    envelope = f"env-{uuid.uuid4().hex[:8]}"
    await _make_request(
        db_session, test_org.id, test_agreement.id, envelope, test_user.id
    )
    payload = _docusign_payload(envelope, event_id="evt-dup-1")

    first = await process_esign_webhook(db_session, payload=payload)
    assert first["applied"] is True

    second = await process_esign_webhook(db_session, payload=payload)
    assert second == {
        "applied": False,
        "duplicate": True,
        "event_id": "evt-dup-1",
    }


@pytest.mark.asyncio
async def test_webhook_unknown_envelope_raises(db_session):
    with pytest.raises(EsignWebhookError, match="No signature request"):
        await process_esign_webhook(
            db_session,
            payload=_docusign_payload("env-does-not-exist", event_id="evt-x1"),
        )


@pytest.mark.asyncio
async def test_webhook_recipient_mismatch_raises(
    db_session, test_org, test_agreement, test_user
):
    envelope = f"env-{uuid.uuid4().hex[:8]}"
    await _make_request(
        db_session, test_org.id, test_agreement.id, envelope, test_user.id
    )

    with pytest.raises(EsignWebhookError, match="does not match"):
        await process_esign_webhook(
            db_session,
            payload=_docusign_payload(
                envelope,
                recipient_email="someone@else.com",
                recipient_status="Completed",
            ),
        )


@pytest.mark.asyncio
async def test_decline_transition_emits_signature_declined(
    db_session, test_org, test_agreement, test_user
):
    envelope = f"env-{uuid.uuid4().hex[:8]}"
    await _make_request(
        db_session, test_org.id, test_agreement.id, envelope, test_user.id
    )

    summary = await process_esign_webhook(
        db_session,
        payload=_docusign_payload(
            envelope,
            recipient_email="jane@counterparty.com",
            recipient_status="Declined",
        ),
    )
    assert summary["to_status"] == "declined"
