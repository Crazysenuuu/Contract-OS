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


async def test_run_sweep_rolls_back_on_error(sweep_sessionmaker, monkeypatch):
    import app.monitoring.scheduler as scheduler_mod

    def _boom(db, *, limit=50):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(scheduler_mod, "schedule_due_monitorings", _boom)
    with pytest.raises(RuntimeError):
        scheduler_mod.run_sweep(limit=50)


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