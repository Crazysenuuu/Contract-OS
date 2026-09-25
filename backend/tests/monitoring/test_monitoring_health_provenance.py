"""Health + provenance gap coverage for spec 3.15: credential status
surfacing (3.15.26), the light circuit breaker (3.15.62), source-version
provenance on evaluations and evidence (3.15.59), webhook signature
persistence (3.15.30), eager rule validation (3.15.55) and workflow
participant recipient fan-out (3.15.49).

Reuses the pipeline module's transport-stub pattern so the real REST
connector is exercised without touching the network.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.monitoring.models import (
    MonitoringEvaluation,
    MonitoringEvidence,
    MonitoringWebhookEvent,
)
from app.monitoring.service import (
    _credential_status_from_reason,
    ingest_webhook_observations,
    run_obligation_monitoring,
)

EVENT_EVALUATION_FAILED = "monitoring.evaluation_failed"
EVENT_SOURCE_UNAVAILABLE = "monitoring.source_unavailable"


# --------------------------------------------------------------------------
# Transport stub (per-module copy; shared one lives in the pipeline module)
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


def _fail_handler(message="source exploded"):
    from app.monitoring.exceptions import ConnectorUnavailable

    def handler(method, url, params, headers):
        raise ConnectorUnavailable(message)

    return handler


async def _event_payloads(db_session, event_type: str) -> list[dict]:
    from app.models.event_outbox import OutboxEvent

    rows = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == event_type)
        )
    ).scalars().all()
    return [row.payload for row in rows]


# --------------------------------------------------------------------------
# credential_status (3.15.26) + circuit breaker (3.15.62)
# --------------------------------------------------------------------------

def test_credential_status_from_reason_classification():
    assert _credential_status_from_reason("credentials expired 2026-08-01") == "EXPIRED"
    assert _credential_status_from_reason("token revoked by provider") == "REVOKED"
    assert _credential_status_from_reason("401 unauthorized") == "INVALID"


async def test_circuit_breaker_opens_skips_and_closes(
    db_session,
    monitoring_obligation,
    monitoring_integration,
    agreement_version,
    test_user,
    stub_http,
):
    from app.monitoring.models import (
        IntegrationConnection,
        IntegrationHealth,
        ObligationMonitoring,
    )

    monitoring_integration.configuration["circuit_breaker"] = {
        "enabled": True,
        "threshold": 2,
        "cooldown_minutes": 10,
    }
    await db_session.flush()

    rule = ObligationMonitoring(
        organization_id=monitoring_integration.organization_id,
        obligation_id=monitoring_obligation.id,
        source_version_id=agreement_version.id,
        integration_id=monitoring_integration.id,
        status="ACTIVE",
        query_definition={"resource": "claims", "fields": ["id"]},
        evaluation_definition={"kind": "existence", "expected": True},
        schedule_definition={"recurrence": "interval", "minutes": 60},
        automation={},
        created_by=test_user.id,
    )
    db_session.add(rule)
    await db_session.flush()

    stub_http(_fail_handler())
    base = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)

    await run_obligation_monitoring(db_session, monitoring=rule, run_at=base, now=base)
    await db_session.flush()
    health = await db_session.get(IntegrationHealth, monitoring_integration.id)
    assert health.consecutive_failures == 1
    assert health.circuit_open_until is None

    await run_obligation_monitoring(
        db_session, monitoring=rule, run_at=base + timedelta(minutes=1), now=base + timedelta(minutes=1)
    )
    await db_session.flush()
    health = await db_session.get(IntegrationHealth, monitoring_integration.id)
    assert health.consecutive_failures == 2
    assert health.circuit_open_until == base + timedelta(minutes=11)

    # Skip while inside cooldown: INCONCLUSIVE, streak NOT grown, no re-alert.
    skip_at = base + timedelta(minutes=2)
    evaluation = await run_obligation_monitoring(
        db_session, monitoring=rule, run_at=skip_at, now=skip_at
    )
    await db_session.flush()
    assert evaluation.result == "INCONCLUSIVE"
    assert evaluation.details["exception_type"] == "CircuitOpenError"
    health = await db_session.get(IntegrationHealth, monitoring_integration.id)
    assert health.consecutive_failures == 2
    assert health.circuit_open_until == base + timedelta(minutes=11)
    source_unavailable_count = len(
        await _event_payloads(db_session, EVENT_SOURCE_UNAVAILABLE)
    )
    assert source_unavailable_count == 2

    # Cooldown expires → healthy fetch closes the circuit and resets the streak.
    stub_http(
        _ok_handler([{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}])
    )
    close_at = base + timedelta(minutes=12)
    await run_obligation_monitoring(db_session, monitoring=rule, run_at=close_at, now=close_at)
    await db_session.flush()
    health = await db_session.get(IntegrationHealth, monitoring_integration.id)
    assert health.circuit_open_until is None
    assert health.consecutive_failures == 0
    refreshed = (
        await db_session.execute(
            select(IntegrationConnection).where(
                IntegrationConnection.id == monitoring_integration.id
            )
        )
    ).scalar_one()
    assert refreshed.status == "ACTIVE"


async def test_circuit_breaker_disabled_never_opens(
    db_session,
    monitoring_obligation,
    monitoring_integration,
    agreement_version,
    test_user,
    stub_http,
):
    from app.monitoring.models import IntegrationHealth, ObligationMonitoring

    monitoring_integration.configuration["circuit_breaker"] = {"enabled": False}
    await db_session.flush()

    rule = ObligationMonitoring(
        organization_id=monitoring_integration.organization_id,
        obligation_id=monitoring_obligation.id,
        source_version_id=agreement_version.id,
        integration_id=monitoring_integration.id,
        status="ACTIVE",
        query_definition={"resource": "claims", "fields": ["id"]},
        evaluation_definition={"kind": "existence", "expected": True},
        schedule_definition={"recurrence": "interval", "minutes": 60},
        automation={},
        created_by=test_user.id,
    )
    db_session.add(rule)
    await db_session.flush()

    stub_http(_fail_handler())
    base = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    for i in range(3):
        at = base + timedelta(minutes=i)
        await run_obligation_monitoring(db_session, monitoring=rule, run_at=at, now=at)
        await db_session.flush()

    health = await db_session.get(IntegrationHealth, monitoring_integration.id)
    assert health.consecutive_failures == 3
    assert health.circuit_open_until is None


# --------------------------------------------------------------------------
# Version provenance on evaluations + evidence (3.15.59)
# --------------------------------------------------------------------------

async def test_source_version_provenance_on_evaluation_and_evidence(
    db_session,
    active_monitoring,
    agreement_version,
    stub_http,
):
    stub_http(
        _ok_handler([{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}])
    )
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    evaluation = (
        await db_session.execute(
            select(MonitoringEvaluation).where(
                MonitoringEvaluation.monitoring_id == active_monitoring.id
            )
        )
    ).scalars().one()
    assert evaluation.source_version_id == agreement_version.id

    evidence_rows = (
        await db_session.execute(
            select(MonitoringEvidence).where(
                MonitoringEvidence.monitoring_id == active_monitoring.id
            )
        )
    ).scalars().all()
    assert evidence_rows
    assert all(row.source_version_id == agreement_version.id for row in evidence_rows)


# --------------------------------------------------------------------------
# Webhook signature persistence (3.15.30)
# --------------------------------------------------------------------------

async def test_webhook_signature_persisted(db_session, monitoring_integration):
    await ingest_webhook_observations(
        db_session,
        integration_id=monitoring_integration.id,
        payload={"id": "evt-1", "status": "in_progress"},
        provider_event_id="evt-1",
        signature="sha256=abcdef123456",
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(MonitoringWebhookEvent).where(
                MonitoringWebhookEvent.provider_event_id == "evt-1"
            )
        )
    ).scalars().one()
    assert row.signature == "sha256=abcdef123456"


# --------------------------------------------------------------------------
# Eager rule validation (3.15.55)
# --------------------------------------------------------------------------

async def test_api_rule_create_rejects_unknown_operator(
    client,
    auth_headers,
    monitoring_integration,
    monitoring_obligation,
    agreement_version,
):
    created = await client.post(
        "/api/v1/monitoring/rules",
        json={
            "obligation_id": str(monitoring_obligation.id),
            "integration_id": str(monitoring_integration.id),
            "source_version_id": str(agreement_version.id),
            "status": "ACTIVE",
            "query_definition": {"resource": "claims"},
            "evaluation_definition": {
                "kind": "threshold",
                "field": "amount",
                "operator": "banana",
                "expected_value": 100,
            },
            "schedule_definition": {"recurrence": "interval", "minutes": 60},
            "automation": {},
        },
        headers=auth_headers,
    )
    assert created.status_code == 400, created.text
    assert "operator" in created.text


async def test_api_rule_create_rejects_invalid_field_path(
    client,
    auth_headers,
    monitoring_integration,
    monitoring_obligation,
    agreement_version,
):
    created = await client.post(
        "/api/v1/monitoring/rules",
        json={
            "obligation_id": str(monitoring_obligation.id),
            "integration_id": str(monitoring_integration.id),
            "source_version_id": str(agreement_version.id),
            "status": "ACTIVE",
            "query_definition": {"resource": "claims"},
            "evaluation_definition": {
                "kind": "threshold",
                "field": "__proto__",
                "operator": "gt",
                "expected_value": 100,
            },
            "schedule_definition": {"recurrence": "interval", "minutes": 60},
            "automation": {},
        },
        headers=auth_headers,
    )
    assert created.status_code == 400, created.text


async def test_api_rule_create_accepts_nested_freshness_max_age(
    client,
    auth_headers,
    monitoring_integration,
    monitoring_obligation,
    agreement_version,
):
    created = await client.post(
        "/api/v1/monitoring/rules",
        json={
            "obligation_id": str(monitoring_obligation.id),
            "integration_id": str(monitoring_integration.id),
            "source_version_id": str(agreement_version.id),
            "status": "ACTIVE",
            "query_definition": {"resource": "claims"},
            "evaluation_definition": {
                "kind": "freshness",
                "max_age": {"amount": 24, "unit": "HOUR"},
            },
            "schedule_definition": {"recurrence": "interval", "minutes": 60},
            "automation": {},
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text


def test_freshness_evaluator_accepts_nested_max_age_shape():
    from datetime import datetime as dt, timezone as tz
    from app.monitoring.connectors.base import ExternalObservation
    from app.monitoring.enums import EvaluationResult
    from app.monitoring.evaluators import get_evaluator

    evaluator = get_evaluator("freshness")
    outcome = evaluator.evaluate(
        observations=[
            ExternalObservation(
                external_id="c1",
                observed_at=dt(2026, 9, 25, 11, 0, tzinfo=tz.utc),
                resource_type="claims",
                payload={},
            )
        ],
        definition={"max_age": {"amount": 24, "unit": "HOUR"}},
        context=__import__("types").SimpleNamespace(
            now=dt(2026, 9, 25, 12, 0, tzinfo=tz.utc)
        ),
    )
    assert outcome.result == EvaluationResult.PASS


# --------------------------------------------------------------------------
# Workflow participant recipient fan-out (3.15.49)
# --------------------------------------------------------------------------

async def test_notification_includes_workflow_participants(
    db_session,
    test_legal_entity,
    monitoring_obligation,
    active_monitoring,
    stub_http,
):
    from app.core.security import hash_password
    from app.models.agreement_access import AgreementParticipant, AgreementParty
    from app.models.user import User

    participant_user = User(
        email=f"participant-{uuid.uuid4().hex[:8]}@example.com",
        name="Agreement Participant",
        password_hash=hash_password("TestPass123!"),
        status="active",
    )
    db_session.add(participant_user)
    await db_session.flush()

    party = AgreementParty(
        agreement_id=monitoring_obligation.agreement_id,
        legal_entity_id=test_legal_entity.id,
        party_role="receiving",
        display_name="Counterparty Ltd",
    )
    db_session.add(party)
    await db_session.flush()

    db_session.add(
        AgreementParticipant(
            agreement_id=monitoring_obligation.agreement_id,
            agreement_party_id=party.id,
            user_id=participant_user.id,
            participant_role="lawyer",
            status="active",
            can_view=True,
            can_comment=True,
            can_propose_changes=True,
        )
    )
    await db_session.commit()

    stub_http(_ok_handler([]))
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await run_obligation_monitoring(db_session, monitoring=active_monitoring, run_at=now, now=now)
    await db_session.flush()

    payloads = await _event_payloads(db_session, EVENT_EVALUATION_FAILED)
    assert len(payloads) == 1
    participant_entries = [
        r for r in payloads[0]["recipients"] if r["role"] == "workflow_participant"
    ]
    assert participant_entries == [
        {"user_id": str(participant_user.id), "email": participant_user.email, "role": "workflow_participant"}
    ]