"""Unit tests for monitoring primitives (spec 3.15): model registration,
credential resolution, observation hashing/dedup, evaluators and schedule
arithmetic. No database calls except where the fixture session is required.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.base import Base


# --------------------------------------------------------------------------
# Model registration on Base.metadata
# --------------------------------------------------------------------------

MONITORING_TABLES = {
    "integration_connections",
    "integration_credentials",
    "integration_health",
    "obligation_monitoring",
    "external_observations",
    "monitoring_evaluations",
    "monitoring_exceptions",
    "monitoring_runs",
    "monitoring_webhook_events",
}


def test_monitoring_tables_registered_on_base_metadata():
    present = set(Base.metadata.tables)
    for table in MONITORING_TABLES:
        assert table in present, f"missing monitoring table {table}"


# --------------------------------------------------------------------------
# Credential resolution (spec 3.15.6 fail-closed)
# --------------------------------------------------------------------------

async def test_credentials_env_resolves_and_fails_closed(db_session):
    from app.monitoring.exceptions import CredentialUnavailable
    from app.monitoring.credentials import get_active_credentials, resolve_credential_value
    from app.monitoring.models import IntegrationCredential
    from app.monitoring.models import IntegrationConnection

    integration = IntegrationConnection(
        organization_id=uuid.uuid4(),
        name="env-integration",
        integration_type="REST_API",
        provider_key="rest_api",
        status="ACTIVE",
        configuration={},
    )
    db_session.add(integration)
    await db_session.flush()

    credential = IntegrationCredential(
        integration_id=integration.id,
        secret_reference="env://MONITORING_TEST_TOKEN",
        status="ACTIVE",
    )
    db_session.add(credential)
    await db_session.flush()

    resolved = await get_active_credentials(db_session, integration.id)
    assert [c.value for c in resolved] == ["test-bearer-token-123"]


async def test_credentials_unknown_scheme_raises(db_session):
    from app.monitoring.exceptions import CredentialUnavailable
    from app.monitoring.credentials import resolve_credential_value

    with pytest.raises(CredentialUnavailable):
        await resolve_credential_value(
            db_session,
            integration_id=uuid.uuid4(),
            secret_reference="plaintext-secret",
        )


async def test_credentials_secretman_closes_without_provider(db_session):
    from app.monitoring.exceptions import CredentialUnavailable
    from app.monitoring.credentials import resolve_credential_value

    with pytest.raises(CredentialUnavailable):
        await resolve_credential_value(
            db_session,
            integration_id=uuid.uuid4(),
            secret_reference="secretman://aws/secrets/prod/claim-token",
        )


async def test_credentials_no_active_rows_raises(db_session):
    from app.monitoring.exceptions import CredentialUnavailable
    from app.monitoring.credentials import get_active_credentials

    with pytest.raises(CredentialUnavailable):
        await get_active_credentials(db_session, uuid.uuid4())


# --------------------------------------------------------------------------
# Observation hashing / redaction / webhook time delivery (3.15.12-15, 3.15.30)
# --------------------------------------------------------------------------

def test_hash_payload_stable_and_ordered():
    from app.monitoring.observations import canon_json, hash_payload

    assert hash_payload({"b": 1, "a": 2}) == hash_payload({"a": 2, "b": 1})
    assert hash_payload({"x": [1, 2]}) != hash_payload({"x": [2, 1]})
    assert len(canon_json({"a": 1})) > 0


def test_redact_payload_strips_credential_shaped_fields():
    from app.monitoring.observations import redact_payload

    redacted = redact_payload(
        {
            "id": "c1",
            "amount": 100,
            "api_token": "LEAKED",
            "headers.authorization": "Bearer LEAKED",
        }
    )
    assert "id" in redacted
    assert "amount" in redacted
    assert "api_token" not in redacted
    assert "headers.authorization" not in redacted


async def test_persist_observations_dedup(db_session):
    from app.monitoring.connectors.base import ExternalObservation
    from app.monitoring.observations import persist_observations

    org_id, integration_id, monitoring_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    obs = [
        ExternalObservation(
            external_id="c1",
            observed_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
            resource_type="claims",
            payload={"id": "c1", "amount": 100},
        )
    ]
    first = await persist_observations(
        db_session,
        organization_id=org_id,
        integration_id=integration_id,
        monitoring_id=monitoring_id,
        observations=obs,
    )
    second = await persist_observations(
        db_session,
        organization_id=org_id,
        integration_id=integration_id,
        monitoring_id=monitoring_id,
        observations=obs,
    )
    assert len(first) == 1
    assert second == []


async def test_webhook_time_replay_window(db_session):
    from app.monitoring.exceptions import WebhookVerificationError
    from app.monitoring.observations import verify_webhook_time_delivery

    now = datetime.now(timezone.utc)
    recent = now - timedelta(seconds=30)
    await verify_webhook_time_delivery(recent, now, max_skew_seconds=300)

    stale = now - timedelta(seconds=600)
    with pytest.raises(WebhookVerificationError):
        await verify_webhook_time_delivery(stale, now, max_skew_seconds=300)


# --------------------------------------------------------------------------
# Evaluators
# --------------------------------------------------------------------------

def _observation(external_id="c1", **payload):
    from app.monitoring.connectors.base import ExternalObservation

    return ExternalObservation(
        external_id=external_id,
        observed_at=datetime.now(timezone.utc),
        resource_type=payload.pop("_resource_type", "claims"),
        payload=payload,
    )


def test_existence_evaluator_pass_and_fail():
    from app.monitoring.evaluators import EvaluationContext, get_evaluator
    from app.monitoring.enums import EvaluationResult

    evaluator = get_evaluator("existence")
    passed = evaluator.evaluate(
        observations=[_observation()],
        definition={"expected": True},
        context=EvaluationContext(),
    )
    assert passed.result == EvaluationResult.PASS

    missing = evaluator.evaluate(
        observations=[],
        definition={"expected": True},
        context=EvaluationContext(),
    )
    assert missing.result == EvaluationResult.FAIL

    absent_ok = evaluator.evaluate(
        observations=[],
        definition={"expected": False},
        context=EvaluationContext(),
    )
    assert absent_ok.result == EvaluationResult.PASS


def test_freshness_evaluator_stale_fails():
    from app.monitoring.connectors.base import ExternalObservation
    from app.monitoring.evaluators import EvaluationContext, get_evaluator
    from app.monitoring.enums import EvaluationResult

    evaluator = get_evaluator("freshness")
    fresh = evaluator.evaluate(
        observations=[
            ExternalObservation(
                external_id="c1",
                observed_at=datetime.now(timezone.utc),
                resource_type="claims",
                payload={},
            )
        ],
        definition={"max_age_hours": 24},
        context=EvaluationContext(now=datetime.now(timezone.utc)),
    )
    assert fresh.result == EvaluationResult.PASS

    stale = evaluator.evaluate(
        observations=[
            ExternalObservation(
                external_id="c1",
                observed_at=datetime.now(timezone.utc) - timedelta(days=4),
                resource_type="claims",
                payload={},
            )
        ],
        definition={"max_age_hours": 24},
        context=EvaluationContext(now=datetime.now(timezone.utc)),
    )
    assert stale.result == EvaluationResult.FAIL


def test_threshold_operators_and_safe_paths():
    from app.monitoring.evaluators import EvaluationContext, get_evaluator
    from app.monitoring.enums import EvaluationResult
    from app.monitoring.exceptions import UnknownField, UnknownOperator

    evaluator = get_evaluator("threshold")

    over = evaluator.evaluate(
        observations=[_observation(amount=150)],
        definition={"operator": "gt", "field_path": "amount", "threshold": 100},
        context=EvaluationContext(),
    )
    assert over.result == EvaluationResult.PASS

    under = evaluator.evaluate(
        observations=[_observation(amount=50)],
        definition={"operator": "gt", "field_path": "amount", "threshold": 100},
        context=EvaluationContext(),
    )
    assert under.result == EvaluationResult.FAIL

    # Unsafe operator
    with pytest.raises(UnknownOperator):
        evaluator.evaluate(
            observations=[_observation()],
            definition={"operator": "always", "field_path": "amount"},
            context=EvaluationContext(),
        )

    # Dunder path is rejected (3.15.55)
    assert evaluator.evaluate(
        observations=[_observation(amount=5)],
        definition={"operator": "gt", "field_path": "__class__.__mro__", "threshold": 1},
        context=EvaluationContext(),
    ).result == EvaluationResult.INCONCLUSIVE


def test_threshold_agreement_field_source():
    from app.monitoring.evaluators import EvaluationContext, get_evaluator
    from app.monitoring.enums import EvaluationResult

    evaluator = get_evaluator("threshold")
    obligation = {"expected_quantity": 1000}

    matched = evaluator.evaluate(
        observations=[_observation(quantity=1200)],
        definition={
            "operator": "gte",
            "field_path": "quantity",
            "value_source": "AGREEMENT_FIELD",
            "setup": {"observed_field": "quantity", "expected_field": "expected_quantity"},
        },
        context=EvaluationContext(obligation=obligation),
    )
    assert matched.result == EvaluationResult.PASS

    # Missing agreement value → INCONCLUSIVE, not FAIL (3.15.25)
    inconclusive = evaluator.evaluate(
        observations=[_observation(quantity=1200)],
        definition={
            "operator": "gte",
            "field_path": "quantity",
            "value_source": "AGREEMENT_FIELD",
            "setup": {"observed_field": "quantity", "expected_field": "nonexistent_field"},
        },
        context=EvaluationContext(obligation=obligation),
    )
    assert inconclusive.result == EvaluationResult.INCONCLUSIVE


def test_count_evaluator_bounds():
    from app.monitoring.evaluators import EvaluationContext, get_evaluator
    from app.monitoring.enums import EvaluationResult

    evaluator = get_evaluator("count")
    ok = evaluator.evaluate(
        observations=[_observation("a"), _observation("b")],
        definition={"min_count": 1, "max_count": 3},
        context=EvaluationContext(),
    )
    assert ok.result == EvaluationResult.PASS

    low = evaluator.evaluate(
        observations=[],
        definition={"min_count": 1},
        context=EvaluationContext(),
    )
    assert low.result == EvaluationResult.FAIL


def test_delivery_evaluator():
    from app.monitoring.evaluators import EvaluationContext, get_evaluator
    from app.monitoring.enums import EvaluationResult

    evaluator = get_evaluator("delivery")
    ok = evaluator.evaluate(
        observations=[_observation("invoice-1")],
        definition={
            "expected": [
                {"resource_type": "claims", "min_count": 1},
                {"resource_type": "disputes", "min_count": 1},
            ]
        },
        context=EvaluationContext(),
    )
    assert ok.result == EvaluationResult.FAIL  # disputes never arrived

    ok2 = evaluator.evaluate(
        observations=[_observation("invoice-1"), _observation("disp-1", _resource_type="disputes")],
        definition={
            "expected": [
                {"resource_type": "claims", "min_count": 1},
                {"resource_type": "disputes", "min_count": 1},
            ]
        },
        context=EvaluationContext(),
    )
    assert ok2.result == EvaluationResult.PASS


def test_unknown_evaluator_kind_raises():
    from app.monitoring.evaluators import get_evaluator
    from app.monitoring.exceptions import UnsupportedEvaluator

    with pytest.raises(UnsupportedEvaluator):
        get_evaluator("gpt")


# --------------------------------------------------------------------------
# Connector registry + render of metadata (3.15.10, 3.15.42)
# --------------------------------------------------------------------------

def test_connector_registry_has_rest_api():
    from app.monitoring.connectors import connector_registry
    from app.monitoring.connectors.base import get_metadata

    assert connector_registry.supports("rest_api")
    meta = get_metadata("rest_api")
    assert meta is not None
    assert "REST_API" in meta.integration_types


# --------------------------------------------------------------------------
# Schedule arithmetic (3.15.31)
# --------------------------------------------------------------------------

def test_compute_next_run_interval_and_cron():
    from app.monitoring.service import compute_next_run

    now = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    next_interval = compute_next_run({"recurrence": "interval", "minutes": 30}, now)
    assert next_interval == now + timedelta(minutes=30)

    next_daily = compute_next_run({"recurrence": "daily", "at": "09:00"}, now)
    assert next_daily.day == 2  # moved to tomorrow since 09:00 already passed

    next_cron = compute_next_run({"recurrence": "cron", "cron": "*/15 * * * *"}, now)
    assert next_cron.minute == 15

    with pytest.raises(ValueError):
        compute_next_run({"recurrence": "cron", "cron": "0 0 30 2 *"}, now)  # never (Feb 30)


def test_cron_expression_subset():
    from app.monitoring.service import CronExpression

    cron = CronExpression("30 0 1 * *")  # 00:30 on the 1st of every month
    assert cron.matches(datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc))
    assert not cron.matches(datetime(2026, 1, 2, 0, 30, tzinfo=timezone.utc))

    stepped = CronExpression("*/15 * * * *")
    assert stepped.matches(datetime(2026, 1, 1, 10, 45, tzinfo=timezone.utc))
    assert not stepped.matches(datetime(2026, 1, 1, 10, 44, tzinfo=timezone.utc))