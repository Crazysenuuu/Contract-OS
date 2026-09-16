"""E-signature provider webhook handling (spec 24.5).

Receives provider events (DocuSign Connect, Adobe Sign webhooks) and applies
them to local state:

  - envelope/recipient status changes update ``signature_requests.status``
  - terminal events are enqueued on the outbox so notifications, ERP
    webhooks, and analytics observe the same transition as native signing

DocuSign Connect posts JSON with ``envelopeId``/``envelopeStatus`` and
per-recipient ``recipientStatuses``; Adobe Sign posts ``agreementId`` with a
``status``. Both are normalized here, so the execution state machine never
needs provider-specific branches.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import SignatureRequest

_log = logging.getLogger(__name__)


class EsignWebhookError(Exception):
    """Raised when a provider webhook cannot be applied."""


# Provider status -> local signature_requests.status
_STATUS_MAP: dict[str, str] = {
    # DocuSign recipient statuses
    "sent": "sent",
    "delivered": "sent",
    "completed": "signed",
    "declined": "declined",
    "autoresponded": "sent",
    # Adobe Sign statuses
    "OUT_FOR_SIGNATURE": "sent",
    "SIGNED": "signed",
    "APPROVED": "signed",
    "REJECTED": "declined",
    "EXPIRED": "expired",
    "CANCELLED": "cancelled",
    # Generic
    "signed": "signed",
    "declined": "declined",
    "cancelled": "cancelled",
    "expired": "expired",
}


def normalize_provider_status(raw: str | None) -> str | None:
    """Map a provider envelope/recipient status to the local vocabulary."""
    if not raw:
        return None
    return _STATUS_MAP.get(raw) or _STATUS_MAP.get(raw.lower()) or _STATUS_MAP.get(raw.upper())


def extract_provider_event(payload: dict) -> dict:
    """Normalize a DocuSign Connect or Adobe Sign webhook payload.

    Returns ``{"provider", "event_id", "envelope_id", "status",
    "recipient_email", "recipient_status"}`` — envelope-level status wins
    only when no recipient-specific status is present.
    """
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    envelope_id = (
        data.get("envelopeId")
        or data.get("envelope_id")
        or data.get("agreementId")
        or data.get("agreement_id")
        or data.get("id")
    )
    event_id = (
        payload.get("event_id")
        or payload.get("eventId")
        or data.get("eventId")
        or (f"{envelope_id}:{payload.get('event') or payload.get('event_type') or 'unknown'}")
    )
    provider = (
        "docusign"
        if "envelopeId" in data or "envelopeStatus" in data
        else "adobe_sign" if "agreementId" in data or "status" in data and "envelopeId" not in data
        else "unknown"
    )

    # Recipient-level status (preferred) then envelope-level.
    recipient_email = None
    recipient_status = None
    for rs in data.get("recipientStatuses") or []:
        email = rs.get("email")
        status = normalize_provider_status(rs.get("status"))
        if email and status:
            recipient_email, recipient_status = email, status
            break

    envelope_status = normalize_provider_status(
        data.get("envelopeStatus") or data.get("status")
    )

    return {
        "provider": provider,
        "event_id": str(event_id or ""),
        "envelope_id": str(envelope_id or ""),
        "status": recipient_status or envelope_status,
        "recipient_email": recipient_email,
        "recipient_status": recipient_status,
        "envelope_status": envelope_status,
    }


def _terminal(status: str | None) -> bool:
    return status in ("signed", "declined", "cancelled", "expired")


async def process_esign_webhook(
    db: AsyncSession,
    *,
    payload: dict,
) -> dict:
    """Apply a verified provider webhook to local state.

    Returns a summary dict: ``{"applied", "signature_request_id",
    "from_status", "to_status", "event_id", "duplicate"}``.
    """
    parsed = extract_provider_event(payload)
    event_id = parsed["event_id"]

    from app.models.incoming_webhook import IncomingWebhookEvent

    # Idempotency: providers retry; the same event must not re-apply.
    existing = (
        await db.execute(
            select(IncomingWebhookEvent).where(
                IncomingWebhookEvent.provider == f"esign:{parsed['provider']}",
                IncomingWebhookEvent.event_id == event_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {"applied": False, "duplicate": True, "event_id": event_id}

    if not parsed["envelope_id"]:
        raise EsignWebhookError("Webhook payload has no envelope/agreement id")

    # Locate the local request. The envelope id is stored in
    # signature_requests.metadata_json.provider_envelope_id (written when the
    # envelope is created); fall back to scanning the JSON field.
    result = await db.execute(
        select(SignatureRequest).where(
            SignatureRequest.tenant_id.isnot(None),
        )
    )
    candidates = result.scalars().all()
    target = None
    for req in candidates:
        meta = req.metadata_json or {}
        if meta.get("provider_envelope_id") == parsed["envelope_id"]:
            target = req
            break

    if target is None:
        raise EsignWebhookError(
            f"No signature request found for envelope {parsed['envelope_id']}"
        )

    old_status = target.status
    new_status = parsed["status"]

    # Recipient-scoped updates only move that signer; envelope-level
    # terminal events complete the request as a whole.
    if parsed["recipient_email"] and target.email.lower() != parsed["recipient_email"].lower():
        raise EsignWebhookError(
            "Webhook recipient does not match the signature request"
        )

    applied = False
    if new_status and new_status != old_status:
        target.status = new_status
        if new_status == "signed":
            target.signed_at = datetime.now(timezone.utc)
        applied = True

    # Record the delivery for idempotency + audit.
    import json

    db.add(
        IncomingWebhookEvent(
            provider=f"esign:{parsed['provider']}",
            event_id=event_id,
            event_type=str(payload.get("event") or payload.get("event_type") or "esign.status"),
            payload=json.dumps(payload, default=str),
            received_at=datetime.now(timezone.utc),
        )
    )

    # Terminal transitions become outbox events so the wider system
    # (notifications, ERP webhooks, analytics) observes them like native
    # signing events.
    if applied and _terminal(new_status):
        from app.services.event_outbox_service import enqueue_event

        await enqueue_event(
            db,
            tenant_id=target.tenant_id,
            event_type=f"signature.{new_status}",
            aggregate_type="signature_request",
            aggregate_id=target.id,
            payload={
                "agreement_id": str(target.agreement_id),
                "signer_email": target.email,
                "signer_name": target.name,
                "provider": parsed["provider"],
                "provider_envelope_id": parsed["envelope_id"],
                "reason": f"Signing status via {parsed['provider']}: {old_status} -> {new_status}",
            },
        )

    _log.info(
        "esign webhook applied: envelope=%s provider=%s %s -> %s (applied=%s)",
        parsed["envelope_id"], parsed["provider"], old_status, new_status, applied,
    )
    return {
        "applied": applied,
        "duplicate": False,
        "event_id": event_id,
        "signature_request_id": str(target.id),
        "from_status": old_status,
        "to_status": new_status or old_status,
    }
