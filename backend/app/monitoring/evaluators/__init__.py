"""Evaluator registry (spec 3.15.9).

``get_evaluator`` raises ``UnsupportedEvaluator`` for a kind with no
registered evaluator so a misconfigured rule never passes silently.
"""

from __future__ import annotations

from app.monitoring.evaluators.base import (
    ALLOWED_OPERATORS,
    EvaluationContext,
    EvaluationOutcome,
    Evaluator,
    resolve_path,
)
from app.monitoring.evaluators.count import CountEvaluator
from app.monitoring.evaluators.delivery import DeliveryEvaluator
from app.monitoring.evaluators.existence import ExistenceEvaluator
from app.monitoring.evaluators.freshness import FreshnessEvaluator
from app.monitoring.evaluators.threshold import ThresholdEvaluator
from app.monitoring.exceptions import UnsupportedEvaluator

_EVALUATORS: dict[str, Evaluator] = {}


def _register(evaluator: Evaluator) -> None:
    _EVALUATORS[evaluator.kind] = evaluator


_register(ExistenceEvaluator())
_register(FreshnessEvaluator())
_register(ThresholdEvaluator())
_register(CountEvaluator())
_register(DeliveryEvaluator())


def get_evaluator(kind: str) -> Evaluator:
    evaluator = _EVALUATORS.get(kind)
    if evaluator is None:
        raise UnsupportedEvaluator(f"Unsupported evaluator kind: {kind}")
    return evaluator


def supported_evaluators() -> list[str]:
    return sorted(_EVALUATORS)


__all__ = [
    "ALLOWED_OPERATORS",
    "EvaluationContext",
    "EvaluationOutcome",
    "Evaluator",
    "get_evaluator",
    "resolve_path",
    "supported_evaluators",
]