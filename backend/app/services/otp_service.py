"""One-time passcode service for signer step-up authentication (spec 24.4).

Generates a short-lived code, stores only its SHA-256 hash, delivers it via
the email service, and verifies presented codes with attempt limiting.

The raw code is never persisted; only ``code_hash`` is stored, so a database
leak does not expose usable codes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.otp_challenge import OTPChallenge

# Codes are 6 digits, valid for 10 minutes.
OTP_LENGTH = 6
OTP_TTL_SECONDS = 600
OTP_MAX_ATTEMPTS = 5


class OTPError(Exception):
    """Raised for OTP lifecycle errors."""


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def issue_otp(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    signature_request_id: uuid.UUID,
    channel: str = "email",
    email: str | None = None,
    phone: str | None = None,
    max_attempts: int = OTP_MAX_ATTEMPTS,
) -> dict:
    """Generate, store (hashed), and deliver a one-time code.

    Returns the challenge id, expiry, and the raw code ONLY for the
    caller to embed in the delivery mechanism. Delivery follows the chosen
    channel: email via the email service, SMS via the provider-agnostic
    gateway. Both are optional-by-construction — a missing credential skips
    delivery but never loses the challenge (the dev/log fallback echoes the
    code through ``debug_code``).
    """
    code = "".join(secrets.choice("0123456789") for _ in range(OTP_LENGTH))
    expires_at = _utcnow() + timedelta(seconds=OTP_TTL_SECONDS)

    if channel == "sms" and not phone:
        raise OTPError("sms channel requires a phone number")

    challenge = OTPChallenge(
        organization_id=organization_id,
        signature_request_id=signature_request_id,
        channel=channel,
        code_hash=_hash_code(code),
        expires_at=expires_at,
        max_attempts=max_attempts,
        attempts=0,
        consumed=False,
    )
    db.add(challenge)
    await db.flush()

    # Deliver the code. The email service logs when SendGrid is absent, so
    # dev environments can read the code from the log; the SMS gateway
    # reports disabled without credentials. Delivery failure must not lose
    # the challenge — log-only fallback.
    delivery_ref = None
    try:
        if channel == "sms":
            from app.services.sms_sender_service import send_sms

            result = await send_sms(
                phone or "",
                f"Your ContractOS verification code is {code}",
            )
            if result.sent:
                delivery_ref = result.provider_message_id
        else:
            from app.services.email_service import EmailService

            service = EmailService()
            result = service.send_email(
                to_email=email or "",
                subject="Your verification code",
                template_name="verification_code",
                template_data={"code": code},
            )
            if result:
                delivery_ref = getattr(result, "message_id", None) or None
    except Exception:
        pass

    challenge.delivery_ref = delivery_ref
    await db.flush()

    return {
        "challenge_id": str(challenge.id),
        "expires_at": expires_at.isoformat(),
        # Raw code is returned here for the dev/log fallback path only;
        # production delivery goes through email/sms and callers should
        # discard it immediately.
        "debug_code": code,
    }


async def verify_otp(
    db: AsyncSession,
    *,
    challenge_id: uuid.UUID,
    code: str,
) -> OTPChallenge:
    """Verify a presented code against a challenge.

    Raises OTPError with a specific reason on failure. A successful
    verification marks the challenge consumed and returns it so the caller
    can record ``identity_method='otp'`` on the signer record.
    """
    result = await db.execute(
        select(OTPChallenge).where(OTPChallenge.id == challenge_id)
    )
    challenge = result.scalar_one_or_none()
    if challenge is None:
        raise OTPError("Challenge not found")

    if challenge.consumed:
        raise OTPError("Challenge already used")

    if challenge.verified_at is not None:
        raise OTPError("Challenge already verified")

    expires_at = challenge.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if _utcnow() > expires_at:
        raise OTPError("Code has expired")

    if challenge.attempts >= challenge.max_attempts:
        raise OTPError("Too many attempts; request a new code")

    challenge.attempts += 1

    if not hmac.compare_digest(_hash_code(code), challenge.code_hash):
        await db.flush()
        remaining = challenge.max_attempts - challenge.attempts
        raise OTPError(f"Invalid code; {remaining} attempt(s) remaining")

    challenge.consumed = True
    challenge.verified_at = _utcnow()
    await db.flush()
    return challenge


async def get_last_challenge(
    db: AsyncSession,
    *,
    signature_request_id: uuid.UUID,
) -> OTPChallenge | None:
    result = await db.execute(
        select(OTPChallenge)
        .where(OTPChallenge.signature_request_id == signature_request_id)
        .order_by(OTPChallenge.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()