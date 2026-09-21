"""Cross-process WebSocket fanout (spec 1.14.21-22).

The WebSocket ``ConnectionManager`` tracks sockets per API process, but
outbox events are dispatched from the Celery worker — so a "signature
completed" push could only ever reach browsers connected to the worker's
own (empty) connection table. Redis pub/sub closes that gap:

1. ``dispatch_event`` calls ``publish_redis_channel`` (already wired), now
   backed by the shared StateStore.
2. Every API process runs a background subscriber task (started from the
   FastAPI lifespan) that listens on ``notify:*`` channels and forwards
   each message to the sockets its own process holds.

Envelope contract (published by ``publish_redis_channel``):

    {
      "channel": "notify:<tenant-id>" | "notify:global",
      "user_ids": ["<uuid>", ...],      # target users on this channel
      "message": { ...original payload... },
    }

Messages without ``user_ids`` are ignored (legacy/foreign payloads) to
avoid leaking arbitrary published data to connected clients. Delivery is
best-effort: Redis absence or an error degrades to in-process-only push,
never breaks the outbox lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from app.core.distributed_state import get_state_store
from app.services.event_outbox_service import connection_manager

logger = logging.getLogger(__name__)

SUBSCRIBE_PATTERN = "notify:*"

# Per-process identity: the dispatcher that published an envelope already
# pushed to its own locally-connected sockets directly (dispatch_event step
# 2), so the subscriber ignores self-published envelopes to avoid double
# delivery. Clients also dedupe by event_id; this keeps it correct server-side.
ORIGIN_ID = uuid.uuid4().hex

_task: asyncio.Task | None = None


async def _handle_message(raw: str | bytes) -> None:
    """Forward one pub/sub message to local sockets.

    Runs inside the API process's event loop — the same loop that owns the
    WebSocket connections — so ``send_to_user_async`` can be awaited
    directly.
    """
    try:
        if isinstance(raw, bytes):
            raw = raw.decode()
        envelope = json.loads(raw)
        if envelope.get("origin") == ORIGIN_ID:
            return  # our own publish — local sockets were already pushed
        user_ids = envelope.get("user_ids") or []
        message = envelope.get("message")
        if not user_ids or not isinstance(message, dict):
            return
        for raw_id in user_ids:
            try:
                user_id = uuid.UUID(str(raw_id))
            except (ValueError, TypeError):
                continue
            await connection_manager.send_to_user_async(user_id, message)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.debug("ws fanout: skipping malformed envelope: %s", exc)
    except Exception as exc:  # noqa: BLE001 — fanout must never crash the loop
        logger.warning("ws fanout: delivery error: %s", exc)


async def _subscriber_loop() -> None:
    """Consume ``notify:*`` messages and forward them to local sockets.

    Pattern-subscribes once — every API replica receives every notification
    and forwards only to users whose sockets it holds (send_to_user_async
    is a no-op for unknown users).
    """
    while True:
        pubsub = None
        try:
            store = get_state_store()
            pubsub = store.subscribe_pattern(SUBSCRIBE_PATTERN)
            if pubsub is None:
                # No Redis: nothing to fan out cross-process. Idle quietly.
                await asyncio.sleep(30)
                continue

            logger.info("ws fanout: subscribed to %s", SUBSCRIBE_PATTERN)
            async for raw in _aiter_pubsub(pubsub):
                await _handle_message(raw)
        except asyncio.CancelledError:
            break
        except Exception as exc:  # noqa: BLE001 — reconnect on any failure
            logger.warning("ws fanout: subscriber error (%s); retrying in 5s", exc)
            await asyncio.sleep(5)
        finally:
            if pubsub is not None:
                try:
                    pubsub.close()
                except Exception:  # noqa: BLE001
                    pass


async def _aiter_pubsub(pubsub):
    """Async iterator over sync redis pub/sub messages via a thread.

    ``redis-py``'s sync client blocks; bridging with ``asyncio.to_thread``
    keeps the event loop free. ``get_message`` returns None when idle, so
    we poll with a small sleep instead of busy-looping.
    """
    while True:
        message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
        if message is not None:
            data = message.get("data")
            if isinstance(data, (str, bytes)):
                yield data
        else:
            await asyncio.sleep(0.05)


def start_fanout() -> None:
    """Start the subscriber task (call from the FastAPI lifespan)."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_subscriber_loop())
        logger.info("ws fanout: background subscriber started")


async def stop_fanout() -> None:
    """Cancel the subscriber task (call on shutdown)."""
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
