"""Freshness evaluator (spec 3.15.17).

PASS when at least one observation is *fresh enough* (within the rule's
``max_age``). A healthy source that returns nothing at all for a delivery
obligation is a real detected condition (FAIL), while an *unreachable*
source raises in the connector and becomes INCONCLUSIVE upstream — so an
empty authenticated response is never confused with a source outage.
"""

from __future__ import annotations

from datetime import timedelta

from app.monitoring.connectors.base import ExternalObservation
from app.monitoring.evaluators.base import Evaluator, EvaluationContext, EvaluationOutcome
from app.monitoring.observations import ensure_aware_utc


def _max_age_seconds(definition: dict) -> float:
    total = 0.0
    total += float(definition.get("max_age_seconds") or 0)
    total += float(definition.get("max_age_minutes") or 0) * 60
    total += float(definition.get("max_age_hours") or 0) * 3600
    total += float(definition.get("max_age_days") or 0) * 86400
    raw = definition.get("max_age")
    if isinstance(raw, dict):
        unit = str(raw.get("unit") or "").upper()
        multiplier = {"MINUTE": 60, "HOUR": 3600, "DAY": 86400}.get(unit, 60)
        total += float(raw.get("amount") or 0) * multiplier
    elif isinstance(raw, (int, float)):
        total += float(raw)
    return total


class FreshnessEvaluator(Evaluator):
    kind = "freshness"

    def evaluate(
        self,
        *,
        observations: list[ExternalObservation],
        definition: dict,
        context: EvaluationContext,
    ) -> EvaluationOutcome:
        max_age_s = _max_age_seconds(definition)
        if max_age_s <= 0:
            return EvaluationOutcome.inconclusive(
                reason="Freshness rule has no positive max_age",
                config_invalid=True,
            )

        now = ensure_aware_utc(context.now or __import__("datetime").datetime.now())
        deadline = now - timedelta(seconds=max_age_s)

        ordered = sorted(observations, key=lambda o: ensure_aware_utc(o.observed_at), reverse=True)
        metrics = {
            "observed_count": len(ordered),
            "max_age_seconds": int(max_age_s),
            "deadline": deadline.isoformat(),
        }

        if not ordered:
            return EvaluationOutcome.fail(
                reason="No observation received from external source",
                **metrics,
            )

        latest = ensure_aware_utc(ordered[0].observed_at)
        metrics["latest_observed_at"] = latest.isoformat()
        metrics["stale_seconds"] = max(0, int((now - latest).total_seconds()))

        if latest >= deadline:
            return EvaluationOutcome.pass_(**metrics)
        return EvaluationOutcome.fail(
            reason=f"Latest observation is older than allowed freshness window",
            **metrics,
        )