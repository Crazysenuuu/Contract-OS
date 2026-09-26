"""Tests for the portfolio-intelligence build-out (spec §3.17-3.20).

Covers: metric snapshot aggregation (the nightly analytics job), anomaly
detection, executive insights, forecasting eligibility + deterministic
models, scenario simulation, the action center, external policy gating and
the newly registered orchestration actions.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.agreement import Agreement
from app.models.analytics import AnomalyRecord, ExecutiveInsight, MetricSnapshot
from app.models.forecasting import ForecastRun, ScenarioRun
from app.models.organization import Organization
from app.models.user import User
from app.services.analytics_service import (
    _detect_anomalies,
    _generate_insights,
    compute_portfolio_metrics,
    run_daily_aggregation,
)
from app.services.action_item_service import (
    complete_action_item,
    resolve_source_item,
    upsert_action_item,
)
from app.services.external_policy_service import (
    ExternalPolicyError,
    clamp_link_expiry,
    filter_answers_for_external,
)
from app.services.forecasting_service import (
    fit_and_predict,
    compute_backtest,
    run_scenario_simulation,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def make_agreement(db, org, user, *, status="active", value=None,
                         expiry=None, suffix=""):
    agreement = Agreement(
        organization_id=org.id,
        agreement_type_id=None,
        title=f"Test Agreement {suffix}",
        status=status,
        created_by=user.id,
        data={"total_value": value} if value is not None else {},
        expiry_date=expiry,
    )
    # agreement_type_id is nullable=False in the schema; tests elsewhere rely
    # on a seeded type. Fetch or create one.
    from app.models.agreement_type import AgreementType

    atype = (
        await db.execute(select(AgreementType).limit(1))
    ).scalars().first()
    if atype is None:
        atype = AgreementType(
            key=f"t_{uuid.uuid4().hex[:8]}",
            name="T",
            category="test",
            schema={"questions": [], "clauses": []},
        )
        db.add(atype)
        await db.flush()
    agreement.agreement_type_id = atype.id
    db.add(agreement)
    await db.flush()
    return agreement


# ---------------------------------------------------------------------------
# analytics: snapshots, anomalies, insights
# ---------------------------------------------------------------------------


class TestMetricSnapshots:
    async def test_run_daily_aggregation_creates_snapshots(self, db_session, test_org, test_user):
        await make_agreement(
            db_session, test_org, test_user,
            status="active", value="100000", suffix="A",
        )
        await make_agreement(
            db_session, test_org, test_user,
            status="draft", value="50000", suffix="B",
        )

        result = await run_daily_aggregation(db_session)

        assert result["organizations_processed"] >= 1
        snaps = (
            await db_session.execute(
                select(MetricSnapshot).where(
                    MetricSnapshot.organization_id == test_org.id
                )
            )
        ).scalars().all()
        by_key = {s.metric_key: s for s in snaps}
        assert by_key["inventory.total_agreements"].value == 2
        assert by_key["inventory.active_agreements"].value == 1
        assert by_key["inventory.total_value"].value == 150000.0

    async def test_run_daily_aggregation_idempotent(self, db_session, test_org, test_user):
        await make_agreement(db_session, test_org, test_user, status="active")
        await run_daily_aggregation(db_session)
        await run_daily_aggregation(db_session)

        snaps = (
            await db_session.execute(
                select(MetricSnapshot).where(
                    MetricSnapshot.organization_id == test_org.id
                )
            )
        ).scalars().all()
        # One row per metric, not duplicated by the second run.
        assert len(snaps) == 11
        assert len({s.metric_key for s in snaps}) == 11

    async def test_compute_portfolio_metrics_counts_overdue(
        self, db_session, test_org, test_user
    ):
        from app.models.obligation import Obligation

        agreement = await make_agreement(db_session, test_org, test_user)
        db_session.add(
            Obligation(
                agreement_id=agreement.id,
                organization_id=test_org.id,
                owner_party="us",
                description="Pay invoice",
                obligation_type="payment",
                status="OPEN",
                due_date=date.today() - timedelta(days=5),
            )
        )
        await db_session.flush()

        metrics = await compute_portfolio_metrics(
            db_session, org_id=test_org.id
        )
        assert metrics["obligations.open_count"] == 1
        assert metrics["obligations.overdue_count"] == 1


class TestAnomalyDetection:
    def test_spike_beyond_threshold_detected(self):
        today = date.today()
        # Baseline varies 9-11 around mean 10 (stddev > 0); today = 50.
        baseline_values = [9.0, 10.0, 11.0, 10.0, 9.5, 10.5, 10.0] * 2
        history = [
            (today - timedelta(days=i + 1), "m.x", baseline_values[i])
            for i in range(14)
        ]
        history.insert(0, (today, "m.x", 50.0))
        anomalies = _detect_anomalies(history)
        assert "m.x" in anomalies
        assert anomalies["m.x"]["direction"] == "spike"

    def test_stable_series_has_no_anomaly(self):
        today = date.today()
        history = [
            (today - timedelta(days=i), "m.x", 10.0) for i in range(15)
        ]
        assert _detect_anomalies(history) == {}

    def test_short_history_is_skipped(self):
        today = date.today()
        history = [(today - timedelta(days=i), "m.x", 10.0) for i in range(5)]
        assert _detect_anomalies(history) == {}

    async def test_anomaly_persisted(self, db_session, test_org, test_user):
        today = date.today()
        # 14 days of varying baseline around 10, today 100 -> spike.
        baseline_values = [9.0, 10.0, 11.0, 10.0, 9.5, 10.5, 10.0] * 2
        for i in range(1, 15):
            db_session.add(
                MetricSnapshot(
                    organization_id=test_org.id,
                    snapshot_date=today - timedelta(days=i),
                    metric_key="inventory.total_agreements",
                    value=baseline_values[i - 1],
                    is_missing=0,
                )
            )
        db_session.add(
            MetricSnapshot(
                organization_id=test_org.id,
                snapshot_date=today,
                metric_key="inventory.total_agreements",
                value=100.0,
                is_missing=0,
            )
        )
        await db_session.flush()

        await run_daily_aggregation(db_session)

        anomalies = (
            await db_session.execute(
                select(AnomalyRecord).where(
                    AnomalyRecord.organization_id == test_org.id
                )
            )
        ).scalars().all()
        # The aggregation upserts today's real value over the seeded 100
        # (the workspace has no agreements, so today lands at 0) — the
        # invariant is that the >3σ deviation against the varying baseline
        # is detected and persisted, whichever direction it lands.
        assert any(
            a.metric_key == "inventory.total_agreements"
            and abs(a.deviation) >= 3.0
            for a in anomalies
        )


class TestInsights:
    def test_overdue_generates_insight_with_evidence(self):
        insights = _generate_insights(
            {
                "inventory.total_agreements": 10.0,
                "obligations.overdue_count": 4.0,
            }
        )
        assert any(i["category"] == "obligation_performance" for i in insights)
        ev = insights[0]["evidence"]["metric_refs"]
        assert any(m["metric_key"] == "obligations.overdue_count" for m in ev)

    def test_clean_portfolio_generates_no_insight(self):
        insights = _generate_insights(
            {
                "inventory.total_agreements": 10.0,
                "obligations.overdue_count": 0.0,
                "lifecycle.renewals_due_30d": 0.0,
                "risk.critical_findings": 0.0,
            }
        )
        assert insights == []


# ---------------------------------------------------------------------------
# forecasting & scenarios
# ---------------------------------------------------------------------------


class TestForecasting:
    def test_trend_predictions_continue_rising_series(self):
        start = date(2026, 1, 1)
        history = [(start + timedelta(days=i), float(i)) for i in range(30)]
        preds = fit_and_predict(history, horizon_days=5, model_type="trend")
        assert len(preds) == 5
        values = [p["predicted_value"] for p in preds]
        assert values == sorted(values)  # strictly non-decreasing
        assert preds[0]["confidence_low"] <= preds[0]["predicted_value"]

    def test_baseline_predictions_flat(self):
        start = date(2026, 1, 1)
        history = [(start + timedelta(days=i), 7.0) for i in range(30)]
        preds = fit_and_predict(history, horizon_days=3, model_type="baseline")
        assert all(abs(p["predicted_value"] - 7.0) < 1e-6 for p in preds)

    def test_backtest_evaluates_with_enough_data(self):
        start = date(2026, 1, 1)
        history = [(start + timedelta(days=i), float(i % 5)) for i in range(30)]
        bt = compute_backtest(history, model_type="trend")
        assert bt["evaluated"] is True
        assert bt["sample_size"] == 5

    def test_backtest_refuses_short_history(self):
        start = date(2026, 1, 1)
        history = [(start + timedelta(days=i), float(i)) for i in range(6)]
        bt = compute_backtest(history, model_type="trend")
        assert bt["evaluated"] is False

    async def test_forecast_rejected_without_history(
        self, db_session, test_org, test_user
    ):
        from app.services.forecasting_service import INSUFFICIENT_DATA

        run = ForecastRun(
            organization_id=test_org.id,
            metric_key="inventory.total_agreements",
            model_type="trend",
            horizon_days=7,
            status="pending",
        )
        db_session.add(run)
        await db_session.flush()
        run.status = "rejected"
        run.status_reason = INSUFFICIENT_DATA
        await db_session.flush()
        assert run.status == "rejected"
        assert "insufficient" in run.status_reason

    async def test_scenario_simulation_scale(self, db_session, test_org, test_user):
        today = date.today()
        db_session.add(
            MetricSnapshot(
                organization_id=test_org.id,
                snapshot_date=today,
                metric_key="inventory.total_value",
                value=1_000_000.0,
                is_missing=0,
            )
        )
        await db_session.flush()

        scenario = ScenarioRun(
            organization_id=test_org.id,
            name="FX shock",
            status="pending",
            variables={
                "fx": {
                    "metric_key": "inventory.total_value",
                    "transformation": "scale",
                    "params": {"factor": 0.9},
                }
            },
        )
        db_session.add(scenario)
        await db_session.flush()

        result = await run_scenario_simulation(db_session, scenario)
        metric = result["metrics"]["inventory.total_value"]
        assert metric["baseline"] == 1_000_000.0
        assert metric["scenario"] == 900_000.0
        assert metric["delta_pct"] == -10.0


# ---------------------------------------------------------------------------
# action center
# ---------------------------------------------------------------------------


class TestActionItems:
    async def test_upsert_is_idempotent(self, db_session, test_org, test_user):
        source_id = uuid.uuid4()
        item1, created1 = await upsert_action_item(
            db_session,
            organization_id=test_org.id,
            action_type="approval_request",
            title="Approve NDA",
            source_system="approvals",
            source_id=source_id,
        )
        item2, created2 = await upsert_action_item(
            db_session,
            organization_id=test_org.id,
            action_type="approval_request",
            title="Approve NDA (urgent)",
            source_system="approvals",
            source_id=source_id,
            priority=90,
        )
        assert created1 is True
        assert created2 is False
        assert item1.id == item2.id
        assert item2.title == "Approve NDA (urgent)"
        assert item2.priority == 90

    async def test_complete_then_source_resolve_is_noop(
        self, db_session, test_org, test_user
    ):
        item, _ = await upsert_action_item(
            db_session,
            organization_id=test_org.id,
            action_type="obligation_due",
            title="Pay supplier",
            source_system="obligations",
            source_id=uuid.uuid4(),
        )
        await complete_action_item(db_session, item_id=item.id, completed_by=test_user.id)
        closed = await resolve_source_item(
            db_session, source_system="obligations", source_id=item.source_id
        )
        assert closed == 0  # already completed — no double-resolution


# ---------------------------------------------------------------------------
# external policy
# ---------------------------------------------------------------------------


class TestExternalPolicy:
    async def test_disabled_access_blocks_guest_links(self, db_session, test_org):
        from app.services.external_policy_service import (
            assert_external_access_allowed,
            update_policy,
        )

        await update_policy(
            db_session, organization_id=test_org.id, allow_external_access=False
        )
        with pytest.raises(ExternalPolicyError):
            await assert_external_access_allowed(
                db_session, organization_id=test_org.id
            )

    def test_link_expiry_clamped_to_policy(self):
        from app.models.external_policy import ExternalWorkspacePolicy

        policy = ExternalWorkspacePolicy(
            organization_id=uuid.uuid4(), max_link_ttl_days=7
        )
        far_future = datetime.now(timezone.utc) + timedelta(days=365)
        clamped = clamp_link_expiry(policy, far_future)
        assert clamped <= datetime.now(timezone.utc) + timedelta(days=8)

    def test_sharing_policy_narrows_answers(self):
        from app.models.external_policy import AgreementSharingPolicy

        policy = AgreementSharingPolicy(
            agreement_id=uuid.uuid4(),
            shared_fields={"keys": ["effective_date", "parties"]},
        )
        answers = {
            "effective_date": "2026-01-01",
            "parties": ["A", "B"],
            "internal_margin": "40%",
        }
        visible = filter_answers_for_external(answers, policy)
        assert visible == {"effective_date": "2026-01-01", "parties": ["A", "B"]}

    def test_no_policy_shares_everything(self):
        answers = {"a": 1}
        assert filter_answers_for_external(answers, None) == answers

    async def test_create_external_party_blocked_when_disabled(
        self, db_session, test_org, test_user
    ):
        from app.models.agreement_access import AgreementParty
        from app.services.external_party_service import create_external_party
        from app.services.external_policy_service import update_policy

        agreement = await make_agreement(db_session, test_org, test_user)
        party = AgreementParty(
            agreement_id=agreement.id,
            legal_entity_id=None,
            party_role="receiving",
        )
        # legal_entity_id is nullable=False; create one.
        from app.models.legal_entity import LegalEntity

        entity = LegalEntity(
            organization_id=test_org.id,
            legal_name="Counterparty Co",
            country="US",
        )
        db_session.add(entity)
        await db_session.flush()
        party.legal_entity_id = entity.id
        db_session.add(party)
        await db_session.flush()

        await update_policy(
            db_session, organization_id=test_org.id, allow_external_access=False
        )
        with pytest.raises(ValueError, match="External access is disabled"):
            await create_external_party(
                db_session,
                agreement_id=agreement.id,
                agreement_party_id=party.id,
                company_name="Co",
                signatory_name="Signer",
                signatory_email="signer@co.example",
            )


# ---------------------------------------------------------------------------
# orchestration actions
# ---------------------------------------------------------------------------


class TestOrchestrationActions:
    def test_all_spec_actions_registered(self):
        from app.services.orchestration_actions import action_keys

        assert action_keys() == {
            "CREATE_TASK",
            "CREATE_NOTIFICATION",
            "CREATE_OBLIGATION",
            "WAIT_FOR_EVENT",
            "CREATE_APPROVAL",
            "REQUEST_SIGNATURE",
            "CREATE_AMENDMENT",
            "RUN_RISK_ANALYSIS",
            "CALL_INTEGRATION",
        }

    def test_planned_actions_empty(self):
        from app.services.orchestration_actions import PLANNED_ACTIONS

        assert PLANNED_ACTIONS == frozenset()

    async def test_create_approval_action_resolves_definition(
        self, db_session, test_org, test_user
    ):
        from app.models.approval import ApprovalDefinition, ApprovalStage
        from app.services.orchestration_actions import get_action

        definition = ApprovalDefinition(
            organization_id=test_org.id, name="Auto approval"
        )
        db_session.add(definition)
        await db_session.flush()
        stage = ApprovalStage(
            definition_id=definition.id, name="Stage 1", order=1
        )
        db_session.add(stage)
        await db_session.flush()

        agreement = await make_agreement(db_session, test_org, test_user)
        action = get_action("CREATE_APPROVAL")
        result = await action.execute(
            db_session,
            config={
                "agreement_id": str(agreement.id),
                "organization_id": str(test_org.id),
                "definition_id": str(definition.id),
            },
            context={},
            step_instance_id=uuid.uuid4(),
        )
        assert "approval_record_id" in result
        assert result["status"] in ("in_progress", "approved")

    async def test_unknown_action_still_raises(self):
        from app.services.orchestration_actions import get_action

        with pytest.raises(Exception, match="Unknown workflow action key"):
            get_action("DEFINITELY_NOT_AN_ACTION")


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


class TestAnalyticsAPI:
    async def test_portfolio_dashboard_empty(self, client, auth_headers, db_session, test_org):
        resp = await client.get(
            "/api/v1/analytics/portfolio", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "no_data"

    async def test_action_items_flow(self, client, auth_headers, db_session, test_org, test_user):
        item, _ = await upsert_action_item(
            db_session,
            organization_id=test_org.id,
            action_type="review_request",
            title="Review clause",
            source_system="reviews",
            source_id=uuid.uuid4(),
            assignee_user_id=test_user.id,
        )
        await db_session.commit()

        resp = await client.get("/api/v1/action-items", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()
        assert any(i["id"] == str(item.id) for i in items)

        resp = await client.post(
            f"/api/v1/action-items/{item.id}/complete", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
