"""Threshold evaluator (spec 3.15.18, 3.15.21).

Compares an *observed* value against an expectation using a strict operator
allow-list and a restricted field-path resolver (3.15.19, 3.15.55).

Two sources for the expectation:

  * ``threshold`` literal (numeric/string) — classic numeric threshold rules;
  * ``value_source: AGREEMENT_FIELD`` + ``setup.expected_field`` — compare the
    external observation against a value the obligation itself declares
    (e.g. an agreed quantity on the record, not a configured constant).

A missing/unparseable observed field produces INCONCLUSIVE by default
(``on_field_missing: fail`` opts into FAIL) — a source shape mismatch must
never masquerade as verified non-compliance.
"""

from __future__ import annotations

import re

from app.monitoring.connectors.base import ExternalObservation
from app.monitoring.evaluators.base import (
    EvaluationContext,
    EvaluationOutcome,
    Evaluator,
    _missing_field_outcome,
    resolve_path,
)
from app.monitoring.observations import ensure_aware_utc


def _coerce_number(value):
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _satisfies(operator: str, observed, expected) -> bool:
    if operator == "exists":
        return observed is not None
    num_obs = _coerce_number(observed)
    num_exp = _coerce_number(expected)

    if operator in {"gt", "gte", "lt", "lte"}:
        if num_obs is None or num_exp is None:
            raise ValueError("numeric operator requires numeric observed/expected values")
        if operator == "gt":
            return num_obs > num_exp
        if operator == "gte":
            return num_obs >= num_exp
        if operator == "lt":
            return num_obs < num_exp
        return num_obs <= num_exp

    if operator in {"eq", "ne"}:
        base = (num_obs == num_exp) if (num_obs is not None and num_exp is not None) else (observed == expected)
        return not base if operator == "ne" else base

    if operator == "contains":
        if isinstance(observed, list):
            return expected in observed
        if isinstance(observed, str):
            return str(expected) in observed
        raise ValueError("contains requires a list or string observed value")
    if operator == "in":
        if isinstance(expected, list):
            return observed in expected
        raise ValueError("in requires an expected list")
    if operator == "not_in":
        if isinstance(expected, list):
            return observed not in expected
        raise ValueError("not_in requires an expected list")
    if operator == "matches":
        if isinstance(observed, str) and isinstance(expected, str):
            try:
                return re.search(expected, observed) is not None
            except re.error as exc:
                raise ValueError(f"invalid regex: {exc}") from exc
        raise ValueError("matches requires string observed/expected values")
    raise ValueError(f"unsupported operator {operator}")


class ThresholdEvaluator(Evaluator):
    kind = "threshold"

    def evaluate(
        self,
        *,
        observations: list[ExternalObservation],
        definition: dict,
        context: EvaluationContext,
    ) -> EvaluationOutcome:
        operator = definition.get("operator")
        if not operator:
            return EvaluationOutcome.inconclusive(
                reason="Threshold rule missing operator",
                config_invalid=True,
            )
        self._check_operator(operator)

        value_source = definition.get("value_source", "OBSERVATION_FIELD")
        setup = definition.get("setup") or {}
        observed_field = definition.get("field_path") or definition.get("field")

        now = ensure_aware_utc(context.now or __import__("datetime").datetime.now())
        latest = sorted(
            observations,
            key=lambda o: ensure_aware_utc(o.observed_at),
            reverse=True,
        )

        if value_source == "AGREEMENT_FIELD":
            observed_field = setup.get("observed_field") or observed_field
            expected_field = setup.get("expected_field")
            obligation = context.obligation or {}
            try:
                expected = resolve_path(obligation, expected_field) if expected_field else None
            except Exception:
                expected = None
            if expected is None:
                return EvaluationOutcome.inconclusive(
                    reason=f"Agreement expected field '{expected_field}' has no value",
                    **{
                        "observed_count": len(latest),
                        "operator": operator,
                        "value_source": value_source,
                        "observed_field": observed_field,
                    },
                )
        else:
            expected = definition.get("threshold")

        metrics = {
            "observed_count": len(latest),
            "operator": operator,
            "value_source": value_source,
            "observed_field": observed_field,
        }

        if not latest:
            return EvaluationOutcome.inconclusive(
                reason="No observation available to evaluate threshold",
                **metrics,
            )

        observed = None
        if observed_field:
            try:
                observed = resolve_path(latest[0].payload, observed_field)
            except Exception:
                observed = None

        if operator != "exists" and observed is None:
            return _missing_field_outcome(
                f"Field '{observed_field}' missing from observation",
                definition,
            )
        if expected is None and operator != "exists":
            return EvaluationOutcome.inconclusive(
                reason="Conditional has no expected value (threshold or agreement field)",
                **metrics,
            )

        try:
            satisfied = _satisfies(operator, observed, expected)
        except ValueError as exc:
            return EvaluationOutcome.inconclusive(reason=str(exc), **metrics)

        num = _coerce_number(observed)
        metrics["observed_value"] = float(num) if num is not None else observed
        metrics["expected_value"] = expected
        metrics["evaluated_at"] = now.isoformat()
        metrics["latest_observed_at"] = ensure_aware_utc(latest[0].observed_at).isoformat()

        if satisfied:
            return EvaluationOutcome.pass_(**metrics)
        return EvaluationOutcome.fail(
            reason=f"Observed value {observed!r} does not satisfy {operator} expected {expected!r}",
            **metrics,
        )