"""Forecasting & scenario API (spec §3.18.51-54).

POST /forecast/runs                      — start a forecast for a metric
GET  /forecast/runs                      — list forecast runs
GET  /forecast/runs/{run_id}             — one run with predictions
POST /forecast/eligibility               — data-quality gate for a metric
POST /scenarios                          — create + run a what-if scenario
GET  /scenarios                          — list scenarios
GET  /scenarios/{scenario_id}            — one scenario with result
DELETE /scenarios/{scenario_id}          — delete a scenario

Forecasts are deterministic (baseline/trend models) and eligibility-gated:
insufficient snapshot history is rejected with an explicit reason, never
silently forecast (§3.18.8-13, §3.18.35).
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.analytics import MetricSnapshot
from app.models.forecasting import ForecastPrediction, ForecastRun, ScenarioRun
from app.models.user import User
from app.services.forecasting_service import (
    INSUFFICIENT_DATA,
    compute_backtest,
    evaluate_eligibility,
    fit_and_predict,
    run_scenario_simulation,
)

router = APIRouter(prefix="/forecast", tags=["Forecasting"])
scenarios_router = APIRouter(prefix="/scenarios", tags=["Scenarios"])

MIN_HISTORY = 14


class ForecastCreateRequest(BaseModel):
    metric_key: str = Field(..., examples=["lifecycle.renewals_due_30d"])
    horizon_days: int = Field(default=30, ge=1, le=365)
    model_type: str = Field(default="trend", pattern="^(trend|baseline)$")


class ScenarioCreateRequest(BaseModel):
    name: str
    variables: dict = Field(
        ...,
        description=(
            "{variable: {metric_key, transformation: 'scale'|'shift', params: {...}}}"
        ),
    )


def _serialize_run(run: ForecastRun) -> dict:
    return {
        "id": str(run.id),
        "metric_key": run.metric_key,
        "model_type": run.model_type,
        "status": run.status,
        "status_reason": run.status_reason,
        "horizon_days": run.horizon_days,
        "dataset_manifest": run.dataset_manifest,
        "backtest": run.backtest,
        "baseline_backtest": run.baseline_backtest,
        "completed_at": (
            run.completed_at.isoformat() if run.completed_at else None
        ),
        "predictions": [
            {
                "target_date": p.target_date.isoformat(),
                "predicted_value": p.predicted_value,
                "confidence_low": p.confidence_low,
                "confidence_high": p.confidence_high,
            }
            for p in run.predictions
        ],
    }


def _serialize_scenario(scenario: ScenarioRun) -> dict:
    return {
        "id": str(scenario.id),
        "name": scenario.name,
        "status": scenario.status,
        "variables": scenario.variables,
        "result": scenario.result,
        "simulation_version": scenario.simulation_version,
        "completed_at": (
            scenario.completed_at.isoformat() if scenario.completed_at else None
        ),
    }


@router.post("/eligibility")
async def forecast_eligibility(
    body: ForecastCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Report whether the metric has enough clean snapshot history."""
    return await evaluate_eligibility(
        db, org_id=uuid.UUID(str(org_id)), metric_key=body.metric_key
    )


