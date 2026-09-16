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