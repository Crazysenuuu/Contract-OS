"""Reliability primitive tests (spec 1.23.17, 1.23.22-1.23.26)."""

import pytest

from app.core.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    backoff_seconds,
    correlation_scope,
    ensure_correlation_id,
    get_correlation_id,
    park_in_dlq,
    requeue_dead_events,
    should_dead_letter,
)


class TestCorrelationIds:
    def test_mints_id_when_missing(self):
        cid = ensure_correlation_id(None)
        assert cid
        assert get_correlation_id() == cid

    def test_adopts_inbound_id(self):
        ensure_correlation_id("my-trace-123")
        assert get_correlation_id() == "my-trace-123"

    def test_rejects_unsane_ids(self):
        cid = ensure_correlation_id("has spaces\tand\nnewlines")
        assert " " not in cid and "\n" not in cid
        cid = ensure_correlation_id("x" * 500)
        assert len(cid) <= 128

    def test_scope_restores_previous(self):
        ensure_correlation_id("outer")
        with correlation_scope("inner"):
            assert get_correlation_id() == "inner"
        assert get_correlation_id() == "outer"


class TestCircuitBreaker:
    def test_opens_after_threshold(self):
        breaker = CircuitBreaker("email", failure_threshold=3, recovery_seconds=0.1)

        def boom():
            raise RuntimeError("smtp down")

        for _ in range(3):
            with pytest.raises(RuntimeError):
                breaker.call(boom)

        with pytest.raises(CircuitOpenError):
            breaker.call(lambda: "ok")  # fail-fast without calling fn

    def test_half_open_recovers(self, monkeypatch):
        import time as time_mod

        real_monotonic = time_mod.monotonic
        offset = [0.0]

        def fake_monotonic():
            return real_monotonic() + offset[0]

        breaker = CircuitBreaker("ai", failure_threshold=2, recovery_seconds=0.05)

        def boom():
            raise ValueError("x")

        for _ in range(2):
            with pytest.raises(ValueError):
                breaker.call(boom)
        assert breaker.state == "open"

        monkeypatch.setattr(time_mod, "monotonic", fake_monotonic)
        offset[0] = 1.0  # past the recovery window
        assert breaker.state == "half_open"
        assert breaker.call(lambda: "fine") == "fine"
        assert breaker.state == "closed"

    def test_success_resets_failures(self):
        breaker = CircuitBreaker("esign", failure_threshold=3)
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] % 2 == 1:
                raise RuntimeError("once")
            return "ok"

        with pytest.raises(RuntimeError):
            breaker.call(flaky)
        assert breaker.call(flaky) == "ok"
        with pytest.raises(RuntimeError):
            breaker.call(flaky)
        assert breaker.call(flaky) == "ok"
        # Still closed: failures never accumulated across successes.
        assert breaker.state == "closed"


class TestDLQ:
    def test_parks_exhausted_events(self):
        class FakeEvent:
            attempts = 10
            status = "pending"
            next_attempt_at = None
            last_error = None

        event = FakeEvent()
        assert park_in_dlq(event, error="smtp unreachable") is True
        assert event.status == "dead"
        assert event.next_attempt_at is None
        assert "smtp" in event.last_error

    def test_schedules_retry_when_attempts_remain(self):
        class FakeEvent:
            attempts = 1
            status = "pending"
            next_attempt_at = None
            last_error = None

        event = FakeEvent()
        assert park_in_dlq(event, error="flaky") is False
        assert event.status == "pending"
        assert event.next_attempt_at is not None

    def test_backoff_is_exponential_with_cap(self):
        assert backoff_seconds(0) == 1
        assert backoff_seconds(3) == 8
        assert backoff_seconds(20) == 900

    def test_should_dead_letter(self):
        assert should_dead_letter(5, max_attempts=5)
        assert not should_dead_letter(4, max_attempts=5)

    def test_requeue_resets_dead_events(self):
        class FakeEvent:
            def __init__(self):
                self.status = "dead"
                self.attempts = 10
                self.next_attempt_at = None

        events = [FakeEvent(), FakeEvent(), FakeEvent()]
        assert requeue_dead_events(events) == 3
        assert all(e.status == "pending" and e.attempts == 0 for e in events)
