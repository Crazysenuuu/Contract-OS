"""Tests for signer OTP step-up authentication (spec 24.4)."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.otp_challenge import OTPChallenge
from app.services.otp_service import (
    OTPError,
    issue_otp,
    verify_otp,
)


@pytest.mark.asyncio
async def test_issue_and_verify_otp(db_session, test_org):
    challenge = await issue_otp(
        db_session,
        organization_id=test_org.id,
        signature_request_id=uuid.uuid4(),
        channel="email",
        email="signer@example.com",
    )
    await db_session.flush()

    assert challenge["challenge_id"]
    code = challenge["debug_code"]
    assert len(code) == 6 and code.isdigit()

    row = (
        await db_session.execute(
            select(OTPChallenge).where(OTPChallenge.id == challenge["challenge_id"])
        )
    ).scalar_one()
    # Raw code is never stored.
    assert row.code_hash != code

    verified = await verify_otp(
        db_session,
        challenge_id=row.id,
        code=code,
    )
    assert verified.consumed is True
    assert verified.verified_at is not None


@pytest.mark.asyncio
async def test_wrong_code_fails(db_session, test_org):
    challenge = await issue_otp(
        db_session,
        organization_id=test_org.id,
        signature_request_id=uuid.uuid4(),
        channel="email",
        email="signer@example.com",
    )
    await db_session.flush()

    with pytest.raises(OTPError) as exc:
        await verify_otp(
            db_session,
            challenge_id=challenge["challenge_id"],
            code="000000",
        )
    assert "Invalid code" in str(exc.value)


@pytest.mark.asyncio
async def test_code_expires(db_session, test_org):
    challenge = await issue_otp(
        db_session,
        organization_id=test_org.id,
        signature_request_id=uuid.uuid4(),
        channel="email",
        email="signer@example.com",
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(OTPChallenge).where(OTPChallenge.id == challenge["challenge_id"])
        )
    ).scalar_one()
    from datetime import timedelta
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.flush()

    with pytest.raises(OTPError) as exc:
        await verify_otp(
            db_session,
            challenge_id=row.id,
            code=challenge["debug_code"],
        )
    assert "expired" in str(exc.value)


@pytest.mark.asyncio
async def test_attempt_limit(db_session, test_org):
    challenge = await issue_otp(
        db_session,
        organization_id=test_org.id,
        signature_request_id=uuid.uuid4(),
        channel="email",
        email="signer@example.com",
        max_attempts=2,
    )
    await db_session.flush()

    with pytest.raises(OTPError):
        await verify_otp(db_session, challenge_id=challenge["challenge_id"], code="000001")
    with pytest.raises(OTPError):
        await verify_otp(db_session, challenge_id=challenge["challenge_id"], code="000002")
    with pytest.raises(OTPError) as exc:
        await verify_otp(db_session, challenge_id=challenge["challenge_id"], code="000003")
    assert "Too many attempts" in str(exc.value)


@pytest.mark.asyncio
async def test_sms_otp_delivers_via_sms_gateway(db_session, test_org):
    """Spec 24.4 — the SMS channel sends through the provider-agnostic
    gateway and records the provider message id as the delivery ref."""
    from unittest.mock import AsyncMock, patch

    from app.services.sms_sender_service import SmsDeliveryResult

    sent: dict = {}

    async def _fake_send(to_phone, body, *, sender_id=None):
        sent["to_phone"] = to_phone
        sent["body"] = body
        return SmsDeliveryResult(sent=True, provider_message_id="sms-otp-1")

    with patch("app.services.sms_sender_service.send_sms", AsyncMock(side_effect=_fake_send)):
        challenge = await issue_otp(
            db_session,
            organization_id=test_org.id,
            signature_request_id=uuid.uuid4(),
            channel="sms",
            phone="+15550001000",
        )
        await db_session.flush()

    assert sent["to_phone"] == "+15550001000"
    assert challenge["debug_code"] in sent["body"]

    row = (
        await db_session.execute(
            select(OTPChallenge).where(OTPChallenge.id == challenge["challenge_id"])
        )
    ).scalar_one()
    assert row.channel == "sms"
    assert row.delivery_ref == "sms-otp-1"

    verified = await verify_otp(db_session, challenge_id=row.id, code=challenge["debug_code"])
    assert verified.consumed is True


@pytest.mark.asyncio
async def test_sms_channel_rejects_missing_phone(db_session, test_org):
    with pytest.raises(OTPError, match="requires a phone"):
        await issue_otp(
            db_session,
            organization_id=test_org.id,
            signature_request_id=uuid.uuid4(),
            channel="sms",
            email="signer@example.com",
        )


@pytest.mark.asyncio
async def test_sms_disabled_gateway_still_keeps_challenge(db_session, test_org):
    """A missing SMS gateway credential must not lose the challenge — the
    dev/log fallback still returns the code via debug_code."""
    from unittest.mock import AsyncMock, patch

    from app.services.sms_sender_service import SmsDeliveryResult

    with patch(
        "app.services.sms_sender_service.send_sms",
        AsyncMock(return_value=SmsDeliveryResult(sent=False, disabled=True)),
    ):
        challenge = await issue_otp(
            db_session,
            organization_id=test_org.id,
            signature_request_id=uuid.uuid4(),
            channel="sms",
            phone="+15550001000",
        )
        await db_session.flush()

    assert len(challenge["debug_code"]) == 6
    row = (
        await db_session.execute(
            select(OTPChallenge).where(OTPChallenge.id == challenge["challenge_id"])
        )
    ).scalar_one()
    assert row.delivery_ref is None


@pytest.mark.asyncio
async def test_otp_api_flow(client, auth_headers, test_org, test_agreement):
    """OTP endpoints require an existing signature request, so this verifies
    the endpoint wiring rejects missing requests (404) rather than 500."""
    res = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/signature-requests/"
        f"{uuid.uuid4()}/otp/issue",
        json={"channel": "email"},
        headers=auth_headers,
    )
    assert res.status_code in (404, 403, 401)