"""Delivery evaluator.

Verifies that each declared delivery arrived: for every entry in
``expected`` (a dict with ``resource_type`` and optional ``min_count`` and
``payload.field``), the fetch must contain at least ``min_count`` matching
observations. Failure to receive an expected delivery from a healthy source
is a real detected condition (FAIL) — never INCONCLUSIVE.
"""

from __future__ import annotations

from app.monitoring.connectors.base import ExternalObservation
from app.monitoring.evaluators.base import (
    EvaluationContext,
    EvaluationOutcome,
    Evaluator,
    resolve_path,
)
from app.monitoring.observations import ensure_aware_utc


class DeliveryEvaluator(Evaluator):
    kind = "delivery"

    def evaluate(
        self,
        *,
        observations: list[ExternalObservation],
        definition: dict,
        context: EvaluationContext,
    ) -> EvaluationOutcome:
        expected = definition.get("expected") or []
        if not isinstance(expected, list) or not expected:
            return EvaluationOutcome.inconclusive(
                reason="Delivery rule missing 'expected' list",
                config_invalid=True,
            )

        all_matched = True
        checks = []
        for entry in expected:
            resource_type = entry.get("resource_type") or entry.get("resource")
            min_count = int(entry.get("min_count") if entry.get("min_count") is not None else 1)
            payload_field = entry.get("payload_field")
            expected_value = entry.get("expected_payload_value")

            matching = [
                o for o in observations
                if (not resource_type or o.resource_type == resource_type)
            ]
            count = len(matching)
            ok = count >= min_count

            # Optional payload assertion on the latest matching item.
            field_ok = True
            if ok and payload_field and matching:
                latest = sorted(matching, key=lambda o: ensure_aware_utc(o.observed_at), reverse=True)[0]
                try:
                    field_ok = resolve_path(latest.payload, payload_field) == expected_value
                except Exception:
                    field_ok = False
            ok = ok and field_ok

            checks.append({
                "resource_type": resource_type,
                "min_count": min_count,
                "received_count": count,
                "payload_field": payload_field,
                "payload_matched": field_ok,
            })
            all_matched = all_matched and ok

        metrics = {"checks": checks, "total_observations": len(observations)}
        if all_matched:
            return EvaluationOutcome.pass_(**metrics)
        failed = [c for c in checks if not c.get("received_count", 0) >= c.get("min_count", 1) or not c.get("payload_matched", True)]
        return EvaluationOutcome.fail(
            reason=f"Delivery expectation not met: {failed}",
            **metrics,
        )