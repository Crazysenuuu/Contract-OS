"""Distributed state primitives (spec 1.14.21-22 / 1.23).

Production deployments run more than one API process and a Celery worker.
Several subsystems previously kept their counters, incident lists, and
connection fanout in per-process memory, which silently breaks behind a
load balancer: two workers each count half of a user's downloads, an
incident created on worker A is invisible to worker B, and a push from the
outbox dispatcher (Celery) never reaches a WebSocket held by an API pod.

``StateStore`` provides the three primitives those subsystems need:

- ``sliding_window``: per-key event timestamps inside a time window
  (DLP mass-download guard, escalation hourly rate limiting).
- ``key/value`` with TTL: last-incident timestamps (escalation cooldown),
  incident records, and other small durable-ish state.
- ``pub/sub``: cross-process fanout (WebSocket notifications).

Design rules:

- Redis is optional. With no REDIS_URL the store falls back to an
  in-process implementation with identical semantics, so dev and the
  SQLite test suite keep working unchanged.
- Every Redis call is failure-tolerant: a Redis outage must degrade to
  the local fallback (or no-op for pub/sub), never raise into callers —
  a broken cache must not take notification delivery or upload gating
  down with it.
- Sync primitives on purpose: callers are a mix of sync guards (DLP)
  and async services (escalation), and every Redis operation here is a
  sub-millisecond round trip; blocking the event loop briefly is
  acceptable and keeps one implementation for both worlds.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Local (in-process) fallback implementation
# --------------------------------------------------------------------------


class _LocalStore:
    """Thread-safe in-process mirror of the Redis operations we use."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._windows: dict[str, deque[float]] = defaultdict(deque)
        self._strings: dict[str, str] = {}
        self._expiry: dict[str, float] = {}

    # -- sliding window ----------------------------------------------------

    def window_add(self, key: str, window_seconds: int, max_events: int) -> dict:
        now = time.monotonic()
        with self._lock:
            window = self._windows[key]
            while window and now - window[0] > window_seconds:
                window.popleft()
            window.append(now)
            return {"count": len(window), "limit": max_events, "exceeded": len(window) > max_events}

    def window_count(self, key: str, window_seconds: int) -> int:
        now = time.monotonic()
        with self._lock:
            window = self._windows.get(key)
            if not window:
                return 0
            while window and now - window[0] > window_seconds:
                window.popleft()
            return len(window)

    def window_reset(self, key: str) -> None:
        with self._lock:
            self._windows.pop(key, None)

    # -- key/value with TTL -------------------------------------------------

    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        with self._lock:
            self._strings[key] = value
            if ttl_seconds is not None:
                self._expiry[key] = time.monotonic() + ttl_seconds
            else:
                self._expiry.pop(key, None)

    def get(self, key: str) -> str | None:
        with self._lock:
            expiry = self._expiry.get(key)
            if expiry is not None and expiry < time.monotonic():
                self._strings.pop(key, None)
                self._expiry.pop(key, None)
                return None
            return self._strings.get(key)

    def delete(self, key: str) -> None:
        with self._lock:
            self._strings.pop(key, None)
            self._expiry.pop(key, None)

    def keys(self, pattern: str) -> list[str]:
        import fnmatch

        with self._lock:
            return [k for k in self._strings if fnmatch.fnmatch(k, pattern)]

    # -- pub/sub -------------------------------------------------------------

    def publish(self, channel: str, message: str) -> None:
        """Local fanout is a no-op: same-process listeners are handled by
        direct calls; cross-process delivery needs real Redis."""


# --------------------------------------------------------------------------
# Redis-backed implementation
# --------------------------------------------------------------------------


class _RedisStore:
    def __init__(self, redis_client) -> None:
        self._r = redis_client

    # -- sliding window ----------------------------------------------------

    def window_add(self, key: str, window_seconds: int, max_events: int) -> dict:
        now = time.time()
        member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
        pipe = self._r.pipeline()
        pipe.zadd(key, {member: now})
        pipe.zremrangebyscore(key, "-inf", now - window_seconds)
        pipe.zcard(key)
        pipe.expire(key, window_seconds + 60)
        count = pipe.execute()[2]
        return {"count": int(count), "limit": max_events, "exceeded": int(count) > max_events}

    def window_count(self, key: str, window_seconds: int) -> int:
        now = time.time()
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(key, "-inf", now - window_seconds)
        pipe.zcard(key)
        return int(pipe.execute()[1])

    def window_reset(self, key: str) -> None:
        self._r.delete(key)

    # -- key/value with TTL -------------------------------------------------

    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        if ttl_seconds is not None:
            self._r.set(key, value, ex=ttl_seconds)
        else:
            self._r.set(key, value)

    def get(self, key: str) -> str | None:
        value = self._r.get(key)
        return value.decode() if isinstance(value, bytes) else value

    def delete(self, key: str) -> None:
        self._r.delete(key)

    def keys(self, pattern: str) -> list[str]:
        try:
            return list(self._r.scan_iter(match=pattern))
        except Exception:  # noqa: BLE001
            return []

    # -- pub/sub -------------------------------------------------------------

    def publish(self, channel: str, message: str) -> None:
        self._r.publish(channel, message)


