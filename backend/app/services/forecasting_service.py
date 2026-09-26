"""Deterministic forecasting & scenario engine (spec §3.18).

Pure-arithmetic models over stored metric snapshots:
- ``baseline``: rolling mean of the trailing window (naive model, the
  yardstick every other model must beat — §3.18.13).
- ``trend``: least-squares linear fit over history, extrapolated forward.

Both produce honest confidence bands derived from residual spread. Scenario
simulation transforms the frozen baseline snapshot with declared variable
policies and stores the delta per impact dimension (§3.18.25-26).

No external ML dependencies: reproducible, auditable, and fast enough to run
inline. When real models are warranted later they slot in behind the same
run/prediction records (§3.18.15 model registry policy).
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import MetricSnapshot

INSUFFICIENT_DATA = "insufficient_data"

# Rolling window for the naive baseline model.
BASELINE_WINDOW = 7

# z=1 for the confidence band: ~68% interval from residual spread.
_CONF_Z = 1.0

SCENARIO_VERSION = "1"


def _residual_band(residuals: list[float]) -> float:
    """Spread measure used for the confidence band."""
    if not residuals:
        return 0.0
    mean = sum(residuals) / len(residuals)
    variance = sum((r - mean) ** 2 for r in residuals) / len(residuals)
    return (variance ** 0.5) * _CONF_Z


def _baseline_series(history: list[tuple[date, float]]) -> list[float]:
    """One-step-ahead rolling-mean predictions over the history."""
    out: list[float] = []
    for i in range(len(history)):
        window = [v for _, v in history[max(0, i - BASELINE_WINDOW) : i]]
        out.append(sum(window) / len(window) if window else history[i][1])
    return out


def _trend_line(history: list[tuple[date, float]]) -> tuple[float, float]:
    """Least-squares fit of value against day index. Returns (slope, intercept)."""
    n = len(history)
    xs = list(range(n))
    ys = [v for _, v in history]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return 0.0, mean_y
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    intercept = mean_y - slope * mean_x
    return slope, intercept


def fit_and_predict(
    history: list[tuple[date, float]],
    *,
    horizon_days: int,
    model_type: str,
) -> list[dict]:
    """Produce ``horizon_days`` daily predictions after the last history point.

    Returns [{target_date, predicted_value, confidence_low, confidence_high}].
    """
    if not history:
        return []

    last_date = history[-1][0]

    if model_type == "baseline":
        fitted = _baseline_series(history)
        # Predictions continue the last rolling mean forward.
        window = [v for _, v in history[-BASELINE_WINDOW:]]
        level = sum(window) / len(window)
        band = _residual_band(
            [v - f for (_, v), f in zip(history, fitted) if f is not None]
        )
        return [
            {
                "target_date": last_date + timedelta(days=i + 1),
                "predicted_value": round(level, 4),
                "confidence_low": round(level - band, 4),
                "confidence_high": round(level + band, 4),
            }
            for i in range(horizon_days)
        ]

    # model_type == "trend"
    slope, intercept = _trend_line(history)
    n = len(history)
    fitted = [intercept + slope * x for x in range(n)]
    band = _residual_band([v - f for (_, v), f in zip(history, fitted)])
    return [
        {
            "target_date": last_date + timedelta(days=i + 1),
            "predicted_value": round(intercept + slope * (n + i), 4),
            "confidence_low": round(intercept + slope * (n + i) - band, 4),
            "confidence_high": round(intercept + slope * (n + i) + band, 4),
        }
        for i in range(horizon_days)
    ]


def compute_backtest(
    history: list[tuple[date, float]],
    *,
    model_type: str,
    holdout: int = 5,
) -> dict:
    """Walk-forward backtest on the last ``holdout`` points (§3.18.12).

    Fits on history[:-holdout], predicts each held-out day one step ahead,
    and reports MAE/MAPE plus sample size. With too little data the model
    honestly reports itself unevaluated.
    """
    if len(history) < holdout + BASELINE_WINDOW + 1:
        return {"evaluated": False, "reason": INSUFFICIENT_DATA}

    train = history[:-holdout]
    test = history[-holdout:]

    if model_type == "baseline":
        def _predict(_train, _i):
            window = [v for _, v in _train[-BASELINE_WINDOW:]]
            return sum(window) / len(window)
    else:
        def _predict(_train, _i):
            slope, intercept = _trend_line(_train)
            return intercept + slope * len(_train)

    errors: list[float] = []
    pct_errors: list[float] = []
    rolling = list(train)
    for i, (d, actual) in enumerate(test):
        pred = _predict(rolling, i)
        errors.append(abs(actual - pred))
        if actual != 0:
            pct_errors.append(abs((actual - pred) / actual))
        rolling.append((d, actual))

    mae = sum(errors) / len(errors)
    mape = (sum(pct_errors) / len(pct_errors) * 100) if pct_errors else None
    return {
        "evaluated": True,
        "mae": round(mae, 4),
        "mape": round(mape, 2) if mape is not None else None,
        "sample_size": len(errors),
    }


async def evaluate_eligibility(
    db: AsyncSession, *, org_id, metric_key: str
) -> dict:
    """Data-quality gate before any forecast (§3.18.8-9)."""
    rows = (
        await db.execute(
            select(MetricSnapshot.snapshot_date, MetricSnapshot.value).where(
                MetricSnapshot.organization_id == org_id,
                MetricSnapshot.metric_key == metric_key,
                MetricSnapshot.is_missing == 0,
            )
        )
    ).all()
    clean = [(r[0], float(r[1])) for r in rows]
    missing = (
        await db.scalar(
            select(MetricSnapshot.id)
            .where(
                MetricSnapshot.organization_id == org_id,
                MetricSnapshot.metric_key == metric_key,
                MetricSnapshot.is_missing == 1,
            )
            .limit(1)
        )
    )
    eligible = len(clean) >= 14
    return {
        "metric_key": metric_key,
        "eligible": eligible,
        "clean_snapshots": len(clean),
        "has_missing_snapshots": missing is not None,
        "reason": None if eligible else INSUFFICIENT_DATA,
    }


async def run_scenario_simulation(
    db: AsyncSession, scenario
) -> dict:
    """Execute a scenario against the frozen baseline snapshot (§3.18.23-26).

    The scenario's ``baseline_snapshot`` is captured at creation (or now, on
    first run) from the latest MetricSnapshot per metric; result deltas are
    always relative to that frozen copy.
    """
    from datetime import datetime, timezone

    baseline_snapshot = scenario.baseline_snapshot
    if not baseline_snapshot:
        rows = (
            await db.execute(
                select(MetricSnapshot.metric_key, MetricSnapshot.value)
                .where(
                    MetricSnapshot.organization_id == scenario.organization_id,
                    MetricSnapshot.is_missing == 0,
                )
                .order_by(MetricSnapshot.snapshot_date.desc())
            )
        ).all()
        seen: dict[str, float] = {}
        for key, value in rows:
            if key not in seen and value is not None:
                seen[key] = float(value)
        baseline_snapshot = seen
        scenario.baseline_snapshot = baseline_snapshot

    result_metrics: dict[str, dict] = {}
    for variable, spec in (scenario.variables or {}).items():
        metric_key = spec.get("metric_key")
        if metric_key not in baseline_snapshot:
            continue
        base_value = baseline_snapshot[metric_key]
        transformation = spec.get("transformation", "scale")
        params = spec.get("params", {})
        if transformation == "scale":
            factor = float(params.get("factor", 1.0))
            scenario_value = base_value * factor
        elif transformation == "shift":
            scenario_value = base_value + float(params.get("amount", 0.0))
        else:
            scenario_value = base_value
        delta_pct = (
            round((scenario_value - base_value) / base_value * 100, 2)
            if base_value
            else 0.0
        )
        result_metrics[metric_key] = {
            "baseline": base_value,
            "scenario": round(scenario_value, 4),
            "delta_pct": delta_pct,
            "variable": variable,
        }

    return {
        "metrics": result_metrics,
        "impact_dimensions": sorted(result_metrics.keys()),
        "confidence": "deterministic",
        "simulated_at": datetime.now(timezone.utc).isoformat(),
        "simulation_version": SCENARIO_VERSION,
    }
