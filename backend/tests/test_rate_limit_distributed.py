"""Tests for distributed (cross-replica) rate limiting.

The point of routing the limiter through the shared state store is that a
budget is spent once across every replica. With N processes behind a load
balancer an in-process limiter grants N times the intended budget, which is
enough to walk past the auth threshold this exists to hold.
"""

from unittest.mock import patch

import pytest

from app.core.rate_limit import _Window, reset_rate_limits


class _FakeRedisStore:
    """Shared window semantics: one counter visible to every caller."""

    def __init__(self, result_factory):
        self.backend = "redis"
        self.calls: list[tuple[str, int, int]] = []
        self._result_factory = result_factory

    def window_add(self, key, *, window_seconds, max_events):
        self.calls.append((key, window_seconds, max_events))
        return self._result_factory(len(self.calls))


def _patched_store(store):
    return patch("app.core.distributed_state.get_state_store", return_value=store)


@pytest.fixture(autouse=True)
def _clean():
    reset_rate_limits()
    yield
    reset_rate_limits()


async def test_shared_window_enforces_limit_across_callers():
    """
    Two independent windows (as two replicas would be) must share one
    counter — the second caller is rejected because the first spent the
    budget, not because it spent it locally.
    """
    store = _FakeRedisStore(lambda n: {"count": n, "limit": 5, "exceeded": n > 5})
    window_a, window_b = _Window(), _Window()

    with _patched_store(store):
        for _ in range(5):
            allowed, _ = await window_a.hit("t:abc:auth", 60, 5)
            assert allowed is True

        allowed, retry_after = await window_b.hit("t:abc:auth", 60, 5)

    assert allowed is False
    assert retry_after == 60


async def test_shared_keys_are_namespaced():
    store = _FakeRedisStore(lambda n: {"count": n, "limit": 9, "exceeded": False})
    with _patched_store(store):
        await _Window().hit("t:abc:default", 60, 240)

    key, window, limit = store.calls[0]
    # Namespacing keeps these from colliding with the other consumers of the
    # shared store (DLP counters, escalation state).
    assert key == "ratelimit:t:abc:default"
    assert (window, limit) == (60, 240)


async def test_in_memory_backend_uses_local_windows(monkeypatch):
    """No Redis means single-instance semantics: local windows, no Redis calls."""

    class _Memory:
        backend = "memory"

    with _patched_store(_Memory()):
        window = _Window()
        for _ in range(3):
            allowed, _ = await window.hit("ip:1.2.3.4:auth", 60, 3)
            assert allowed is True
        allowed, _ = await window.hit("ip:1.2.3.4:auth", 60, 3)

    assert allowed is False


async def test_store_failure_degrades_to_local_window(caplog):
    """
    A Redis blip must cost the cross-replica protection, not availability.

    The limiter falls back to process-local windows instead of returning 500
    for every request in the deployment.
    """

    class _Broken:
        backend = "redis"

        def window_add(self, *a, **kw):
            raise ConnectionError("connection reset by peer")

    with _patched_store(_Broken()):
        window = _Window()
        allowed, _ = await window.hit("t:x:default", 60, 10)

    assert allowed is True
    assert "Rate-limit store unavailable" in caplog.text


async def test_retry_after_is_never_zero_when_rejected():
    store = _FakeRedisStore(lambda n: {"count": n, "limit": 1, "exceeded": n > 1})
    with _patched_store(store):
        window = _Window()
        assert (await window.hit("k", 60, 1))[0] is True
        allowed, retry_after = await window.hit("k", 60, 1)

    assert allowed is False
    # A 429 without a usable Retry-After invites clients to retry instantly.
    assert retry_after >= 1