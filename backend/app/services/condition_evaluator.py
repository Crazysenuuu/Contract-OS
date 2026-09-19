"""Declarative conditional-field evaluation (spec §4.2 "Dynamic questionnaire").

Conditions are data, never executable code (spec: schemas must not embed
arbitrary frontend code). The canonical declarative shape is:

    {"field": "personal_data_processed", "operator": "equals", "value": true}

Supported operators: equals, not_equals, in, not_in, contains, gt, gte, lt,
lte, is_set, is_empty, matches (case-insensitive substring).

A legacy shorthand is also accepted for backwards compatibility:

    {"depends_on": {"field": "agreement_party", "value": "..."}}
"""

from __future__ import annotations

import re

_TRUE_STRINGS = {"yes", "true", "1", "on", "y"}


def _normalise(value):
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in {"no", "false", "off", "n", ""}:
            return False
        return value
    return value


def _coerce_list(value) -> list:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if value is None:
        return []
    return [value]


def _is_set(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def evaluate_condition(condition, answers: dict) -> bool:
    """Evaluate one declarative condition against the collected answers."""
    if not condition:
        return True
    if not isinstance(condition, dict):
        return True

    # Legacy depends_on shorthand.
    if "depends_on" in condition and not condition.get("field"):
        condition = condition["depends_on"]

    field = condition.get("field")
    if not field:
        return True

    actual = answers.get(field)
    op = condition.get("operator", "equals")
    expected = condition.get("value")

    if op in {"equals", "eq"}:
        return _normalise(actual) == _normalise(expected)
    if op in {"not_equals", "neq"}:
        return _normalise(actual) != _normalise(expected)
    if op in {"in", "one_of"}:
        return _normalise(actual) in {_normalise(v) for v in _coerce_list(expected)}
    if op in {"not_in"}:
        return _normalise(actual) not in {_normalise(v) for v in _coerce_list(expected)}
    if op in {"contains", "has"}:
        return _normalise(expected) in _coerce_list(actual)
    if op == "gt":
        try:
            return float(actual) > float(expected)
        except (TypeError, ValueError):
            return False
    if op == "gte":
        try:
            return float(actual) >= float(expected)
        except (TypeError, ValueError):
            return False
    if op == "lt":
        try:
            return float(actual) < float(expected)
        except (TypeError, ValueError):
            return False
    if op == "lte":
        try:
            return float(actual) <= float(expected)
        except (TypeError, ValueError):
            return False
    if op == "is_set":
        return _is_set(actual)
    if op == "is_empty":
        return not _is_set(actual)
    if op == "matches":
        return re.search(
            re.escape(str(expected)), str(actual), flags=re.IGNORECASE
        ) is not None
    return True


def filter_visible_questions(questions: list[dict], answers: dict) -> list[dict]:
    """Return only questions whose condition is satisfied by ``answers``.

    Required-field validation must run against the *visible* set so that
    branching branches never force answers the wizard intentionally hid.
    """
    visible = []
    for q in questions:
        if evaluate_condition(q.get("condition"), answers):
            visible.append(q)
    return visible