@router.post("/runs")
async def create_forecast(
    body: ForecastCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Run the forecast pipeline for one metric over snapshot history."""
    org_uuid = uuid.UUID(str(org_id))

    run = ForecastRun(
        organization_id=org_uuid,
        metric_key=body.metric_key,
        model_type=body.model_type,
        horizon_days=body.horizon_days,
        status="pending",
        created_by=user.id,
    )
    db.add(run)
    await db.flush()

    history_rows = (
        await db.execute(
            select(MetricSnapshot.snapshot_date, MetricSnapshot.value)
            .where(
                MetricSnapshot.organization_id == org_uuid,
                MetricSnapshot.metric_key == body.metric_key,
                MetricSnapshot.is_missing == 0,
            )
            .order_by(MetricSnapshot.snapshot_date.asc())
        )
    ).all()
    history = [(row[0], float(row[1])) for row in history_rows]

    if len(history) < MIN_HISTORY:
        run.status = "rejected"
        run.status_reason = (
            f"{INSUFFICIENT_DATA}: {len(history)} clean snapshots available, "
            f"{MIN_HISTORY} required"
        )
        run.completed_at = datetime.now(timezone.utc)
        await db.flush()
        # Load the (empty) predictions collection explicitly: async lazy-load
        # in the serializer would fail the request (missing greenlet).
        await db.refresh(run, attribute_names=["predictions"])
        return _serialize_run(run)

    dataset_manifest = {
        "snapshot_count": len(history),
        "first_date": history[0][0].isoformat(),
        "last_date": history[-1][0].isoformat(),
    }

    predictions = fit_and_predict(
        history, horizon_days=body.horizon_days, model_type=body.model_type
    )
    for p in predictions:
        db.add(
            ForecastPrediction(
                run_id=run.id,
                target_date=p["target_date"],
                predicted_value=p["predicted_value"],
                confidence_low=p.get("confidence_low"),
                confidence_high=p.get("confidence_high"),
            )
        )

    backtest = compute_backtest(history, model_type=body.model_type)
    baseline_backtest = compute_backtest(history, model_type="baseline")
    run.dataset_manifest = dataset_manifest
    run.backtest = backtest
    run.baseline_backtest = baseline_backtest
    run.status = "completed"
    run.completed_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(run, attribute_names=["predictions"])

    return _serialize_run(run)


@router.get("/runs")
async def list_forecasts(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    rows = (
        await db.execute(
            select(ForecastRun)
            .where(ForecastRun.organization_id == uuid.UUID(str(org_id)))
            .order_by(ForecastRun.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return [
        {
            "id": str(r.id),
            "metric_key": r.metric_key,
            "model_type": r.model_type,
            "status": r.status,
            "horizon_days": r.horizon_days,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/runs/{run_id}")
async def get_forecast(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    run = await db.get(ForecastRun, uuid.UUID(run_id))
    if run is None or run.organization_id != uuid.UUID(str(org_id)):
        raise HTTPException(status_code=404, detail="Forecast run not found")
    await db.refresh(run, attribute_names=["predictions"])
    return _serialize_run(run)


@scenarios_router.post("")
async def create_scenario(
    body: ScenarioCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Create and immediately simulate a what-if scenario."""
    org_uuid = uuid.UUID(str(org_id))

    scenario = ScenarioRun(
        organization_id=org_uuid,
        name=body.name,
        status="pending",
        variables=body.variables,
        created_by=user.id,
    )
    db.add(scenario)
    await db.flush()

    result = await run_scenario_simulation(db, scenario)
    scenario.result = result
    scenario.status = "completed"
    scenario.completed_at = datetime.now(timezone.utc)
    await db.flush()

    return _serialize_scenario(scenario)


@scenarios_router.get("")
async def list_scenarios(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    rows = (
        await db.execute(
            select(ScenarioRun)
            .where(ScenarioRun.organization_id == uuid.UUID(str(org_id)))
            .order_by(ScenarioRun.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return [_serialize_scenario(s) for s in rows]


@scenarios_router.get("/{scenario_id}")
async def get_scenario(
    scenario_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    scenario = await db.get(ScenarioRun, uuid.UUID(scenario_id))
    if scenario is None or scenario.organization_id != uuid.UUID(str(org_id)):
        raise HTTPException(status_code=404, detail="Scenario not found")
    return _serialize_scenario(scenario)


@scenarios_router.delete("/{scenario_id}")
async def delete_scenario(
    scenario_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    scenario = await db.get(ScenarioRun, uuid.UUID(scenario_id))
    if scenario is None or scenario.organization_id != uuid.UUID(str(org_id)):
        raise HTTPException(status_code=404, detail="Scenario not found")
    await db.delete(scenario)
    return {"status": "deleted"}
