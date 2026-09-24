"""Existence evaluator (spec 3.15.16).

PASS when the external resource exists (or — with ``expected: false`` —
verifiably does not exist). ``expected`` flips the assertion so "absence
proves compliance" obligations work the same way.
"""

from __future__ import annotations

from app.monitoring.connectors.base import ExternalObservation
from app.monitoring.evaluators.base import Evaluator, EvaluationContext, EvaluationOutcome


class ExistenceEvaluator(Evaluator):
    kind = "existence"

    def evaluate(
        self,
        *,
        observations: list[ExternalObservation],
        definition: dict,
        context: EvaluationContext,
    ) -> EvaluationOutcome:
        expected = bool(definition.get("expected", True))
        present = len(observations) > 0
        metrics = {"observed_count": len(observations), "expected": expected}
        if present == expected:
            return EvaluationOutcome.pass_(**metrics)
        return EvaluationOutcome.fail(
            reason=(
                f"Expected resource to {'exist' if expected else 'be absent'} "
                f"but observed {'present' if present else 'absent'}"
            ),
            **metrics,
        )