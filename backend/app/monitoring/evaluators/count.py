"""Count evaluator.

PASS when the number of observations in the fetch window falls inside the
declared ``min_count``/``max_count`` bounds (e.g. "at least N status
updates per month"). Empty response from an authenticated, healthy source is
a real detected condition (FAIL); a broken source raises upstream and is
INCONCLUSIVE.
"""

from __future__ import annotations

from app.monitoring.connectors.base import ExternalObservation
from app.monitoring.evaluators.base import Evaluator, EvaluationContext, EvaluationOutcome


class CountEvaluator(Evaluator):
    kind = "count"

    def evaluate(
        self,
        *,
        observations: list[ExternalObservation],
        definition: dict,
        context: EvaluationContext,
    ) -> EvaluationOutcome:
        min_count = int(definition.get("min_count") if definition.get("min_count") is not None else 0)
        max_count = definition.get("max_count")
        max_count = int(max_count) if max_count is not None else None
        count = len(observations)

        metrics = {"count": count, "min_count": min_count, "max_count": max_count}
        if count < min_count:
            return EvaluationOutcome.fail(
                reason=f"Expected at least {min_count} observations, received {count}",
                **metrics,
            )
        if max_count is not None and count > max_count:
            return EvaluationOutcome.fail(
                reason=f"Expected at most {max_count} observations, received {count}",
                **metrics,
            )
        return EvaluationOutcome.pass_(**metrics)