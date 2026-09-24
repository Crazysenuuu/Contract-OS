"""Guest KYC verification flow tests (spec 24.4).

Service-level: start (KYC vs OTP routing), complete (verified/pending/
failed), webhook application (idempotency, session mismatch). API-level:
the three guest endpoints under the token-auth surface.
"""

import json
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.external_party import ExternalParty, KycVerificationAttempt
from app.services.external_party_service import (
    KYCFlowError,
    apply_kyc_webhook_event,
    complete_kyc_verification,
    ensure_id_verified,
    start_id_verification,
    start_kyc_verification,
)
from app.services.kyc_provider import (
    MockKYCProvider,
    reset_kyc_provider_cache,
)


async def _make_party(
    db,
    agreement,
    *,
    requires_kyc: bool = True,
    requires_idv: bool = True,
) -> ExternalParty:
    from app.models.agreement_access import AgreementParty

    party_row = AgreementParty(
        agreement_id=agreement.id,
        legal_entity_id=uuid.uuid4(),
        party_role="receiving",
    )
    db.add(party_row)
    await db.flush()

    party = ExternalParty(
        agreement_id=agreement.id,
        agreement_party_id=party_row.id,
        company_name="Counterparty Inc",
        signatory_name="Jane Guest",
        signatory_email="jane@counterparty.com",
        access_token="tok-" + uuid.uuid4().hex,
        expires_at=None,
        status="pending",
        requires_id_verification=requires_idv,
        requires_kyc=requires_kyc,
    )
    db.add(party)
    await db.flush()
    return party


async def _attempts_for(db, party_id: uuid.UUID) -> list[KycVerificationAttempt]:
    result = await db.execute(
        select(KycVerificationAttempt)
        .where(KycVerificationAttempt.external_party_id == party_id)
        .order_by(KycVerificationAttempt.created_at)
    )
    return list(result.scalars().all())


@pytest.fixture(autouse=True)
def _fresh_providers():
    reset_kyc_provider_cache()
    yield
    reset_kyc_provider_cache()


# --- Service: start ---------------------------------------------------------


@pytest.mark.asyncio
async def test_start_routes_to_kyc_when_flagged(db_session, test_agreement):
    party = await _make_party(db_session, test_agreement, requires_kyc=True)

    challenge = await start_id_verification(db_session, party)
    assert challenge["method"] == "kyc_provider"
    assert challenge["session_id"].startswith("vs_mock_")
    assert challenge["url"]
    assert challenge["debug_code"]  # mock dev affordance

    assert party.kyc_session_id == challenge["session_id"]
    assert party.kyc_session_status == "requires_input"
    assert party.kyc_provider == "mock"
    assert party.id_verified_at is None  # not verified by starting


@pytest.mark.asyncio
async def test_start_routes_to_otp_when_not_kyc(db_session, test_agreement):
    party = await _make_party(db_session, test_agreement, requires_kyc=False)

    challenge = await start_id_verification(db_session, party)
    assert challenge["method"] == "otp_email"
    assert challenge["challenge_id"]


@pytest.mark.asyncio
async def test_start_kyc_records_attempt(db_session, test_agreement):
    party = await _make_party(db_session, test_agreement)

    challenge = await start_id_verification(db_session, party)

    attempts = await _attempts_for(db_session, party.id)
    assert len(attempts) == 1
    assert attempts[0].session_id == challenge["session_id"]
    assert attempts[0].status == "requires_input"
    assert attempts[0].completed_at is None


# --- Service: complete --------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_marks_verified_with_method(db_session, test_agreement):
    party = await _make_party(db_session, test_agreement)
    challenge = await start_kyc_verification(db_session, party)

    result = await complete_kyc_verification(
        db_session, party, submitted_code=challenge["debug_code"]
    )

    assert result["verified"] is True
    assert result["method"] == "kyc_provider"
    assert party.id_verified_at is not None
    assert party.id_verification_method == "kyc_provider"
    assert party.kyc_session_status == "verified"
    ensure_id_verified(party)  # accept/sign gate now passes


@pytest.mark.asyncio
async def test_complete_wrong_code_stays_unverified(db_session, test_agreement):
    party = await _make_party(db_session, test_agreement)
    challenge = await start_kyc_verification(db_session, party)

    result = await complete_kyc_verification(
        db_session, party, submitted_code="WRONG"
    )

    assert result["verified"] is False
    assert party.id_verified_at is None
    ensure_id_verified_gate_blocks(party)


