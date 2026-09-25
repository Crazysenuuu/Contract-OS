"""Observation utilities (spec 3.15.12-3.15.15).

Payload integrity (3.15.13): every persisted observation carries a
``payload_hash`` computed over the *original* (pre-redaction) payload, so a
tampered stowed payload is detectable after redaction is applied for
storage. Hash uses BLAKE2b (stable, non-python-randomized, no collisions
with any other hash in the app).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone, date as date_type
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.monitoring.enums import ObservationStatus
from app.monitoring.exceptions import WebhookVerificationError

if TYPE_CHECKING:
    from app.monitoring.connectors.base import ExternalObservation

_HASH_BLOCKED_TOKENS = (
    "secret",
    "token",
    "api_key",
    "apikey",
    "password",
    "authorization",
    "bearer",
    "x-signature",
)


def canon_json(value: object) -> str:
    """Deterministic JSON: keys sorted, ensure dates are ISO."""
    def default(obj):
        if isinstance(obj, (datetime, date_type)):
            return obj.isoformat()
        if isinstance(obj, uuid.UUID):
            return str(obj)
        raise TypeError(f"Unserializable {type(obj)!r}")

    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=default, separators=(",", ":"))


def hash_payload(payload: dict | None) -> str:
    """BLAKE2b digest over the canonical original payload."""
    raw = canon_json(payload or {})
    return hashlib.blake2b(raw.encode("utf-8")).hexdigest()


def redact_payload(payload: dict) -> dict:
    """Strip credential-shaped fields before stowing (3.15.44)."""
    if not isinstance(payload, dict):
        return payload
    return {
        key: value
        for key, value in payload.items()
        if not any(token in key.lower() for token in _HASH_BLOCKED_TOKENS)
    }


def ensure_aware_utc(value) -> datetime:
    """Coerce a stored/queried datetime into a tz-aware UTC datetime."""
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc)


async def persist_observations(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    integration_id: uuid.UUID,
    monitoring_id: uuid.UUID,
    observations: list[ExternalObservation],
) -> list[uuid.UUID]:
    """Idempotently stow external observations (dedup on the 5-part key).

    Returns the ids of the newly inserted rows. Duplicates are ignored, so a
    retried fetch never double counts (3.15.14).
    """
    from app.monitoring.models import ExternalObservationRecord

    created: list[ExternalObservationRecord] = []
    for obs in observations:
        payload = dict(obs.payload) if isinstance(obs.payload, dict) else {}
        digest = hash_payload(payload)
        observed_at = ensure_aware_utc(obs.observed_at)

        existing = await db.scalar(
            select(ExternalObservationRecord.id).where(
                ExternalObservationRecord.integration_id == integration_id,
                ExternalObservationRecord.monitoring_id == monitoring_id,
                ExternalObservationRecord.external_id == obs.external_id,
                ExternalObservationRecord.observed_at == observed_at,
                ExternalObservationRecord.payload_hash == digest,
            )
        )
        if existing is not None:
            continue

        row = ExternalObservationRecord(
            organization_id=organization_id,
            integration_id=integration_id,
            monitoring_id=monitoring_id,
            external_id=obs.external_id[:500],
            resource_type=(obs.resource_type or "unknown")[:255],
            observed_at=observed_at,
            payload=redact_payload(payload),
            source_reference=dict(obs.source_reference) if isinstance(obs.source_reference, dict) else {},
            payload_hash=digest,
            status=ObservationStatus.VALIDATED.value,
        )
        db.add(row)
        created.append(row)

    if created:
        await db.flush()
    return [row.id for row in created]


async def verify_webhook_time_delivery(
    received_at: datetime,
    now: datetime,
    *,
    max_skew_seconds: int = 300,
) -> None:
    """Reject stale webhook deliveries (replay protection, 3.15.30)."""
    delta = abs((received_at - now).total_seconds())
    if delta > max_skew_seconds:
        raise WebhookVerificationError(
            f"Webhook timestamp skew {delta:.0f}s exceeds {max_skew_seconds}s"
        )