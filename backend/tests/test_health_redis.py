"""Tests for health/readiness reporting of distributed state.

The state store degrades to an in-process implementation when Redis is
unreachable. That keeps requests working, but it means rate limits and locks
are enforced separately in each replica — an outage that must be visible
rather than reported as healthy.
"""

from app.api.v1.health import _redis_status
from app.core.distributed_state import StateStore


class _FakeStore:
    def __init__(self, backend: str, ping_result: bool):
        self.backend = backend
        self._ping_result = ping_result

    def ping(self) -> bool:
        return self._ping_result


def test_memory_store_reports_unreachable():
    store = StateStore(None)
    assert store.backend == "memory"
    assert store.ping() is False


def test_ping_never_raises_when_redis_breaks():
    """Health checks must not raise, however the client misbehaves."""

    class _Exploding:
        def ping(self):
            raise RuntimeError("connection reset")

    store = StateStore.__new__(StateStore)
    store.backend = "redis"
    store._redis = _Exploding()
    assert store.ping() is False


def test_redis_configured_and_reachable_is_healthy(monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.health.get_state_store", lambda: _FakeStore("redis", True)
    )
    assert _redis_status()["status"] == "healthy"


def test_redis_configured_but_down_is_unhealthy(monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.health.get_state_store", lambda: _FakeStore("redis", False)
    )
    assert _redis_status()["status"] == "unhealthy"


def test_unconfigured_redis_is_degraded_not_unhealthy(monkeypatch):
    """
    A single-instance deployment legitimately has no Redis.

    Readiness must not block it, but the response still has to say that
    limits are not shared.
    """
    monkeypatch.setattr(
        "app.api.v1.health.get_state_store", lambda: _FakeStore("memory", False)
    )
    status = _redis_status()
    assert status["status"] == "degraded"
    assert "not shared" in status["message"]


async def test_readiness_fails_when_redis_configured_but_down(monkeypatch):
    from app.api.v1.health import readiness_check

    monkeypatch.setattr(
        "app.api.v1.health.get_state_store", lambda: _FakeStore("redis", False)
    )

    class _Db:
        async def execute(self, *a, **kw):
            class _R:
                def scalar(self):
                    return 1
            return _R()

    result = await readiness_check(db=_Db())
    assert isinstance(result, tuple)
    body, status_code = result
    assert status_code == 503
    assert body["status"] == "not_ready"


async def test_readiness_succeeds_when_redis_absent(monkeypatch):
    from app.api.v1.health import readiness_check

    monkeypatch.setattr(
        "app.api.v1.health.get_state_store", lambda: _FakeStore("memory", False)
    )

    class _Db:
        async def execute(self, *a, **kw):
            class _R:
                def scalar(self):
                    return 1
            return _R()

    result = await readiness_check(db=_Db())
    assert isinstance(result, dict)
    assert result["status"] == "ready"
    assert result["redis"] == "degraded"