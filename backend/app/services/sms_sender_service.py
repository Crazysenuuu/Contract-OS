"""SMS notification sender (spec §44: in-app, email, push, SMS, webhook).

Delivers short text notifications through a generic HTTP SMS gateway. The
provider contract is deliberately provider-agnostic:

    POST {sms_api_base_url}
    Authorization: Bearer {sms_api_key}
    {"to": "<phone>", "from": "<sender_id>", "text": "<body>"}

which most provider bridges (Twilio proxy services, Africa's Talking,
local GSM modems behind an HTTP facade) can serve directly.

Design constraints, mirroring the push channel:

  - Optional by construction: without ``sms_api_base_url`` /
    ``sms_api_key`` the sender reports itself disabled and delivery is
    skipped — email, push and WebSocket remain the delivery paths. A
    missing credential must never break the outbox.
  - Best-effort: transport errors are returned as a failed result, never
    raised into the outbox dispatch loop.
  - Plain, confidential content only (doc4 §14): callers pass a short
    body — agreement titles and action links only, never clause terms.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import get_settings_lazy

logger = logging.getLogger(__name__)


@dataclass
class SmsDeliveryResult:
    """Outcome of one SMS send attempt."""

    sent: bool
    disabled: bool = False
    provider_message_id: str | None = None
    error: str | None = None


def _truncate_body(body: str) -> str:
    """SMS segments cap content hard; keep the message inside one segment."""
    return body[:300]


async def send_sms(
    to_phone: str,
    body: str,
    *,
    sender_id: str | None = None,
) -> SmsDeliveryResult:
    """Send one SMS through the configured gateway.

    Returns ``disabled=True`` when no gateway is configured — callers must
    treat that as "channel not active", not as an error.
    """
    settings = get_settings_lazy()
    base_url = settings.sms_api_base_url
    api_key = settings.sms_api_key

    if not to_phone or not body:
        return SmsDeliveryResult(sent=False, error="missing_recipient_or_body")
    if not base_url or not api_key:
        return SmsDeliveryResult(sent=False, disabled=True)

    headers = {
        "Authorization": f"Bearer {api_key.get_secret_value()}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": to_phone,
        "from": sender_id or settings.sms_sender_id,
        "text": _truncate_body(body),
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.sms_timeout_seconds,
        ) as client:
            response = await client.post(base_url, json=payload, headers=headers)
            response.raise_for_status()
            data = _safe_json(response)
            return SmsDeliveryResult(
                sent=True,
                provider_message_id=(
                    data.get("message_id")
                    or data.get("sid")
                    or data.get("messageId")
                    if isinstance(data, dict)
                    else None
                ),
            )
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "SMS gateway rejected send: status=%s body=%s",
            exc.response.status_code,
            exc.response.text[:200],
        )
        return SmsDeliveryResult(
            sent=False, error=f"http_{exc.response.status_code}"
        )
    except httpx.HTTPError as exc:
        logger.warning("SMS gateway unreachable: %s", exc)
        return SmsDeliveryResult(sent=False, error="transport_error")
    except Exception as exc:  # never break the outbox on SMS problems
        logger.warning("Unexpected SMS delivery failure: %s", exc)
        return SmsDeliveryResult(sent=False, error="unexpected_error")


def _safe_json(response: httpx.Response) -> dict | None:
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def send_sms_to_user(
    db,
    *,
    user_id,
    body: str,
) -> SmsDeliveryResult:
    """Resolve the recipient's phone number and send one SMS.

    Users without a verified phone number are skipped silently — the SMS
    channel is supplementary and must not produce error noise for the many
    users who will never register a phone.
    """
    from sqlalchemy import select

    from app.models.notification import NotificationPreference
    from app.models.user import User

    row = await db.scalar(select(User).where(User.id == user_id))
    phone = getattr(row, "phone", None) if row is not None else None
    if not phone:
        return SmsDeliveryResult(sent=False, error="no_phone_number")

    prefs = await db.scalar(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
        )
    )
    if prefs is not None and not prefs.sms_enabled:
        return SmsDeliveryResult(sent=False, error="user_opted_out")

    return await send_sms(phone, body)
