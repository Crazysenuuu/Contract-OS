"""Gap coverage for spec 3.15: evidence records (3.15.37-38), conservative
automation on PASS (3.15.36), obligation-linkage and repeated-FAIL risk
escalation (3.15.50-51), notification recipient resolution (3.15.49), and
the spec audit events (3.15.47).

Mirrors the pipeline module's transport-stub pattern so the real REST
connector code is exercised without touching the network.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.event_outbox import OutboxEvent


# --------------------------------------------------------------------------
# Transport stub (per-module copy — shared stub lives in the pipeline module)
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
    return httpx.Response(status, json=body, request=httpx.Request("GET", "http://test"))


def _ok_handler(items):
    def handler(method, url, params, headers):
        return _json_response(200, items=items)
    return handler


async def _count_events(db_session, event_type: str) -> int:
    rows = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == event_type)
        )
    ).scalars().all()
    return len(rows)


async def _audit_actions(db_session, org_id) -> list[str]:
    rows = (
        await db_session.execute(
            select(AuditEvent).where(AuditEvent.tenant_id == org_id)
        )
    ).scalars().all()
    return [row.action for row in rows]


async def _event_payloads(db_session, event_type: str) -> list[dict]:
    rows = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == event_type)
        )
    ).scalars().all()
    return [row.payload or {} for row in rows]


# --------------------------------------------------------------------------
# Evidence chain (3.15.37-38)
# --------------------------------------------------------------------------

async def test_evidence_records_materialize_on_pass(
    db_session, monitoring_integration, active_monitoring, stub_http
):
    from app.monitoring.models import ExternalObservationRecord, MonitoringEvidence
    from app.monitoring.observations import hash_payload
    from app.monitoring.service import run_obligation_monitoring

    stub_http(
        _ok_handler([{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}])
    )
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(MonitoringEvidence).where(
                MonitoringEvidence.monitoring_id == active_monitoring.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    ev = rows[0]

    assert ev.evidence_type == "SYSTEM_RECORD"
    assert ev.source == "rest_api"
    assert ev.source_identifier == "c1"
    assert ev.integration_id == monitoring_integration.id
    assert ev.monitoring_run_id is not None
    assert ev.obligation_id == active_monitoring.obligation_id
    assert ev.observed_at.replace(tzinfo=timezone.utc) == datetime(
        2026, 9, 25, 12, 0, tzinfo=timezone.utc
    )
    assert ev.received_at is not None
    assert ev.payload_hash
    assert ev.attached_to_obligation is False
    assert ev.value["external_id"] == "c1"
    assert ev.value["observation_id"]

    obs = (
        await db_session.execute(
            select(ExternalObservationRecord).where(
                ExternalObservationRecord.monitoring_id == active_monitoring.id
            )
        )
    ).scalar_one()
    assert ev.payload_hash == obs.payload_hash
    assert ev.payload_hash == hash_payload(obs.payload)


async def test_webhook_ingest_materializes_evidence_without_run(
    db_session, active_monitoring, monitoring_integration
):
    from app.monitoring.models import MonitoringEvidence
    from app.monitoring.service import ingest_webhook_observations

    count = await ingest_webhook_observations(
        db_session,
        integration_id=monitoring_integration.id,
        payload={"id": "wh-1", "observed_at": "2026-09-25T12:00:00Z"},
        provider_event_id="evt-1",
    )
    await db_session.flush()

    assert count == 1
    rows = (
        await db_session.execute(
            select(MonitoringEvidence).where(
                MonitoringEvidence.monitoring_id == active_monitoring.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    ev = rows[0]
    assert ev.source_identifier == "wh-1"
    assert ev.monitoring_run_id is None
    assert ev.obligation_id == active_monitoring.obligation_id
    assert ev.value["external_id"] == "wh-1"


# --------------------------------------------------------------------------
# Evidence API (3.15.37-38)
# --------------------------------------------------------------------------

async def test_evidence_endpoint_returns_integrity_metadata(
    client, auth_headers, db_session, active_monitoring, stub_http
):
    from app.monitoring.service import run_obligation_monitoring

    stub_http(
        _ok_handler([{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}])
    )
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    response = await client.get(
        f"/api/v1/monitoring/rules/{active_monitoring.id}/evidence",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    entry = rows[0]
    assert entry["source"] == "rest_api"
    assert entry["source_identifier"] == "c1"
    assert entry["monitoring_run_id"] is not None
    assert entry["obligation_id"] == str(active_monitoring.obligation_id)
    assert entry["payload_hash"]
    assert entry["value"]["external_id"] == "c1"


# --------------------------------------------------------------------------
# Automation on PASS (3.15.36) — conservative, no auto-complete of obligation
# --------------------------------------------------------------------------

async def _set_automation(db_session, active_monitoring, action: str):
    from app.monitoring.models import ObligationMonitoring

    rule = await db_session.get(ObligationMonitoring, active_monitoring.id)
    rule.automation = {"on_pass": action}
    await db_session.flush()
    return rule


async def test_automation_mark_task_ready_progresses_pending_obligation_task(
    db_session,
    test_org,
    test_user,
    monitoring_obligation,
    active_monitoring,
    stub_http,
):
    from app.models.user_task import UserTask
    from app.monitoring.service import run_obligation_monitoring

    task = UserTask(
        organization_id=test_org.id,
        user_id=test_user.id,
        title="Submit monthly claims",
        status="pending",
        task_type="obligation",
        agreement_id=monitoring_obligation.agreement_id,
    )
    db_session.add(task)
    await db_session.commit()
    await _set_automation(db_session, active_monitoring, "MARK_TASK_READY")

    stub_http(
        _ok_handler([{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}])
    )
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    refreshed = await db_session.get(UserTask, task.id)
    assert refreshed.status == "in_progress"
    assert refreshed.completed_at is None
    assert await _count_events(db_session, "obligation.automation_triggered") == 1


async def test_automation_complete_task_completes_task_not_obligation(
    db_session,
    test_org,
    test_user,
    monitoring_obligation,
    active_monitoring,
    stub_http,
):
    from app.models.obligation import Obligation
    from app.models.user_task import UserTask
    from app.monitoring.service import run_obligation_monitoring

    task = UserTask(
        organization_id=test_org.id,
        user_id=test_user.id,
        title="Submit monthly claims",
        status="pending",
        task_type="obligation",
        agreement_id=monitoring_obligation.agreement_id,
    )
    db_session.add(task)
    await db_session.commit()
    await _set_automation(db_session, active_monitoring, "COMPLETE_TASK")

    stub_http(
        _ok_handler([{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}])
    )
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    refreshed = await db_session.get(UserTask, task.id)
    assert refreshed.status == "completed"
    assert refreshed.completed_at is not None

    obligation = await db_session.get(Obligation, monitoring_obligation.id)
    assert obligation.status == "OPEN"


async def test_automation_attach_evidence_links_obligation_evidence(
    db_session,
    test_org,
    test_user,
    monitoring_obligation,
    active_monitoring,
    monitoring_integration,
    stub_http,
):
    from app.models.obligation import ObligationEvidence
    from app.monitoring.models import MonitoringEvidence
    from app.monitoring.service import run_obligation_monitoring

    await _set_automation(db_session, active_monitoring, "ATTACH_EVIDENCE")

    stub_http(
        _ok_handler([{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}])
    )
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    evidence = (
        await db_session.execute(
            select(MonitoringEvidence).where(
                MonitoringEvidence.monitoring_id == active_monitoring.id
            )
        )
    ).scalars().all()
    assert evidence and evidence[0].attached_to_obligation is True

    oblig_evidence = (
        await db_session.execute(
            select(ObligationEvidence).where(
                ObligationEvidence.obligation_id == monitoring_obligation.id
            )
        )
    ).scalars().all()
    assert len(oblig_evidence) == 1
    assert oblig_evidence[0].submitted_by == test_user.id
    assert oblig_evidence[0].status == "SUBMITTED"
    assert oblig_evidence[0].submitted_at is not None


async def test_automation_no_action_touches_nothing(
    db_session,
    test_org,
    test_user,
    monitoring_obligation,
    active_monitoring,
    stub_http,
):
    from app.models.obligation import ObligationEvidence
    from app.models.user_task import UserTask
    from app.monitoring.service import run_obligation_monitoring

    task = UserTask(
        organization_id=test_org.id,
        user_id=test_user.id,
        title="Submit monthly claims",
        status="pending",
        task_type="obligation",
        agreement_id=monitoring_obligation.agreement_id,
    )
    db_session.add(task)
    await db_session.commit()

    stub_http(
        _ok_handler([{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}])
    )
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    refreshed = await db_session.get(UserTask, task.id)
    assert refreshed.status == "pending"
    assert (
        len(
            (
                await db_session.execute(
                    select(ObligationEvidence).where(
                        ObligationEvidence.obligation_id == monitoring_obligation.id
                    )
                )
            ).scalars().all()
        )
        == 0
    )
    assert await _count_events(db_session, "obligation.automation_triggered") == 0


# --------------------------------------------------------------------------
# Risk escalation from repeated FAIL (3.15.50-51)
# --------------------------------------------------------------------------

async def test_repeated_fail_raises_monitoring_risk_finding(
    db_session, active_monitoring, monitoring_obligation, agreement_version, stub_http
):
    from app.models.ai_analysis import RiskFinding
    from app.monitoring.enums import EvaluationResult
    from app.monitoring.models import ObligationMonitoring
    from app.monitoring.service import run_obligation_monitoring

    rule = await db_session.get(ObligationMonitoring, active_monitoring.id)
    rule.evaluation_definition = {"kind": "existence", "expected": True, "risk_threshold": 2}
    await db_session.flush()

    stub_http(_ok_handler([]))
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    for i in range(2):
        run_at = now + timedelta(minutes=60 * i)
        evaluation = await run_obligation_monitoring(
            db_session, monitoring=active_monitoring, run_at=run_at, now=run_at
        )
        await db_session.flush()
        assert evaluation.result == EvaluationResult.FAIL.value

    finding = (
        await db_session.execute(
            select(RiskFinding).where(
                RiskFinding.agreement_id == monitoring_obligation.agreement_id
            )
        )
    ).scalars().all()
    assert len(finding) == 1
    row = finding[0]
    assert row.category == "monitoring"
    assert row.severity == "HIGH"
    assert row.reviewer_status == "pending"
    assert row.version_id == agreement_version.id
    assert row.evidence["source_type"] == "OBLIGATION_ANALYSIS"
    assert row.evidence["monitoring_id"] == str(active_monitoring.id)
    assert row.evidence["obligation_id"] == str(monitoring_obligation.id)
    assert len(row.evidence["evaluation_ids"]) == 2


async def test_repeated_fail_does_not_duplicate_finding(
    db_session, active_monitoring, monitoring_obligation, stub_http
):
    from app.models.ai_analysis import RiskFinding
    from app.monitoring.models import ObligationMonitoring
    from app.monitoring.service import run_obligation_monitoring

    rule = await db_session.get(ObligationMonitoring, active_monitoring.id)
    rule.evaluation_definition = {"kind": "existence", "expected": True, "risk_threshold": 2}
    await db_session.flush()

    stub_http(_ok_handler([]))
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    for i in range(3):
        run_at = now + timedelta(minutes=60 * i)
        await run_obligation_monitoring(
            db_session, monitoring=active_monitoring, run_at=run_at, now=run_at
        )
        await db_session.flush()

    findings = (
        await db_session.execute(
            select(RiskFinding).where(
                RiskFinding.agreement_id == monitoring_obligation.agreement_id,
                RiskFinding.reviewer_status == "pending",
            )
        )
    ).scalars().all()
    assert len(findings) == 1


async def test_single_fail_below_threshold_raises_no_finding(
    db_session, active_monitoring, monitoring_obligation, stub_http
):
    from app.models.ai_analysis import RiskFinding
    from app.monitoring.service import run_obligation_monitoring

    stub_http(_ok_handler([]))
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    assert (
        len(
            (
                await db_session.execute(
                    select(RiskFinding).where(
                        RiskFinding.agreement_id == monitoring_obligation.agreement_id
                    )
                )
            ).scalars().all()
        )
        == 0
    )


# --------------------------------------------------------------------------
# Notification recipient resolution (3.15.49)
# --------------------------------------------------------------------------

async def test_notification_prefers_obligation_owner(
    db_session, test_org, monitoring_obligation, active_monitoring, stub_http
):
    from app.core.security import hash_password
    from app.models.obligation import ObligationAssignee
    from app.models.user import User
    from app.monitoring.service import run_obligation_monitoring

    owner = User(
        email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        name="Obligation Owner",
        password_hash=hash_password("TestPass123!"),
        status="active",
    )
    db_session.add(owner)
    await db_session.flush()
    db_session.add(
        ObligationAssignee(
            obligation_id=monitoring_obligation.id,
            member_id=owner.id,
            responsibility_type="INTERNAL_OWNER",
            primary_assignee=True,
        )
    )
    await db_session.commit()

    stub_http(_ok_handler([]))
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    payloads = await _event_payloads(db_session, "monitoring.evaluation_failed")
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["recipient_user_id"] == str(owner.id)
    assert payload["recipient_email"] == owner.email
    assert payload["recipients"][0] == {
        "user_id": str(owner.id),
        "email": owner.email,
        "role": "obligation_owner",
    }


async def test_notification_no_assignee_falls_back_to_integration_owner(
    db_session, test_user, active_monitoring, stub_http
):
    from app.monitoring.service import run_obligation_monitoring

    stub_http(_ok_handler([]))
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    payloads = await _event_payloads(db_session, "monitoring.evaluation_failed")
    assert payloads[0]["recipient_user_id"] == str(test_user.id)
    assert payloads[0]["recipients"][0]["role"] == "integration_owner"


# --------------------------------------------------------------------------
# Spec audit events (3.15.47)
# --------------------------------------------------------------------------

async def test_pass_run_audits_spec_events(
    db_session, active_monitoring, test_org, stub_http
):
    from app.monitoring.service import (
        AUDIT_EVALUATION_COMPLETED,
        AUDIT_OBSERVATION_RECEIVED,
        AUDIT_RUN_COMPLETED,
        AUDIT_RUN_STARTED,
        run_obligation_monitoring,
    )

    stub_http(
        _ok_handler([{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}])
    )
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    actions = await _audit_actions(db_session, test_org.id)
    assert AUDIT_RUN_STARTED in actions
    assert AUDIT_OBSERVATION_RECEIVED in actions
    assert AUDIT_EVALUATION_COMPLETED in actions
    assert AUDIT_RUN_COMPLETED in actions


async def test_fail_run_audits_exception_opened(
    db_session, active_monitoring, test_org, stub_http
):
    from app.monitoring.service import (
        AUDIT_EVALUATION_COMPLETED,
        AUDIT_EXCEPTION_OPENED,
        run_obligation_monitoring,
    )

    stub_http(_ok_handler([]))
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    actions = await _audit_actions(db_session, test_org.id)
    assert AUDIT_EXCEPTION_OPENED in actions
    assert AUDIT_EVALUATION_COMPLETED in actions


# --------------------------------------------------------------------------
# API-side audit events (3.15.47)
# --------------------------------------------------------------------------

async def test_api_create_integration_audits_spec_event(client, auth_headers, test_org, db_session):
    from app.monitoring.service import AUDIT_INTEGRATION_CREATED

    response = await client.post(
        "/api/v1/monitoring/integrations",
        json={
            "name": "Audited API",
            "integration_type": "REST_API",
            "provider_key": "rest_api",
            "configuration": {"base_url": "https://vendor.example.internal"},
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    actions = await _audit_actions(db_session, test_org.id)
    assert AUDIT_INTEGRATION_CREATED in actions


async def test_api_rule_create_and_pause_audit_spec_events(
    client, auth_headers, test_org, db_session, active_monitoring
):
    from app.monitoring.service import (
        AUDIT_MONITORING_ACTIVATED,
        AUDIT_MONITORING_CREATED,
        AUDIT_MONITORING_PAUSED,
    )

    created = await client.post(
        "/api/v1/monitoring/rules",
        json={
            "obligation_id": str(active_monitoring.obligation_id),
            "source_version_id": str(active_monitoring.source_version_id),
            "integration_id": str(active_monitoring.integration_id),
            "query_definition": {"resource": "claims", "fields": ["id", "amount"]},
            "evaluation_definition": {"kind": "existence", "expected": True},
            "schedule_definition": {"recurrence": "interval", "minutes": 60},
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]

    actions = await _audit_actions(db_session, test_org.id)
    assert AUDIT_MONITORING_CREATED in actions

    await client.post(f"/api/v1/monitoring/rules/{rule_id}/activate", headers=auth_headers)
    await client.post(f"/api/v1/monitoring/rules/{rule_id}/pause", headers=auth_headers)

    actions = await _audit_actions(db_session, test_org.id)
    assert AUDIT_MONITORING_ACTIVATED in actions
    assert AUDIT_MONITORING_PAUSED in actions


async def test_api_delete_integration_audits_disconnected(
    client, auth_headers, test_org, db_session
):
    from app.monitoring.service import AUDIT_INTEGRATION_DISCONNECTED

    created = await client.post(
        "/api/v1/monitoring/integrations",
        json={
            "name": "Delete me",
            "integration_type": "REST_API",
            "provider_key": "rest_api",
            "configuration": {"base_url": "https://vendor.example.internal"},
        },
        headers=auth_headers,
    )
    integration_id = created.json()["id"]
    deleted = await client.delete(
        f"/api/v1/monitoring/integrations/{integration_id}", headers=auth_headers
    )
    assert deleted.status_code == 200, deleted.text

    actions = await _audit_actions(db_session, test_org.id)
    assert AUDIT_INTEGRATION_DISCONNECTED in actions


async def test_webhook_bad_signature_audits_rejected(
    client, test_org, test_user, monitoring_obligation, agreement_version, db_session
):
    from app.monitoring.models import IntegrationConnection, IntegrationCredential
    from app.monitoring.service import AUDIT_OBSERVATION_REJECTED

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
    await db_session.commit()

    body = json.dumps({"id": "x", "observed_at": "2026-09-25T11:58:00Z"}).encode("utf-8")
    response = await client.post(
        f"/api/v1/integrations/{integration.id}/webhook",
        content=body,
        headers={"x-monitoring-signature": "deadbeef"},
    )
    assert response.status_code == 401

    actions = await _audit_actions(db_session, test_org.id)
    assert AUDIT_OBSERVATION_REJECTED in actions


# --------------------------------------------------------------------------
# Failure policy (3.15.27), disconnect (3.15.53), cred expiring (3.15.49),
# config-time validation (3.15.55)
# --------------------------------------------------------------------------

async def test_failure_policy_pauses_rules_at_threshold(
    db_session, test_org, test_user, agreement_version, monitoring_obligation
):
    from app.monitoring.enums import PauseReason
    from app.monitoring.models import IntegrationConnection, ObligationMonitoring
    from app.monitoring.service import (
        AUDIT_MONITORING_PAUSED,
        EVENT_SOURCE_UNAVAILABLE,
        run_obligation_monitoring,
    )

    integration = IntegrationConnection(
        organization_id=test_org.id,
        name="Flaky source",
        integration_type="REST_API",
        provider_key="no_such_connector",
        status="ACTIVE",
        configuration={
            "failure_policy": {"max_consecutive_failures": 2, "action": "PAUSE_MONITORING"}
        },
        created_by=test_user.id,
        created_by_name=test_user.name,
    )
    db_session.add(integration)
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
    )
    db_session.add(rule)
    await db_session.commit()

    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=rule, run_at=now, now=now)
    await db_session.flush()
    assert rule.status == "ACTIVE"

    second_now = now + timedelta(minutes=60)
    await run_obligation_monitoring(db_session, monitoring=rule, run_at=second_now, now=second_now)
    await db_session.flush()
    assert rule.status == "PAUSED"
    assert rule.pause_reason == PauseReason.FAILURE_POLICY.value

    actions = await _audit_actions(db_session, test_org.id)
    assert AUDIT_MONITORING_PAUSED in actions
    assert await _count_events(db_session, EVENT_SOURCE_UNAVAILABLE) == 3
    pause_payloads = await _event_payloads(db_session, EVENT_SOURCE_UNAVAILABLE)
    assert any("monitoring paused pending source fix" in str(p.get("reason")) for p in pause_payloads)


async def test_failure_policy_below_threshold_keeps_rule_active(
    db_session, test_org, test_user, agreement_version, monitoring_obligation
):
    from app.monitoring.models import IntegrationConnection, ObligationMonitoring
    from app.monitoring.service import run_obligation_monitoring

    integration = IntegrationConnection(
        organization_id=test_org.id,
        name="Flaky source",
        integration_type="REST_API",
        provider_key="no_such_connector",
        status="ACTIVE",
        configuration={
            "failure_policy": {"max_consecutive_failures": 5, "action": "PAUSE_MONITORING"}
        },
        created_by=test_user.id,
        created_by_name=test_user.name,
    )
    db_session.add(integration)
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
    )
    db_session.add(rule)
    await db_session.commit()

    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=rule, run_at=now, now=now)
    await db_session.flush()
    assert rule.status == "ACTIVE"


async def test_api_integration_disconnect_sets_status_and_audits(
    client, auth_headers, test_org, db_session
):
    from app.monitoring.models import IntegrationConnection
    from app.monitoring.service import AUDIT_INTEGRATION_DISCONNECTED

    created = await client.post(
        "/api/v1/monitoring/integrations",
        json={
            "name": "Disconnect me",
            "integration_type": "REST_API",
            "provider_key": "rest_api",
            "configuration": {"base_url": "https://vendor.example.internal"},
        },
        headers=auth_headers,
    )
    integration_id = created.json()["id"]
    resp = await client.post(
        f"/api/v1/monitoring/integrations/{integration_id}/disconnect",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "disconnected"

    integration = await db_session.get(IntegrationConnection, uuid.UUID(integration_id))
    assert integration is not None
    assert integration.status == "DISCONNECTED"
    actions = await _audit_actions(db_session, test_org.id)
    assert AUDIT_INTEGRATION_DISCONNECTED in actions


async def test_detect_expiring_credentials_notifies_and_audits(
    db_session, test_org, test_user
):
    from app.monitoring.models import IntegrationConnection, IntegrationCredential
    from app.monitoring.service import EVENT_CREDENTIAL_EXPIRING, detect_expiring_credentials

    integration = IntegrationConnection(
        organization_id=test_org.id,
        name="Credentialed API",
        integration_type="REST_API",
        provider_key="rest_api",
        status="ACTIVE",
        configuration={},
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
            expires_at=datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc),
        )
    )
    db_session.add(
        IntegrationCredential(
            integration_id=integration.id,
            secret_reference="env://MONITORING_TEST_TOKEN",
            status="ACTIVE",
            expires_at=datetime(2026, 12, 31, 0, 0, tzinfo=timezone.utc),
        )
    )
    await db_session.commit()

    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    notified = await detect_expiring_credentials(db_session, now=now, within_days=3)
    await db_session.commit()
    assert notified == 1

    payloads = await _event_payloads(db_session, EVENT_CREDENTIAL_EXPIRING)
    assert len(payloads) == 1
    assert payloads[0]["integration_id"] == str(integration.id)
    assert payloads[0]["recipient_email"] == test_user.email

    actions = await _audit_actions(db_session, test_org.id)
    assert "INTEGRATION_CREDENTIAL_EXPIRING" in actions


async def test_api_rule_rejects_unknown_evaluator(client, auth_headers, active_monitoring):
    resp = await client.post(
        "/api/v1/monitoring/rules",
        json={
            "obligation_id": str(active_monitoring.obligation_id),
            "source_version_id": str(active_monitoring.source_version_id),
            "integration_id": str(active_monitoring.integration_id),
            "query_definition": {"resource": "claims"},
            "evaluation_definition": {"kind": "crystal_ball", "expected": True},
            "schedule_definition": {"recurrence": "interval", "minutes": 60},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text


async def test_api_rule_rejects_retired_obligation(
    client,
    auth_headers,
    db_session,
    test_org,
    test_agreement,
    agreement_version,
    monitoring_integration,
):
    from app.models.obligation import Obligation

    obligation = Obligation(
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        title="Already completed",
        owner_party="Vendor Ltd",
        description="Terminal obligation",
        obligation_type="reporting",
        status="COMPLETED",
        criticality="LOW",
        evidence_status="NOT_REQUIRED",
    )
    db_session.add(obligation)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/monitoring/rules",
        json={
            "obligation_id": str(obligation.id),
            "source_version_id": str(agreement_version.id),
            "integration_id": str(monitoring_integration.id),
            "query_definition": {"resource": "claims"},
            "evaluation_definition": {"kind": "existence", "expected": True},
            "schedule_definition": {"recurrence": "interval", "minutes": 60},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text


async def test_api_rule_rejects_unknown_automation_action(
    client, auth_headers, active_monitoring
):
    resp = await client.post(
        "/api/v1/monitoring/rules",
        json={
            "obligation_id": str(active_monitoring.obligation_id),
            "source_version_id": str(active_monitoring.source_version_id),
            "integration_id": str(active_monitoring.integration_id),
            "query_definition": {"resource": "claims"},
            "evaluation_definition": {"kind": "existence", "expected": True},
            "schedule_definition": {"recurrence": "interval", "minutes": 60},
            "automation": {"on_pass": "DELETE_EVERYTHING"},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text


# --------------------------------------------------------------------------
# Read API completeness (3.15.53): GET /integrations/{id}, /health, /runs,
# rotate-credentials; provenance (3.15.59); auth-failed audit (3.15.47)
# --------------------------------------------------------------------------

async def test_api_get_single_integration(client, auth_headers, test_org, db_session):
    created = await client.post(
        "/api/v1/monitoring/integrations",
        json={
            "name": "Single lookup",
            "integration_type": "REST_API",
            "provider_key": "rest_api",
            "configuration": {"base_url": "https://vendor.example.internal"},
        },
        headers=auth_headers,
    )
    integration_id = created.json()["id"]
    resp = await client.get(
        f"/api/v1/monitoring/integrations/{integration_id}", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == integration_id
    assert body["name"] == "Single lookup"
    assert body["status"] == "ACTIVE"


async def test_api_get_integration_unknown_org_404(client, auth_headers, db_session, test_org):
    other = uuid.uuid4()
    resp = await client.get(
        f"/api/v1/monitoring/integrations/{other}", headers=auth_headers
    )
    assert resp.status_code == 404, resp.text


async def test_api_monitoring_health_summary(
    client, auth_headers, test_org, monitoring_integration, active_monitoring
):
    resp = await client.get("/api/v1/monitoring/health", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["integrations_total"] == 1
    assert body["integrations"][0]["integration_id"] == str(monitoring_integration.id)
    assert body["integrations"][0]["active_rules"] == 1
    assert body["degraded"] == 0


async def test_api_rotate_credentials_audits_and_revokes_previous(
    client, auth_headers, test_org, db_session
):
    from app.monitoring.models import IntegrationConnection, IntegrationCredential
    from app.monitoring.service import AUDIT_INTEGRATION_CREDENTIAL_ROTATED

    created = await client.post(
        "/api/v1/monitoring/integrations",
        json={
            "name": "Rotate creds",
            "integration_type": "REST_API",
            "provider_key": "rest_api",
            "configuration": {"base_url": "https://vendor.example.internal"},
        },
        headers=auth_headers,
    )
    integration_id = uuid.UUID(created.json()["id"])

    first = await client.post(
        f"/api/v1/monitoring/integrations/{integration_id}/credentials",
        json={"secret_reference": "env://MONITORING_TEST_TOKEN"},
        headers=auth_headers,
    )
    assert first.status_code == 201, first.text
    old_id = uuid.UUID(first.json()["id"])

    resp = await client.post(
        f"/api/v1/monitoring/integrations/{integration_id}/rotate-credentials",
        json={"secret_reference": "env://MONITORING_TEST_TOKEN"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    new_id = uuid.UUID(resp.json()["id"])
    assert new_id != old_id
    assert resp.json()["status"] == "ACTIVE"

    old = await db_session.get(IntegrationCredential, old_id)
    assert old is not None
    assert old.status == "REVOKED"
    assert old.last_rotated_at is not None
    new = await db_session.get(IntegrationCredential, new_id)
    assert new.status == "ACTIVE"

    actions = await _audit_actions(db_session, test_org.id)
    assert AUDIT_INTEGRATION_CREDENTIAL_ROTATED in actions


async def test_api_rotate_credentials_rejects_bad_secret_reference(
    client, auth_headers, test_org
):
    created = await client.post(
        "/api/v1/monitoring/integrations",
        json={
            "name": "Rotate bad",
            "integration_type": "REST_API",
            "provider_key": "rest_api",
            "configuration": {"base_url": "https://vendor.example.internal"},
        },
        headers=auth_headers,
    )
    integration_id = created.json()["id"]
    resp = await client.post(
        f"/api/v1/monitoring/integrations/{integration_id}/rotate-credentials",
        json={"secret_reference": "plaintext-credential"},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text


async def test_api_list_runs(db_session, monitoring_integration, active_monitoring, stub_http):
    from app.monitoring.service import run_obligation_monitoring

    stub_http(
        _ok_handler([{"id": "c1", "amount": 100, "observed_at": "2026-09-25T12:00:00Z"}])
    )
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    from app.monitoring.models import MonitoringRun

    rows = (
        await db_session.execute(
            select(MonitoringRun).where(
                MonitoringRun.monitoring_id == active_monitoring.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_evaluation_stores_evaluator_provenance(
    db_session, monitoring_integration, active_monitoring, stub_http
):
    from app.monitoring.models import MonitoringEvaluation
    from app.monitoring.service import run_obligation_monitoring

    stub_http(
        _ok_handler([{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}])
    )
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(MonitoringEvaluation).where(
                MonitoringEvaluation.monitoring_id == active_monitoring.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    ev = rows[0]
    assert ev.metrics.get("evaluator") == active_monitoring.evaluation_definition.get("kind")


async def test_runtime_credential_failure_audits_auth_failed(
    db_session, test_org, test_user, agreement_version, monitoring_obligation
):
    from app.monitoring.models import IntegrationConnection, ObligationMonitoring
    from app.monitoring.service import AUDIT_INTEGRATION_AUTH_FAILED, run_obligation_monitoring

    integration = IntegrationConnection(
        organization_id=test_org.id,
        name="Expired cred source",
        integration_type="REST_API",
        provider_key="rest_api",
        status="ACTIVE",
        configuration={},
        created_by=test_user.id,
        created_by_name=test_user.name,
    )
    db_session.add(integration)
    await db_session.flush()
    rule = ObligationMonitoring(
        organization_id=test_org.id,
        obligation_id=monitoring_obligation.id,
        source_version_id=agreement_version.id,
        integration_id=integration.id,
        status="ACTIVE",
        query_definition={"resource": "deliveries", "fields": ["id"]},
        evaluation_definition={"kind": "existence", "expected": True},
        schedule_definition={"recurrence": "interval", "minutes": 60},
        automation={},
    )
    db_session.add(rule)
    await db_session.commit()

    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=rule, run_at=now, now=now)
    await db_session.flush()

    actions = await _audit_actions(db_session, test_org.id)
    assert AUDIT_INTEGRATION_AUTH_FAILED in actions