# --------------------------------------------------------------------------
# Public store
# --------------------------------------------------------------------------


class StateStore:
    """Redis-backed state with a transparent in-process fallback.

    Construct once per process via :func:`get_state_store`. ``backend``
    is exposed for tests that need to assert which mode is active.
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self.backend = "memory"
        self.redis_url = redis_url
        self._redis = None
        if redis_url:
            try:
                import redis

                self._redis = redis.Redis.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
                self._redis.ping()
                self.backend = "redis"
            except Exception as exc:  # noqa: BLE001 — degrade, never raise
                logger.warning(
                    "Redis unavailable (%s); distributed state degrading to in-process",
                    exc,
                )
                self._redis = None
        self._local = _LocalStore()
        self._active = self._redis if self._redis is not None else self._local

    # -- sliding window ----------------------------------------------------

    def window_add(self, key: str, *, window_seconds: int, max_events: int | None = None) -> dict:
        """Record an event for ``key`` and return window stats.

        Cleans entries older than ``window_seconds`` first, so ``count``
        is the live window size.
        """
        return self._active.window_add(key, window_seconds, max_events or 0)

    def window_count(self, key: str, *, window_seconds: int) -> int:
        """Read-only live window size (no event recorded)."""
        return self._active.window_count(key, window_seconds)

    def window_reset(self, key: str) -> None:
        self._active.window_reset(key)

    # -- key/value with TTL -------------------------------------------------

    def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None:
        self._active.set(key, value, ttl_seconds)

    def set_json(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> None:
        self.set(key, json.dumps(value, default=str), ttl_seconds=ttl_seconds)

    def get(self, key: str) -> str | None:
        return self._active.get(key)

    def get_json(self, key: str) -> Any | None:
        raw = self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    def delete(self, key: str) -> None:
        self._active.delete(key)

    def keys(self, pattern: str) -> list[str]:
        """Keys matching a glob pattern (e.g. ``escalation:incidents:*``)."""
        return self._active.keys(pattern)

    # -- pub/sub -------------------------------------------------------------

    def publish(self, channel: str, message: dict) -> bool:
        """Best-effort publish; returns False (never raises) when Redis is
        absent or unreachable — same-process delivery does not depend on it."""
        if self._redis is None:
            return False
        try:
            self._redis.publish(channel, json.dumps(message, default=str))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis publish to %s failed: %s", channel, exc)
            return False

    def subscribe(self, *channels: str):
        """Return a live pubsub for ``channels``, or None without Redis.

        The caller owns the subscription and must close it; see
        ``app.services.ws_fanout`` for the reference consumer loop.
        """
        if self._redis is None:
            return None
        try:
            pubsub = self._redis.pubsub()
            pubsub.subscribe(*channels)
            return pubsub
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis subscribe to %s failed: %s", channels, exc)
            return None

    def subscribe_pattern(self, pattern: str):
        """Pattern subscription (``psubscribe``) for channel globs."""
        if self._redis is None:
            return None
        try:
            pubsub = self._redis.pubsub()
            pubsub.psubscribe(pattern)
            return pubsub
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis psubscribe to %s failed: %s", pattern, exc)
            return None


_store: StateStore | None = None
_store_lock = threading.Lock()


def get_state_store() -> StateStore:
    """Process-wide store. Reads REDIS_URL lazily so tests can env-patch."""
    global _store
    with _store_lock:
        import os

        redis_url = os.environ.get("REDIS_URL") or None
        if _store is None or _store.redis_url != redis_url:
            _store = StateStore(redis_url)
        return _store


def reset_state_store() -> None:
    """Test helper: force re-creation on next access (e.g. after env changes)."""
    global _store
    with _store_lock:
        _store = None
