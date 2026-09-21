"""Condition engine for the workflow orchestrator (spec §2.11.14/2.11.15).

Transitions and steps carry declarative condition trees. This module evaluates
them against a workflow instance's ``context`` — never through ``eval`` or
arbitrary import paths. The operator set is fixed and code-backed: new
operators are added here, not in workflow configuration.

Supported tree shapes::

    {"operator": "EQUALS", "field": "agreement.status", "value": "executed"}
    {"operator": "IN", "field": "party.type", "value": ["buyer", "lessee"]}
    {"operator": "EXISTS", "field": "amendment.id"}
    {"operator": "GREATER_THAN", "field": "amount.exclusive", "value": 5000}
    {"operator": "AND", "children": [{...}, {...}]}
    {"operator": "OR", "children": [{...}]}
    {"operator": "NOT", "child": {...}}

A ``None`` file is treated as a passing condition (unconditional transition).
"""

from __future__ import annotations

from typing import Any

Operators = frozenset(
    {
        "EQUALS",
        "NOT_EQUALS",
        "IN",
        "NOT_IN",
        "EXISTS",
        "MISSING",
        "GREATER_THAN",
        "GREATER_THAN_OR_EQUAL",
        "LESS_THAN",
        "LESS_THAN_OR_EQUAL",
        "AND",
        "OR",
        "NOT",
        "TRUE",
        "FALSE",
    }
)


def resolve_path(context: dict[str, Any], field: str | None) -> Any:
    """Traverse a dotted path through a nested context dict.

    Returns ``None`` for a missing path so EXISTS/MISSING can distinguish by
    walking with a sentinel.
    """

    if not field:
        return None
    current: Any = context
    for part in field.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


_MISSING = object()


def evaluate_condition(condition: dict[str, Any] | None, context: dict[str, Any]) -> bool:
    """Evaluate a declarative condition tree against ``context``.

    Raises ``ValueError`` for unknown operators or malformed compound nodes so
    configuration mistakes surface loudly at validation time.
    """

    if condition is None:
        return True

    operator = condition.get("operator")
    if operator not in Operators:
        raise ValueError(f"Unknown condition operator: {operator!r}")

    if operator == "TRUE":
        return True
    if operator == "FALSE":
        return False

    if operator in ("AND", "OR"):
        children = condition.get("children") or []
        results = [evaluate_condition(child, context) for child in children]
        return all(results) if operator == "AND" else any(results)

    if operator == "NOT":
        return not evaluate_condition(condition.get("child"), context)

    field = condition.get("field")
    expected = condition.get("value")
    actual = resolve_path(context, field)

    if operator == "EXISTS":
        sentinel = _MISSING
        current: Any = context
        if field:
            for part in field.split("."):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    sentinel = _MISSING
                    break
            else:
                sentinel = current
        return sentinel is not _MISSING and sentinel is not None

    if operator == "MISSING":
        sentinel = _MISSING
        current: Any = context
        if field:
            for part in field.split("."):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    sentinel = _MISSING
                    break
            else:
                sentinel = current
        return sentinel is _MISSING or sentinel is None

    if operator == "EQUALS":
        return _coerce(actual) == _coerce(expected)
    if operator == "NOT_EQUALS":
        return _coerce(actual) != _coerce(expected)
    if operator == "IN":
        return _coerce(actual) in list(expected or [])
    if operator == "NOT_IN":
        return _coerce(actual) not in list(expected or [])

    if operator in (
        "GREATER_THAN",
        "GREATER_THAN_OR_EQUAL",
        "LESS_THAN",
        "LESS_THAN_OR_EQUAL",
    ):
        try:
            if actual is None or expected is None:
                return False
            left = float(actual)
            right = float(expected)
        except (TypeError, ValueError):
            return False
        if operator == "GREATER_THAN":
            return left > right
        if operator == "GREATER_THAN_OR_EQUAL":
            return left >= right
        if operator == "LESS_THAN":
            return left < right
        return left <= right

    raise ValueError(f"Unhandled condition operator: {operator!r}")


def _coerce(value: Any) -> Any:
    """Normalise values for equality comparison (enum members vs strings)."""

    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return value
    if hasattr(value, "value"):
        return value.value
    return value