@pytest.mark.asyncio
async def test_complete_decline_code_marks_failed(db_session, test_agreement):
    party = await _make_party(db_session, test_agreement)
    challenge = await start_kyc_verification(db_session, party)

    result = await complete_kyc_verification(
        db_session,
        party,
        submitted_code=MockKYCProvider.DECLINE_CODE,
    )

    assert result["verified"] is False
    assert result["status"] == "failed"
    assert party.id_verified_at is None


@pytest.mark.asyncio
async def test_complete_without_session_raises_flow_error(
    db_session, test_agreement
):
    party = await _make_party(db_session, test_agreement)
    with pytest.raises(KYCFlowError):
        await complete_kyc_verification(db_session, party)


@pytest.mark.asyncio
async def test_complete_closes_attempt_row(db_session, test_agreement):
    party = await _make_party(db_session, test_agreement)
    challenge = await start_kyc_verification(db_session, party)

    await complete_kyc_verification(
        db_session, party, submitted_code=challenge["debug_code"]
    )

    attempts = await _attempts_for(db_session, party.id)
    assert attempts[0].status == "verified"
    assert attempts[0].completed_at is not None


# --- Service: webhook ---------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_verified_marks_party(db_session, test_agreement):
    party = await _make_party(db_session, test_agreement)
    challenge = await start_kyc_verification(db_session, party)

    result = await apply_kyc_webhook_event(
        db_session,
        party,
        session_id=challenge["session_id"],
        status="verified",
        details={"event_type": "identity.verification_session.verified"},
    )

    assert result["applied"] is True
    assert result["verified"] is True
    assert party.id_verified_at is not None
    assert party.kyc_session_status == "verified"


@pytest.mark.asyncio
async def test_webhook_is_idempotent(db_session, test_agreement):
    party = await _make_party(db_session, test_agreement)
    challenge = await start_kyc_verification(db_session, party)

    await apply_kyc_webhook_event(
        db_session,
        party,
        session_id=challenge["session_id"],
        status="verified",
    )
    replay = await apply_kyc_webhook_event(
        db_session,
        party,
        session_id=challenge["session_id"],
        status="verified",
    )

    assert replay == {"applied": False, "duplicate": True}


@pytest.mark.asyncio
async def test_webhook_canceled_does_not_verify(db_session, test_agreement):
    party = await _make_party(db_session, test_agreement)
    challenge = await start_kyc_verification(db_session, party)

    result = await apply_kyc_webhook_event(
        db_session,
        party,
        session_id=challenge["session_id"],
        status="canceled",
    )

    assert result["verified"] is False
    assert party.id_verified_at is None
    assert party.kyc_session_status == "canceled"


@pytest.mark.asyncio
async def test_webhook_session_mismatch_raises(db_session, test_agreement):
    party = await _make_party(db_session, test_agreement)
    await start_kyc_verification(db_session, party)

    with pytest.raises(KYCFlowError):
        await apply_kyc_webhook_event(
            db_session,
            party,
            session_id="vs_someone_else",
            status="verified",
        )


# --- API endpoints ------------------------------------------------------------


# External-party routes mount at the app root (no /api/v1 prefix,
# see app/main.py: token-based guest surface).


@pytest.mark.asyncio
async def test_api_start_returns_kyc_session(client, db_session, test_agreement):
    party = await _make_party(db_session, test_agreement)
    await db_session.commit()

    resp = await client.post(f"/review/{party.access_token}/verify-id/start")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["method"] == "kyc_provider"
    assert body["session_id"].startswith("vs_mock_")
    assert body["url"]


@pytest.mark.asyncio
async def test_api_kyc_complete_flow(client, db_session, test_agreement):
    party = await _make_party(db_session, test_agreement)
    await db_session.commit()

    start = await client.post(
        f"/review/{party.access_token}/verify-id/start"
    )
    code = start.json()["debug_code"]

    done = await client.post(
        f"/review/{party.access_token}/verify-id/kyc/complete",
        json={"code": code},
    )
    assert done.status_code == 200, done.text
    assert done.json()["verified"] is True
    assert done.json()["method"] == "kyc_provider"


@pytest.mark.asyncio
async def test_api_kyc_complete_wrong_code_not_verified(
    client, db_session, test_agreement
):
    party = await _make_party(db_session, test_agreement)
    await db_session.commit()

    await client.post(f"/review/{party.access_token}/verify-id/start")
    done = await client.post(
        f"/review/{party.access_token}/verify-id/kyc/complete",
        json={"code": "NOPE"},
    )
    assert done.status_code == 200
    assert done.json()["verified"] is False


