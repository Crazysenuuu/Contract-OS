"""Integration tests for the monitoring pipeline (spec 3.15): end-to-end run,
idempotency, failure doctrine, sweep, staleness, webhook ingestion and the
management API with RBAC + cross-org isolation.

The REST connector is driven through a stubbed ``httpx.AsyncClient`` so no
external network is touched, while still exercising the real fetch/mapping
code (3.15.11 — no fake sources in production code, only in tests).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.models.event_outbox import OutboxEvent


# --------------------------------------------------------------------------
# Transport stub
# --------------------------------------------------------------------------

@pytest.fixture
def stub_http(monkeypatch):
    """Replace httpx.AsyncClient in the http connector with a scriptable client."""

    def install(handler):
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


def _json_response(status, **body):
    if status in (204,):
        return httpx.Response(status, request=httpx.Request("GET", "http://test"))
    return httpx.Response(status, json=body, request=httpx.Request("GET", "http://test"))


def _ok_handler(items):
    def handler(method, url, params, headers):
        return _json_response(200, items=items)
    return handler


async def _count_events(db_session, event_type: str) -> int:
    rows = (
        (
            await db_session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == event_type)
            )
        )
        .scalars()
        .all()
    )
    return len(rows)


# --------------------------------------------------------------------------
# End-to-end run pipeline
# --------------------------------------------------------------------------

async def test_pipeline_pass_records_evaluation_run_health(
    db_session, monitoring_integration, active_monitoring, stub_http
):
    from app.monitoring.enums import EvaluationResult
    from app.monitoring.models import (
        ExternalObservationRecord,
        IntegrationHealth,
        MonitoringEvaluation,
        MonitoringRun,
        ObligationMonitoring,
    )
    from app.monitoring.service import run_obligation_monitoring

    stub_http(
        _ok_handler(
            [{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}]
        )
    )
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    evaluation = await run_obligation_monitoring(
        db_session, monitoring=active_monitoring, run_at=now, now=now
    )
    await db_session.flush()

    assert evaluation.result == EvaluationResult.PASS.value

    run = (
        await db_session.execute(
            select(MonitoringRun).where(MonitoringRun.monitoring_id == active_monitoring.id)
        )
    ).scalar_one()
    assert run.status == "COMPLETED"
    assert run.idempotency_key == f"{active_monitoring.id}:2026-09-25T12:00"

    health = await db_session.get(IntegrationHealth, monitoring_integration.id)
    assert health is not None
    assert health.consecutive_failures == 0

    stored = (
        await db_session.execute(
            select(ExternalObservationRecord)
            .where(ExternalObservationRecord.monitoring_id == active_monitoring.id)
        )
    ).scalars().all()
    assert len(stored) == 1
    assert stored[0].payload["amount"] == 150

    rule = await db_session.get(ObligationMonitoring, active_monitoring.id)
    assert rule.last_result == "PASS"
    assert rule.next_run_at is not None
    assert rule.last_run_at is not None

    # A PASS with notify_on_pass unset must not fan out events.
    assert await _count_events(db_session, "monitoring.evaluation_passed") == 0
    assert await _count_events(db_session, "monitoring.evaluation_failed") == 0


async def test_pipeline_idempotent_for_same_period(
    db_session, active_monitoring, stub_http
):
    from app.monitoring.models import MonitoringEvaluation, MonitoringRun
    from app.monitoring.service import run_obligation_monitoring

    stub_http(
        _ok_handler(
            [{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}]
        )
    )
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    first = await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()
    second = await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    assert second.id == first.id
    assert (
        len(
            (await db_session.execute(
                select(MonitoringEvaluation).where(
                    MonitoringEvaluation.monitoring_id == active_monitoring.id
                )
            )).scalars().all()
        )
        == 1
    )
    assert (
        len(
            (await db_session.execute(
                select(MonitoringRun).where(MonitoringRun.monitoring_id == active_monitoring.id)
            )).scalars().all()
        )
        == 1
    )


async def test_pipeline_fail_opens_exception_and_notifies(
    db_session, active_monitoring, stub_http
):
    from app.monitoring.enums import EvaluationResult
    from app.monitoring.models import MonitoringEvaluation, MonitoringException
    from app.monitoring.service import run_obligation_monitoring

    # Existence rule expects at least one claim.
    stub_http(_ok_handler([]))
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    evaluation = await run_obligation_monitoring(
        db_session, monitoring=active_monitoring, run_at=now, now=now
    )
    await db_session.flush()

    assert evaluation.result == EvaluationResult.FAIL.value
    exception = (
        await db_session.execute(
            select(MonitoringException).where(
                MonitoringException.monitoring_id == active_monitoring.id
            )
        )
    ).scalars().one()
    assert exception.status == "OPEN"

    # Idempotent on the exception too: a second failed period keeps one OPEN row.
    second_run_at = now + timedelta(minutes=60)
    await run_obligation_monitoring(
        db_session, monitoring=active_monitoring, run_at=second_run_at, now=second_run_at
    )
    await db_session.flush()
    assert (
        len(
            (await db_session.execute(
                select(MonitoringException).where(
                    MonitoringException.monitoring_id == active_monitoring.id,
                    MonitoringException.status == "OPEN",
                )
            )).scalars().all()
        )
        == 1
    )
    assert await _count_events(db_session, "monitoring.evaluation_failed") == 2
    assert await _count_events(db_session, "monitoring.source_unavailable") == 0


# --------------------------------------------------------------------------
# Failure doctrine: source failures are INCONCLUSIVE (3.15.25, 3.15.62)
# --------------------------------------------------------------------------

async def test_pipeline_auth_failure_is_inconclusive_and_degrades(
    db_session, monitoring_integration, active_monitoring, stub_http
):
    from app.monitoring.enums import EvaluationResult, IntegrationStatus
    from app.monitoring.exceptions import ConnectorAuthError
    from app.monitoring.models import IntegrationHealth, MonitoringEvaluation, MonitoringException
    from app.monitoring.service import run_obligation_monitoring

    def reject(method, url, params, headers):
        return httpx.Response(
            401, request=httpx.Request("GET", url)
        )

    stub_http(reject)
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    evaluation = await run_obligation_monitoring(
        db_session, monitoring=active_monitoring, run_at=now, now=now
    )
    await db_session.flush()

    assert evaluation.result == EvaluationResult.INCONCLUSIVE.value
    assert evaluation.metrics["exception_type"] == ConnectorAuthError.__name__

    integration = await db_session.get(type(monitoring_integration), monitoring_integration.id)
    assert integration.status == IntegrationStatus.DEGRADED.value

    health = await db_session.get(IntegrationHealth, monitoring_integration.id)
    assert health.consecutive_failures == 1
    assert health.last_error_code == "ConnectorAuthError"

    # A source failure is NOT a rule failure: never open a MonitoringException
    # and never emit a FAIL alert.
    assert (
        len(
            (await db_session.execute(
                select(MonitoringException).where(
                    MonitoringException.monitoring_id == active_monitoring.id
                )
            )).scalars().all()
        )
        == 0
    )
    assert await _count_events(db_session, "monitoring.source_unavailable") == 1
    assert await _count_events(db_session, "monitoring.evaluation_failed") == 0


async def test_pipeline_transport_5xx_is_inconclusive(
    db_session, monitoring_integration, active_monitoring, stub_http
):
    from app.monitoring.enums import EvaluationResult
    from app.monitoring.exceptions import ConnectorUnavailable
    from app.monitoring.service import run_obligation_monitoring

    def failing(method, url, params, headers):
        return httpx.Response(503, request=httpx.Request("GET", url))

    stub_http(failing)
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    evaluation = await run_obligation_monitoring(
        db_session, monitoring=active_monitoring, run_at=now, now=now
    )
    await db_session.flush()
    assert evaluation.result == EvaluationResult.INCONCLUSIVE.value
    assert evaluation.metrics["exception_type"] == ConnectorUnavailable.__name__


async def test_missing_credential_fails_closed_inconclusive(
    db_session, active_monitoring, stub_http
):
    from app.monitoring.enums import EvaluationResult
    from app.monitoring.models import IntegrationCredential
    from app.monitoring.service import run_obligation_monitoring

    stub_http(_ok_handler([{"id": "c1"}]))
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)

    credential = (
        await db_session.execute(
            select(IntegrationCredential).where(
                IntegrationCredential.integration_id == active_monitoring.integration_id
            )
        )
    ).scalar_one()
    credential.status = "DISABLED"
    await db_session.flush()

    evaluation = await run_obligation_monitoring(
        db_session, monitoring=active_monitoring, run_at=now, now=now
    )
    await db_session.flush()
    assert evaluation.result == EvaluationResult.INCONCLUSIVE.value
    assert evaluation.metrics["exception_type"] == "CredentialUnavailable"


async def test_pipeline_recovers_after_degraded(
    db_session, monitoring_integration, active_monitoring, stub_http
):
    from app.monitoring.enums import IntegrationStatus
    from app.monitoring.models import IntegrationHealth
    from app.monitoring.service import run_obligation_monitoring

    integration = await db_session.get(type(monitoring_integration), monitoring_integration.id)
    integration.status = IntegrationStatus.DEGRADED.value
    db_session.add(
        IntegrationHealth(
            integration_id=integration.id,
            last_failure_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
            consecutive_failures=4,
            last_error_code="ConnectorUnavailable",
            last_error="boom",
        )
    )
    await db_session.flush()

    stub_http(
        _ok_handler(
            [{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}]
        )
    )
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    integration = await db_session.get(type(monitoring_integration), monitoring_integration.id)
    assert integration.status == IntegrationStatus.ACTIVE.value
    health = await db_session.get(IntegrationHealth, monitoring_integration.id)
    assert health.last_error_code is None
    assert await _count_events(db_session, "monitoring.recovered") == 1


async def test_pipeline_unregistered_connector_is_inconclusive(
    db_session, monitoring_obligation, agreement_version, test_org, test_user, stub_http
):
    from app.monitoring.enums import EvaluationResult
    from app.monitoring.models import IntegrationConnection, IntegrationCredential, ObligationMonitoring
    from app.monitoring.service import run_obligation_monitoring

    integration = IntegrationConnection(
        organization_id=test_org.id,
        name="Unknown source",
        integration_type="REST_API",
        provider_key="nonexistent_provider",
        status="ACTIVE",
        configuration={},
        created_by=test_user.id,
    )
    db_session.add(integration)
    await db_session.flush()
    db_session.add(
        IntegrationCredential(
            integration_id=integration.id,
            secret_reference="env://MONITORING_TEST_TOKEN",
            status="ACTIVE",
        )
    )
    await db_session.flush()

    rule = ObligationMonitoring(
        organization_id=test_org.id,
        obligation_id=monitoring_obligation.id,
        source_version_id=agreement_version.id,
        integration_id=integration.id,
        status="ACTIVE",
        query_definition={"resource": "claims"},
        evaluation_definition={"kind": "existence", "expected": True},
        schedule_definition={"recurrence": "interval", "minutes": 60},
        automation={},
        created_by=test_user.id,
    )
    db_session.add(rule)
    await db_session.flush()

    evaluation = await run_obligation_monitoring(
        db_session,
        monitoring=rule,
        run_at=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
        now=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
    )
    await db_session.flush()
    assert evaluation.result == EvaluationResult.INCONCLUSIVE.value


# --------------------------------------------------------------------------
# Sweep + staleness (3.15.31, 3.15.58)
# --------------------------------------------------------------------------

async def test_schedule_runs_due_monitorings(db_session, active_monitoring, stub_http):
    from app.monitoring.models import MonitoringEvaluation, ObligationMonitoring
    from app.monitoring.service import schedule_due_monitorings

    stub_http(
        _ok_handler(
            [{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}]
        )
    )
    rule = await db_session.get(ObligationMonitoring, active_monitoring.id)
    rule.next_run_at = datetime(2026, 9, 25, 11, 0, tzinfo=timezone.utc)

    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    evaluation_ids = await schedule_due_monitorings(db_session, now=now, limit=50)
    await db_session.flush()

    assert len(evaluation_ids) == 1
    evaluation = await db_session.get(MonitoringEvaluation, evaluation_ids[0])
    assert evaluation is not None
    rule = await db_session.get(ObligationMonitoring, active_monitoring.id)
    assert rule.next_run_at is not None and rule.next_run_at > now


async def test_stale_version_pauses_rule(db_session, test_agreement, active_monitoring, test_user):
    from app.monitoring.enums import MonitoringStatus, PauseReason
    from app.models.agreement import AgreementVersion
    from app.monitoring.models import ObligationMonitoring
    from app.monitoring.service import detect_stale_monitorings

    db_session.add(
        AgreementVersion(
            agreement_id=test_agreement.id,
            version_number=2,
            content="## Revised",
            content_hash="test-hash-v2",
            status="ACTIVE",
            created_by=test_user.id,
            data={},
        )
    )
    await db_session.flush()

    paused = await detect_stale_monitorings(db_session)
    await db_session.flush()

    assert paused == 1
    rule = await db_session.get(ObligationMonitoring, active_monitoring.id)
    assert rule.status == MonitoringStatus.PAUSED.value
    assert rule.pause_reason == PauseReason.STALE_VERSION.value
    assert await _count_events(db_session, "monitoring.stale_version") == 1


# --------------------------------------------------------------------------
# Webhook ingestion (3.15.28-3.15.30)
# --------------------------------------------------------------------------

def _webhook_request(integration_id, payload: dict, secret: str) -> tuple[dict, str]:
    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, signature


async def _make_webhook_integration(
    db_session, test_org, test_user, monitoring_obligation, agreement_version
):
    """Create a webhook integration + ACTIVE monitoring bound to it."""
    from app.monitoring.models import (
        IntegrationConnection,
        IntegrationCredential,
        ObligationMonitoring,
    )

    integration = IntegrationConnection(
        organization_id=test_org.id,
        name="Provider webhook",
        integration_type="WEBHOOK",
        provider_key="webhook",
        status="ACTIVE",
        configuration={"webhook_secret": "env://MONITORING_WEBHOOK_SECRET"},
        created_by=test_user.id,
        created_by_name=test_user.name,
    )
    db_session.add(integration)
    await db_session.flush()
    db_session.add(
        IntegrationCredential(
            integration_id=integration.id,
            secret_reference="env://MONITORING_TEST_TOKEN",
            status="ACTIVE",
        )
    )
    db_session.add(
        ObligationMonitoring(
            organization_id=test_org.id,
            obligation_id=monitoring_obligation.id,
            source_version_id=agreement_version.id,
            integration_id=integration.id,
            status="ACTIVE",
            query_definition={"resource": "shipments", "external_id_path": "id"},
            evaluation_definition={"kind": "existence", "expected": True},
            schedule_definition={"recurrence": "interval", "minutes": 60},
            automation={},
            created_by=test_user.id,
        )
    )
    await db_session.commit()
    return integration


async def test_webhook_accepts_and_creates_observation(
    client, db_session, test_org, test_user, monitoring_obligation, agreement_version
):
    from app.monitoring.models import (
        ExternalObservationRecord,
        MonitoringWebhookEvent,
    )
    from app.monitoring.observations import hash_payload

    integration = await _make_webhook_integration(
        db_session, test_org, test_user, monitoring_obligation, agreement_version
    )

    payload = {
        "id": "ship-900",
        "status": "delivered",
        "observed_at": "2026-09-25T11:58:00Z",
    }
    body, signature = _webhook_request(integration.id, payload, "test-webhook-secret-456")
    response = await client.post(
        f"/api/v1/integrations/{integration.id}/webhook",
        content=body,
        headers={"x-monitoring-signature": signature},
    )
    assert response.status_code == 200, response.text
    ack = response.json()
    assert ack["received"] is True
    assert ack["duplicate"] is False
    assert ack["observations"] == 1

    event = (
        await db_session.execute(
            select(MonitoringWebhookEvent).where(
                MonitoringWebhookEvent.integration_id == integration.id
            )
        )
    ).scalar_one()
    assert event.status == "ACCEPTED"
    assert event.payload_hash == hash_payload(payload)

    records = (
        await db_session.execute(
            select(ExternalObservationRecord).where(
                ExternalObservationRecord.integration_id == integration.id
            )
        )
    ).scalars().all()
    assert len(records) == 1
    assert records[0].external_id == "ship-900"


async def test_webhook_replays_same_payload_as_duplicate(
    client, test_org, test_user, monitoring_obligation, agreement_version, db_session
):
    integration = await _make_webhook_integration(
        db_session, test_org, test_user, monitoring_obligation, agreement_version
    )

    payload = {"id": "ship-777", "status": "delivered", "observed_at": "2026-09-25T11:58:00Z"}
    body, signature = _webhook_request(integration.id, payload, "test-webhook-secret-456")
    first = await client.post(
        f"/api/v1/integrations/{integration.id}/webhook",
        content=body,
        headers={"x-monitoring-signature": signature},
    )
    second = await client.post(
        f"/api/v1/integrations/{integration.id}/webhook",
        content=body,
        headers={"x-monitoring-signature": signature},
    )
    assert first.status_code == 200
    assert second.json()["duplicate"] is True


async def test_webhook_rejects_bad_signature(
    client, test_org, test_user, monitoring_obligation, agreement_version, db_session
):
    integration = await _make_webhook_integration(
        db_session, test_org, test_user, monitoring_obligation, agreement_version
    )

    payload = {"id": "x", "observed_at": "2026-09-25T11:58:00Z"}
    body, _ = _webhook_request(integration.id, payload, "test-webhook-secret-456")
    response = await client.post(
        f"/api/v1/integrations/{integration.id}/webhook",
        content=body,
        headers={"x-monitoring-signature": "0123456789abcdef"},
    )
    assert response.status_code == 401


async def test_webhook_rejects_stale_timestamp(
    client, test_org, test_user, monitoring_obligation, agreement_version, db_session
):
    integration = await _make_webhook_integration(
        db_session, test_org, test_user, monitoring_obligation, agreement_version
    )

    payload = {
        "id": "x",
        "observed_at": "2026-09-25T11:58:00Z",
        "timestamp": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    }
    body, signature = _webhook_request(integration.id, payload, "test-webhook-secret-456")
    response = await client.post(
        f"/api/v1/integrations/{integration.id}/webhook",
        content=body,
        headers={"x-monitoring-signature": signature},
    )
    assert response.status_code == 401


async def test_webhook_fails_closed_without_secret(
    client, monitoring_integration, monkeypatch
):
    import app.monitoring.router as router_mod

    class _NoSecretSettings:
        monitoring_webhook_secret = None
        monitoring_webhook_max_skew_seconds = 300

    monkeypatch.setattr(router_mod, "get_settings", lambda: _NoSecretSettings())
    # monitoring_integration has no webhook_secret configured and the
    # fallback settings secret is None → fail closed.
    body, signature = _webhook_request(
        monitoring_integration.id,
        {"id": "x", "observed_at": "2026-09-25T11:58:00Z"},
        "whatever",
    )
    response = await client.post(
        f"/api/v1/integrations/{monitoring_integration.id}/webhook",
        content=body,
        headers={"x-monitoring-signature": signature},
    )
    assert response.status_code == 503


# --------------------------------------------------------------------------
# Management API (authorization 3.15.46, fail-closed credentials 3.15.6)
# --------------------------------------------------------------------------

async def test_api_integration_crud_no_secret_leak(
    client, db_session, test_org, auth_headers
):
    create = await client.post(
        "/api/v1/monitoring/integrations",
        json={
            "name": "Vendor API",
            "integration_type": "REST_API",
            "provider_key": "rest_api",
            "configuration": {
                "base_url": "https://vendor.example.internal",
                "id_field": "id",
                "observed_at_field": "observed_at",
            },
        },
        headers=auth_headers,
    )
    assert create.status_code == 201, create.text
    integration = create.json()
    assert integration["status"] == "ACTIVE"
    assert "secret_reference" not in create.text

    # list filtered to workspace
    listing = await client.get("/api/v1/monitoring/integrations", headers=auth_headers)
    assert listing.status_code == 200
    assert any(i["id"] == integration["id"] for i in listing.json())

    # add a credential that resolves to a real env value — the value must
    # never come back out of the API.
    response = await client.post(
        f"/api/v1/monitoring/integrations/{integration['id']}/credentials",
        json={"secret_reference": "env://MONITORING_TEST_TOKEN"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert "test-bearer-token-123" not in response.text

    credentials = await client.get(
        f"/api/v1/monitoring/integrations/{integration['id']}/credentials",
        headers=auth_headers,
    )
    assert credentials.status_code == 200
    assert "test-bearer-token-123" not in credentials.text

    # providers metadata visible
    providers = await client.get(
        "/api/v1/monitoring/integrations/providers", headers=auth_headers
    )
    assert providers.status_code == 200
    assert any(p["provider_key"] == "rest_api" for p in providers.json()["providers"])


async def test_api_rule_crud_activate_run(
    client, auth_headers, active_monitoring, stub_http
):
    stub_http(
        _ok_handler(
            [{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}]
        )
    )

    created = await client.post(
        "/api/v1/monitoring/rules",
        json={
            "obligation_id": str(active_monitoring.obligation_id),
            "integration_id": str(active_monitoring.integration_id),
            "source_version_id": str(active_monitoring.source_version_id),
            "status": "ACTIVE",
            "query_definition": active_monitoring.query_definition,
            "evaluation_definition": active_monitoring.evaluation_definition,
            "schedule_definition": {"recurrence": "interval", "minutes": 30},
            "automation": {},
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["status"] == "ACTIVE"

    activated = await client.post(
        f"/api/v1/monitoring/rules/{rule['id']}/run",
        headers=auth_headers,
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["evaluation_id"]

    listing = await client.get("/api/v1/monitoring/rules", headers=auth_headers)
    assert listing.status_code == 200
    assert any(r["id"] == rule["id"] for r in listing.json())

    evaluations = await client.get(
        f"/api/v1/monitoring/rules/{rule['id']}/evaluations", headers=auth_headers
    )
    assert evaluations.status_code == 200
    assert len(evaluations.json()) >= 1

    dashboard = await client.get("/api/v1/monitoring/dashboard", headers=auth_headers)
    assert dashboard.status_code == 200
    assert "rules_by_status" in dashboard.json()


async def test_api_rbac_denies_no_permission(client, db_session, get_second_org_user):
    second = await get_second_org_user(role_name="analyst")

    response = await client.get(
        "/api/v1/monitoring/integrations",
        headers=second["auth_headers"],
    )
    assert response.status_code == 403

    response = await client.post(
        "/api/v1/monitoring/integrations",
        json={
            "name": "Sneaky",
            "integration_type": "REST_API",
            "provider_key": "rest_api",
            "configuration": {"base_url": "https://example.com"},
        },
        headers=second["auth_headers"],
    )
    assert response.status_code == 403


async def test_api_cross_org_isolation_404(client, active_monitoring, get_second_org_user):
    # The second user CAN read in their own org but must never see another
    # org's rule — the endpoint returns 404 (not 403) to avoid existence leaks.
    second = await get_second_org_user(
        role_name="analyst", permissions=["monitoring.view"]
    )
    response = await client.get(
        f"/api/v1/monitoring/rules/{active_monitoring.id}",
        headers=second["auth_headers"],
    )
    assert response.status_code == 404