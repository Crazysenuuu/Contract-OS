"""Unit tests for the orchestrator condition engine (spec §2.11.14/2.11.15)."""

from __future__ import annotations

import enum

import pytest

from app.services.orchestration_conditions import evaluate_condition, resolve_path


class _SampleEnum(str, enum.Enum):
    EXECUTED = "executed"
    DRAFT = "draft"


def test_none_condition_passes():
    assert evaluate_condition(None, {}) is True


@pytest.mark.parametrize(
    "condition,context,expected",
    [
        (
            {"operator": "EQUALS", "field": "status", "value": "executed"},
            {"status": "executed"},
            True,
        ),
        (
            {"operator": "EQUALS", "field": "status", "value": "executed"},
            {"status": "draft"},
            False,
        ),
        (
            {"operator": "EQUALS", "field": "nested.value", "value": 42},
            {"nested": {"value": 42}},
            True,
        ),
        ({"operator": "NOT_EQUALS", "field": "x", "value": "a"}, {"x": "b"}, True),
        ({"operator": "IN", "field": "party_type", "value": ["buyer", "lessee"]}, {"party_type": "buyer"}, True),
        ({"operator": "IN", "field": "party_type", "value": ["buyer"]}, {"party_type": "seller"}, False),
        ({"operator": "NOT_IN", "field": "party_type", "value": ["buyer"]}, {"party_type": "seller"}, True),
        ({"operator": "EXISTS", "field": "amendment.id"}, {"amendment": {"id": "x"}}, True),
        ({"operator": "EXISTS", "field": "amendment.id"}, {}, False),
        ({"operator": "EXISTS", "field": "amendment.id"}, {"amendment": {"id": None}}, False),
        ({"operator": "MISSING", "field": "amendment.id"}, {}, True),
        ({"operator": "MISSING", "field": "amendment.id"}, {"amendment": {"id": "x"}}, False),
        ({"operator": "GREATER_THAN", "field": "amount", "value": 100}, {"amount": 101}, True),
        ({"operator": "GREATER_THAN", "field": "amount", "value": 100}, {"amount": 99}, False),
        ({"operator": "GREATER_THAN_OR_EQUAL", "field": "amount", "value": 100}, {"amount": 100}, True),
        ({"operator": "LESS_THAN", "field": "amount", "value": 100}, {"amount": 99}, True),
        ({"operator": "LESS_THAN_OR_EQUAL", "field": "amount", "value": 100}, {"amount": 100}, True),
        ({"operator": "GREATER_THAN", "field": "amount", "value": "abc"}, {"amount": 101}, False),
        ({"operator": "TRUE"}, {}, True),
        ({"operator": "FALSE"}, {}, False),
        (
            {"operator": "AND", "children": [
                {"operator": "EQUALS", "field": "a", "value": 1},
                {"operator": "EQUALS", "field": "b", "value": 2},
            ]},
            {"a": 1, "b": 2},
            True,
        ),
        (
            {"operator": "AND", "children": [
                {"operator": "EQUALS", "field": "a", "value": 1},
                {"operator": "EQUALS", "field": "b", "value": 3},
            ]},
            {"a": 1, "b": 2},
            False,
        ),
        (
            {"operator": "OR", "children": [
                {"operator": "EQUALS", "field": "a", "value": 1},
                {"operator": "EQUALS", "field": "b", "value": 2},
            ]},
            {"a": 0, "b": 2},
            True,
        ),
        (
            {"operator": "NOT", "child": {"operator": "EQUALS", "field": "a", "value": 1}},
            {"a": 2},
            True,
        ),
    ],
)
def test_evaluate_condition(condition, context, expected):
    assert evaluate_condition(condition, context) is expected


def test_enum_values_coerce_for_equality():
    condition = {"operator": "EQUALS", "field": "status", "value": "executed"}
    assert evaluate_condition(condition, {"status": _SampleEnum.EXECUTED}) is True


def test_unknown_operator_raises():
    with pytest.raises(ValueError):
        evaluate_condition({"operator": "LIKE", "field": "x", "value": "a"}, {})


def test_resolve_path_nested_and_missing():
    context = {"agreement": {"status": "executed", "party": {"type": "buyer"}}}
    assert resolve_path(context, "agreement.status") == "executed"
    assert resolve_path(context, "agreement.party.type") == "buyer"
    assert resolve_path(context, "agreement.missing.path") is None
    assert resolve_path(context, None) is None
    assert resolve_path(context, "") is None


def test_depth_first_with_lists_returns_default_missing():
    # Lists are not traversed; a path through a list is treated as missing.
    assert resolve_path({"items": [{"x": 1}]}, "items.x") is None