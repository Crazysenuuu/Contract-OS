"""Event outbox + real-time notification service (spec 1.14).

Domain services enqueue OutboxEvents in the same transaction as their
state change. The dispatcher marks events published (idempotently) and
delivers them: creating Notification records and pushing real-time updates
over WebSocket to connected users, with per-tenant channel isolation.

The WebSocket connection manager is in-memory per process; multi-process
deployments should back it with Redis pub/sub (see redis_channels helper
below) so a notification pushed on one worker reaches users connected to
another.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import WebSocket
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_outbox import OutboxEvent
from app.models.notification import Notification
from app.services.audit_service import record_event

_log = logging.getLogger(__name__)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime | None) -> datetime | None:
    """Normalize naive datetimes (SQLite) to aware UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# Outbox
# --------------------------------------------------------------------------

async def enqueue_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID | None = None,
    payload: dict | None = None,
) -> OutboxEvent:
    """Append an event to the outbox (same transaction as the state change)."""
    event = OutboxEvent(
        tenant_id=tenant_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload or {},
        status="pending",
        attempts=0,
        next_attempt_at=now_utc(),
    )
    db.add(event)
    await db.flush()
    return event


async def list_pending_events(
    db: AsyncSession,
    limit: int = 100,
) -> list[OutboxEvent]:
    result = await db.execute(
        select(OutboxEvent)
        .where(OutboxEvent.status == "pending")
        .order_by(OutboxEvent.created_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_outbox_stats(db: AsyncSession) -> dict:
    result = await db.execute(
        select(
            OutboxEvent.status,
            func.count(),
        ).group_by(OutboxEvent.status)
    )
    counts = {status: count for status, count in result.all()}
    return {
        "pending": counts.get("pending", 0),
        "published": counts.get("published", 0),
        "failed": counts.get("failed", 0),
        "total": sum(counts.values()),
    }


async def mark_event_failed(
    db: AsyncSession,
    event: OutboxEvent,
    error: str,
    *,
    retry_in_seconds: int = 60,
    max_attempts: int = 5,
) -> None:
    """Record a delivery failure (spec 1.23.22).

    Exhausted events are parked in a dead-letter status ('dead') instead of
    disappearing into 'failed'; they remain inspectable via
    GET /outbox?status=dead and re-drivable via the requeue endpoint.
    Retries use exponential backoff with a 15-minute cap.
    """
    from app.core.resilience import backoff_seconds, park_in_dlq

    event.attempts += 1
    event.last_error = error
    if park_in_dlq(event, error=error, max_attempts=max_attempts):
        pass  # parked as 'dead' with next_attempt_at cleared
    else:
        event.next_attempt_at = now_utc() + timedelta(
            seconds=max(retry_in_seconds, backoff_seconds(event.attempts))
        )
    await db.flush()


async def dispatch_event(
    db: AsyncSession,
    event: OutboxEvent,
) -> bool:
    """Deliver one event: publish it, create notifications, push real-time.

    Returns True on success. Delivery is best-effort and never blocks the
    domain transaction; failures mark the event for retry.
    """
    payload = event.payload or {}

    # 1. Create a notification record when the payload carries recipient
    #    info (email or user id). Notification contents are always built
    #    from authorized payload data — never from arbitrary client input.
    recipient_email = payload.get("recipient_email")
    recipient_user_id = payload.get("recipient_user_id")
    subject = payload.get("subject") or event.event_type
    if recipient_email and event.tenant_id is not None:
        notification = Notification(
            organization_id=event.tenant_id,
            agreement_id=(
                uuid.UUID(payload["agreement_id"])
                if payload.get("agreement_id")
                else None
            ),
            notification_type=_notification_type(event.event_type),
            to_email=recipient_email,
            subject=subject,
            status="sent",
            sent_at=now_utc(),
            metadata_={
                "outbox_event_id": str(event.id),
                "aggregate_type": event.aggregate_type,
                "aggregate_id": (
                    str(event.aggregate_id) if event.aggregate_id else None
                ),
                "payload": payload,
            },
        )
        db.add(notification)
        await db.flush()

    # 2. Push real-time to the recipient if connected.
    if recipient_user_id:
        user_id = uuid.UUID(str(recipient_user_id))
        await connection_manager.send_to_user_async(
            user_id,
            {
                "event_id": str(event.id),
                "event_type": event.event_type,
                "aggregate_type": event.aggregate_type,
                "aggregate_id": (
                    str(event.aggregate_id) if event.aggregate_id else None
                ),
                "notification_type": _notification_type(event.event_type),
                "subject": subject,
                "payload": _safe_payload(payload),
            },
        )

    # 2.5. Push to the recipient's registered devices via FCM (spec 2.02
    # §24-26). Best-effort and optional: disabled without credentials,
    # per-token failures cleaned up, never breaks the outbox lifecycle.
    if recipient_user_id:
        from app.services import push_sender_service

        push_result = await push_sender_service.send_push_to_user(
            db,
            user_id=uuid.UUID(str(recipient_user_id)),
            notification_id=str(event.id),
            event_type=event.event_type,
            notification_type=_notification_type(event.event_type),
            title=subject,
            body=str(payload.get("body") or payload.get("message") or subject),
            payload=payload,
        )
        if push_result.disabled:
            _log.debug("push disabled (no FCM credentials); event %s skipped", event.id)
    else:
        push_result = None

    # 2.6. SMS channel (spec §44: in-app, email, push, SMS, webhook).
    # Best-effort and optional: disabled without gateway credentials,
    # skipped for users without a registered phone, never breaks the
    # outbox lifecycle. Bodies are the confidential-notification style
    # (doc4 §14): subject + link pointer, no agreement terms.
    if recipient_user_id:
        from app.services import sms_sender_service

        sms_body = str(payload.get("sms_body") or subject)
        try:
            sms_result = await sms_sender_service.send_sms_to_user(
                db,
                user_id=uuid.UUID(str(recipient_user_id)),
                body=sms_body,
            )
            if sms_result.disabled:
                _log.debug(
                    "sms disabled (no gateway credentials); event %s skipped",
                    event.id,
                )
        except Exception as exc:  # noqa: BLE001 — SMS must never break the outbox
            sms_result = None
            _log.warning("sms delivery failed for event %s: %s", event.id, exc)
    else:
        sms_result = None

    # 3. Cross-process channel (Redis) when available — non-blocking.
    publish_redis_channel(
        event.tenant_id,
        {
            "event_type": event.event_type,
            "aggregate_id": (
                str(event.aggregate_id) if event.aggregate_id else None
            ),
        },
    )

    event.status = "published"
    event.published_at = now_utc()
    await db.flush()

    if event.tenant_id is not None:
        try:
            await record_event(
                db,
                tenant_id=event.tenant_id,
                actor_id=None,
                actor_type="system",
                action=f"OUTBOX_{event.event_type.upper()}",
                resource_type=event.aggregate_type,
                resource_id=event.aggregate_id,
                metadata_json={
                    "outbox_event_id": str(event.id),
                    "event_type": event.event_type,
                },
            )
        except Exception:
            # The outbox publish itself is authoritative; audit best-effort.
            pass
    await db.flush()
    return True


async def process_pending_events(db: AsyncSession, limit: int = 50) -> dict:
    """Publish + deliver up to ``limit`` pending events (idempotent)."""
    events = await list_pending_events(db, limit=limit)
    delivered = 0
    failed = 0
    for event in events:
        next_attempt = _ensure_aware(event.next_attempt_at)
        if next_attempt is not None and next_attempt > now_utc():
            continue
        try:
            ok = await dispatch_event(db, event)
            if ok:
                delivered += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            await mark_event_failed(db, event, str(exc))
    await db.flush()
    return {"delivered": delivered, "failed": failed, "processed": len(events)}


def _notification_type(event_type: str) -> str:
    """Map an outbox event type to a notification_type value."""
    mapping = {
        "agreement.status_changed": "workflow_transition",
        "negotiation.proposal_created": "change_requested",
        "negotiation.proposal_accepted": "agreement_accepted",
        "signature.requested": "signature_request",
        "signature.completed": "signature_completed",
        "approval.required": "approval_request",
        "obligation.reminder": "obligation_reminder",
        "compliance.violation": "compliance_violation",
    }
    return mapping.get(event_type, "workflow_transition")


def _safe_payload(payload: dict) -> dict:
    """Strip non-serializable/oversized fields before pushing to clients."""
    allowed = {
        "agreement_id",
        "agreement_title",
        "party_name",
        "change_id",
        "version_number",
        "obligation_id",
        "reason",
    }
    return {k: v for k, v in payload.items() if k in allowed}


# --------------------------------------------------------------------------
# WebSocket connection manager (in-memory, per process)
# --------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self) -> None:
        # user_id (str) -> set of WebSockets
        self._connections: dict[str, set[WebSocket]] = {}
        # org_id (str) -> set of user_ids (for org-scoped broadcast)
        self._org_users: dict[str, set[str]] = {}

    async def connect(
        self,
        websocket: WebSocket,
        user_id: uuid.UUID,
        org_id: uuid.UUID | None,
    ) -> None:
        await websocket.accept()
        key = str(user_id)
        self._connections.setdefault(key, set()).add(websocket)
        if org_id is not None:
            self._org_users.setdefault(str(org_id), set()).add(key)

    async def disconnect(
        self,
        websocket: WebSocket,
        user_id: uuid.UUID,
        org_id: uuid.UUID | None,
    ) -> None:
        key = str(user_id)
        conns = self._connections.get(key)
        if conns is not None:
            conns.discard(websocket)
            if not conns:
                self._connections.pop(key, None)
        if org_id is not None:
            users = self._org_users.get(str(org_id))
            if users is not None:
                users.discard(key)
                if not users:
                    self._org_users.pop(str(org_id), None)

    async def send_to_user_async(
        self,
        user_id: uuid.UUID,
        message: dict,
    ) -> int:
        """Send to all sockets of a user. Returns number of recipients."""
        conns = self._connections.get(str(user_id)) or set()
        for ws in list(conns):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                conns.discard(ws)
        return len(conns)

    def connected_user_count(self) -> int:
        return len(self._connections)


connection_manager = ConnectionManager()


# --------------------------------------------------------------------------
# Redis channel isolation (optional, non-blocking)
# --------------------------------------------------------------------------

def publish_redis_channel(
    tenant_id: uuid.UUID | None,
    message: dict,
) -> None:
    """Publish to a per-tenant Redis channel when Redis is configured.

    Deliberately best-effort: absence of Redis must never break delivery.
    A subscriber process forwards channel messages to the WebSocket
    manager for cross-worker propagation.
    """
    try:
        import os

        redis_url = os.environ.get("REDIS_URL")
        if not redis_url:
            return
        import redis

        r = redis.Redis.from_url(redis_url, decode_responses=True)
        channel = f"notify:{tenant_id}" if tenant_id else "notify:global"
        r.publish(channel, __import__("json").dumps(message, default=str))
    except Exception:  # noqa: BLE001
        return