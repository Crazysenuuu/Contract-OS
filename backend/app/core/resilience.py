"""Reliability primitives (spec 1.23.17, 1.23.22-1.23.26).

- CorrelationID: request-scoped correlation id (from X-Request-ID or new)
  propagated into structured logs and downstream calls (1.23.17).
- CircuitBreaker: fail-fast wrapper around flaky dependencies (email, AI
  providers, e-signature) with half-open recovery (1.23.26).
- DLQ helpers (1.23.22): events that exceed max attempts are parked in a
  dead-letter status instead of being retried forever; they remain
  inspectable and re-drivable.
"""

from __future__ import annotations

import contextvars
import secrets
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

# --- Correlation IDs (1.23.17) -------------------------------------------

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)

CORRELATION_HEADER = "X-Request-ID"


def new_correlation_id() -> str:
    return secrets.token_hex(8)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


@contextmanager
def correlation_scope(correlation_id: str | None) -> Generator[str | None, None, None]:
    """Bind the correlation id for the current context (request or task)."""
    token = _correlation_id.set(correlation_id)
    try:
        yield _correlation_id.get()
    finally:
        _correlation_id.reset(token)


def ensure_correlation_id(request_header_value: str | None) -> str:
    """Adopt the caller's id when sane, otherwise mint one; bind it to the
    current context and return it."""
    cid = (request_header_value or "").strip()
    if not cid or len(cid) > 128 or any(c.isspace() for c in cid):
        cid = new_correlation_id()
    _correlation_id.set(cid)
    return cid


class CorrelationLogFilter:
    """Logging filter injecting correlation_id into every record."""

    def filter(self, record) -> bool:  # noqa: ANN001
        record.correlation_id = get_correlation_id() or "-"
        return True


# --- Circuit breaker (1.23.26) --------------------------------------------


class CircuitOpenError(RuntimeError):
    """Raised when a call is attempted while the circuit is open."""


class CircuitBreaker:
    """Thread-safe closed/open/half-open circuit breaker."""

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        # RLock: call() holds the lock while consulting the state property,
        # which acquires it again (non-reentrant Lock would deadlock).
        self._lock = threading.RLock()
        self._failures = 0
        self._state: str = "closed"
        self._opened_at: float | None = None
        self._last_error: str | None = None

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == "open" and self._opened_at is not None:
                if time.monotonic() - self._opened_at >= self.recovery_seconds:
                    self._state = "half_open"
            return self._state

    def call(self, fn: Callable[[], Any]) -> Any:
        with self._lock:
            state = self.state
            if state == "open":
                raise CircuitOpenError(
                    f"circuit '{self.name}' open (last error: {self._last_error})"
                )
        try:
            result = fn()
        except Exception as exc:
            with self._lock:
                self._failures += 1
                self._last_error = f"{type(exc).__name__}: {exc}"[:200]
                if self._failures >= self.failure_threshold:
                    self._state = "open"
                    self._opened_at = time.monotonic()
            raise
        with self._lock:
            self._failures = 0
            self._state = "closed"
            self._opened_at = None
        return result


_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def get_breaker(name: str, **kwargs) -> CircuitBreaker:
    """Named process-wide breaker registry (email, ai, esignature, ...)."""
    with _breakers_lock:
        if name not in _breakers:
            _breakers[name] = CircuitBreaker(name, **kwargs)
        return _breakers[name]


# --- Dead-letter handling (1.23.22) ----------------------------------------

# Outbox statuses used by the DLQ. `dead` events stay queryable and can be
# re-driven by an operator via requeue_dead_events.
DLQ_STATUS = "dead"
MAX_ATTEMPTS_DEFAULT = 10


def should_dead_letter(attempts: int, max_attempts: int = MAX_ATTEMPTS_DEFAULT) -> bool:
    return attempts >= max_attempts


def backoff_seconds(attempts: int) -> float:
    """Exponential backoff with a 15-minute cap."""
    return min(2 ** max(0, attempts), 900)


def next_attempt_time(attempts: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds(attempts))


def park_in_dlq(event, *, error: str, max_attempts: int = MAX_ATTEMPTS_DEFAULT) -> bool:
    """Move an exhausted outbox event to the DLQ status. Returns True when
    parked, False when the event still has attempts left (caller should
    schedule a retry instead). Mutates the passed ORM instance."""
    if not should_dead_letter(event.attempts or 0, max_attempts):
        event.status = "pending"
        event.next_attempt_at = next_attempt_time(event.attempts or 0)
        event.last_error = error[:500]
        return False
    event.status = DLQ_STATUS
    event.next_attempt_at = None
    event.last_error = error[:500]
    return True


def requeue_dead_events(events: list) -> int:
    """Operator action: re-drive all (or selected) dead events."""
    requeued = 0
    now = datetime.now(timezone.utc)
    for event in events:
        if event.status == DLQ_STATUS:
            event.status = "pending"
            event.attempts = 0
            event.next_attempt_at = now
            requeued += 1
    return requeued