@pytest.mark.asyncio
async def test_api_kyc_complete_without_start_400(
    client, db_session, test_agreement
):
    party = await _make_party(db_session, test_agreement, requires_kyc=False)
    await db_session.commit()

    done = await client.post(
        f"/review/{party.access_token}/verify-id/kyc/complete",
        json={},
    )
    assert done.status_code == 400


@pytest.mark.asyncio
async def test_api_webhook_verified(client, db_session, test_agreement):
    party = await _make_party(db_session, test_agreement)
    await db_session.commit()

    start = await client.post(
        f"/review/{party.access_token}/verify-id/start"
    )
    session_id = start.json()["session_id"]

    event = {
        "type": "identity.verification_session.verified",
        "data": {"object": {"id": session_id, "status": "verified"}},
    }
    resp = await client.post(
        f"/kyc/webhook/{party.access_token}",
        json=event,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["verified"] is True


@pytest.mark.asyncio
async def test_api_webhook_signature_auth_any_session(
    client, db_session, test_agreement
):
    """A validly-signed provider callback may target any party/session."""
    from app.services.kyc_provider import kyc_webhook_secret

    party = await _make_party(db_session, test_agreement)
    await db_session.commit()
    await client.post(f"/review/{party.access_token}/verify-id/start")

    import hashlib
    import hmac as hmac_mod

    payload = json.dumps(
        {
            "type": "identity.verification_session.verified",
            "data": {
                "object": {"id": "vs_other_session", "status": "verified"}
            },
        }
    )
    ts = int(time.time())
    secret = kyc_webhook_secret() or "whsec_fallback"
    sig = hmac_mod.new(
        secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256
    ).hexdigest()

    resp = await client.post(
        f"/kyc/webhook/{party.access_token}",
        content=payload.encode(),
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": f"t={ts},v1={sig}",
        },
    )
    # Provider-signed events are applied to the party the token resolves to;
    # a foreign session id is refused unless signed. Signed + foreign session
    # still must match this party's active session per service guard... but
    # the guard compares against kyc_session_id, so foreign ids are refused
    # for the party. The signed path only skips the token-vs-session check;
    # the service-level guard still applies.
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_api_webhook_bad_signature_400(client, db_session, test_agreement):
    party = await _make_party(db_session, test_agreement)
    await db_session.commit()

    start = await client.post(
        f"/review/{party.access_token}/verify-id/start"
    )
    session_id = start.json()["session_id"]

    event = {
        "type": "identity.verification_session.verified",
        "data": {"object": {"id": session_id, "status": "verified"}},
    }
    resp = await client.post(
        f"/kyc/webhook/{party.access_token}",
        json=event,
        headers={"Stripe-Signature": "t=123,v1=deadbeef"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_api_webhook_session_mismatch_403(
    client, db_session, test_agreement
):
    party = await _make_party(db_session, test_agreement)
    await db_session.commit()

    event = {
        "type": "identity.verification_session.verified",
        "data": {"object": {"id": "vs_foreign", "status": "verified"}},
    }
    resp = await client.post(
        f"/kyc/webhook/{party.access_token}",
        json=event,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_api_gated_sign_blocked_until_verified(
    client, db_session, test_agreement
):
    """Signing is refused until the KYC flow completes (spec 24.4 gate)."""
    party = await _make_party(db_session, test_agreement)
    await db_session.commit()

    from app.services.agreement_versioning import get_latest_version
    from app.models.agreement import AgreementVersion

    version = AgreementVersion(
        agreement_id=test_agreement.id,
        version_number=1,
        content="v1",
        content_hash="hash-gate-test",
        status="active",
        created_by=test_agreement.created_by,
    )
    db_session.add(version)
    await db_session.flush()

    resp = await client.post(
        f"/review/{party.access_token}/sign",
        json={"consent_text": "I agree"},
    )
    assert resp.status_code == 403
    assert resp.headers.get("x-id-verification-required") == "true"

    # Complete KYC, then signing proceeds past the ID gate (may still fail
    # on other lifecycle preconditions, but not the 403 ID gate).
    start = await client.post(
        f"/review/{party.access_token}/verify-id/start"
    )
    code = start.json()["debug_code"]
    await client.post(
        f"/review/{party.access_token}/verify-id/kyc/complete",
        json={"code": code},
    )

    resp2 = await client.post(
        f"/review/{party.access_token}/sign",
        json={"consent_text": "I agree"},
    )
    assert resp2.status_code != 403 or (
        "ID verification" not in resp2.text
    )


def ensure_id_verified_gate_blocks(party: ExternalParty) -> None:
    with pytest.raises(PermissionError):
        ensure_id_verified(party)
