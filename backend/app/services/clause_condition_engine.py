"""Safe, data-driven clause condition evaluation (spec 1.8.9).

Conditions are stored as JSON configuration and evaluated with a tiny,
closed operator set. No eval(), no Python logic in the database, and no
agreement-type-specific assumptions in this module.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class ConditionResult:
    matched: bool
    reason: str | None = None


class ClauseConditionEngine:
    """Evaluates structured condition documents against agreement data."""

    def evaluate(self, condition: dict, data: dict[str, Any]) -> ConditionResult:
        if not isinstance(condition, dict):
            return ConditionResult(False, "condition must be an object")

        if "all" in condition:
            results = [self.evaluate(item, data) for item in condition["all"]]
            if all(r.matched for r in results):
                return ConditionResult(True)
            failed = next((r.reason for r in results if not r.matched), None)
            return ConditionResult(False, failed or "Not all conditions matched")

        if "any" in condition:
            results = [self.evaluate(item, data) for item in condition["any"]]
            if any(r.matched for r in results):
                return ConditionResult(True)
            return ConditionResult(False, "No condition matched")

        if "not" in condition:
            inner = self.evaluate(condition["not"], data)
            return ConditionResult(not inner.matched, None if inner.matched else "negated condition matched")

        path = condition.get("path")
        operator = condition.get("operator")
        actual = self._get_value(data, path)

        if operator == "exists":
            expected = condition.get("value", True)
            return ConditionResult((actual is not None) == bool(expected))

        if operator == "equals":
            return ConditionResult(actual == condition.get("value"))

        if operator == "not_equals":
            return ConditionResult(actual != condition.get("value"))

        if operator == "greater_than":
            return ConditionResult(
                actual is not None and _comparable(actual, condition.get("value")) and actual > condition["value"]
            )

        if operator == "less_than":
            return ConditionResult(
                actual is not None and _comparable(actual, condition.get("value")) and actual < condition["value"]
            )

        if operator == "in":
            allowed = condition.get("value") or []
            return ConditionResult(actual in allowed)

        if operator == "is_true":
            return ConditionResult(bool(actual))

        if operator == "is_false":
            return ConditionResult(not bool(actual))

        raise ValueError(f"Unsupported clause condition operator: {operator}")

    def _get_value(self, data: dict[str, Any], path: str | None) -> Any:
        if not path:
            return None
        current: Any = data
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
            if current is None:
                return None
        return current


def _comparable(actual: Any, value: Any) -> bool:
    try:
        return isinstance(actual, (int, float)) and isinstance(value, (int, float))
    except TypeError:
        return False
