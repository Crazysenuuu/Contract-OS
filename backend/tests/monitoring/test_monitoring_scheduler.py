"""Scheduler + Celery task coverage for spec 3.15: the periodic sweep
(run_sweep / _run_sweep) and stale-version detection (detect_stale), driven
through the real Celery task shims in app.monitoring.tasks."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agreement import AgreementVersion
from app.monitoring.models import MonitoringEvaluation, ObligationMonitoring


@pytest.fixture
def sweep_sessionmaker(engine, monkeypatch):
    """Point app.monitoring.scheduler.AsyncSessionLocal at the test engine."""
    import app.monitoring.scheduler as scheduler_mod

    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr(scheduler_mod, "AsyncSessionLocal", factory)
    return factory


@pytest.fixture
def stub_sweep_http(monkeypatch):
    def install(items):
        def handler(method, url, params, headers):
            return httpx.Response(
                200,
                json={"items": items},
                request=httpx.Request("GET", "http://test"),
            )

        from app.monitoring.connectors.implementations import http as http_mod

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                self.handler = handler

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def request(self, method, url, params=None, headers=None):
                return self.handler(method, url, params or {}, headers or {})

        monkeypatch.setattr(http_mod.httpx, "AsyncClient", _FakeAsyncClient)

    return install


async def test_run_monitoring_sweep_task_runs_due(
    sweep_sessionmaker,
    stub_sweep_http,
    active_monitoring,
    db_session,
):
    rule = await db_session.get(ObligationMonitoring, active_monitoring.id)
    rule.next_run_at = datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc)
    await db_session.commit()

    stub_sweep_http([{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}])

    from app.monitoring.tasks import run_monitoring_sweep

    result = run_monitoring_sweep(limit=50)
    assert result["evaluations_run"] == 1

    evaluations = await db_session.execute(
        select(MonitoringEvaluation).where(
            MonitoringEvaluation.monitoring_id == active_monitoring.id
        )
    )
    assert evaluations.scalars().all()


async def test_run_sweep_logs_and_skips_failing_tenant(
    sweep_sessionmaker, monkeypatch, caplog, db_session
):
    """A per-tenant failure is logged and skipped, not raised.

    Fan-out sweeps must not abort mid-way: Celery would retry the whole job
    and re-do the tenants that already succeeded.
    """
    import app.monitoring.scheduler as scheduler_mod

    await _ensure_org(db_session)

    def _boom(db, *, limit=50):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(scheduler_mod, "schedule_due_monitorings", _boom)
    with caplog.at_level("ERROR", logger="app.services.tenant_context"):
        result = scheduler_mod.run_sweep(limit=50)

    # Every counter is absent because the tenant failed, rather than a
    # fabricated zero that would read as "swept, found nothing".
    assert result == {}
    assert "sweep exploded" in caplog.text


async def test_run_per_tenant_raise_on_error_is_fail_fast(
    sweep_sessionmaker, monkeypatch, db_session
):
    """Management commands can opt into stopping at the first failure."""
    from app.services.tenant_context import run_per_tenant

    await _ensure_org(db_session)

    async def _boom(db):
        raise RuntimeError("tenant service exploded")

    with pytest.raises(RuntimeError, match="tenant service exploded"):
        await run_per_tenant(
            _boom,
            session_factory=sweep_sessionmaker,
            raise_on_error=True,
        )


async def _ensure_org(db_session) -> None:
    """Guarantee the fan-out loop has at least one tenant to visit."""
    from app.models.organization import Organization

    db_session.add(
        Organization(
            name="Sweep Org",
            slug="sweep-org",
            country="US",
            timezone="UTC",
        )
    )
    await db_session.commit()


async def test_detect_stale_task_pauses_superseded_version(
    sweep_sessionmaker,
    db_session,
    monitoring_obligation,
    agreement_version,
    monitoring_integration,
    test_user,
):
    newest = AgreementVersion(
        agreement_id=monitoring_obligation.agreement_id,
        version_number=2,
        content="Newest",
        content_hash="test-hash-v2",
        status="ACTIVE",
        created_by=test_user.id,
        data={},
    )
    db_session.add(newest)
    await db_session.commit()

    stale_rule = ObligationMonitoring(
        organization_id=monitoring_obligation.organization_id,
        obligation_id=monitoring_obligation.id,
        source_version_id=agreement_version.id,
        integration_id=monitoring_integration.id,
        status="ACTIVE",
        query_definition={"resource": "claims"},
        evaluation_definition={"kind": "existence", "expected": True},
        schedule_definition={"recurrence": "interval", "minutes": 60},
        automation={},
        created_by=test_user.id,
    )
    db_session.add(stale_rule)
    await db_session.commit()

    from app.monitoring.tasks import detect_stale_monitorings_task

    result = detect_stale_monitorings_task()
    assert result == {"paused": 1}

    await db_session.refresh(stale_rule)
    assert stale_rule.status == "PAUSED"
    assert stale_rule.pause_reason == "STALE_VERSION"