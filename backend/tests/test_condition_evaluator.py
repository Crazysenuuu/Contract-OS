"""Tests for the dynamic questionnaire condition engine (spec §4.2).

Validates declarative `condition` evaluation (equals / in / numeric / is_set
/ matches) and that both validation paths (legacy validate_answers and the
formal schema validator) only require fields whose branch is visible.
"""
from app.services.condition_evaluator import (
    evaluate_condition,
    filter_visible_questions,
)
from app.services.schema_validation_service import validate_agreement_data
from app.services.agreement_renderer import validate_answers


def _q(key, required=True, **kw):
    return {
        "id": key,
        "key": key,
        "label": key,
        "type": "text",
        "required": required,
        **kw,
    }


class TestConditionEvaluator:
    def test_equals_string(self):
        assert evaluate_condition(
            {"field": "personal_data_processed", "operator": "equals", "value": "Yes"},
            {"personal_data_processed": "Yes"},
        )
        assert not evaluate_condition(
            {"field": "personal_data_processed", "operator": "equals", "value": "Yes"},
            {"personal_data_processed": "No"},
        )

    def test_equals_boolean_vs_checkbox_false(self):
        # Unanswered select renders as "" which normalizes to False; only a
        # literal "Yes"/True satisfies the branch.
        assert not evaluate_condition(
            {"field": "personal_data_processed", "operator": "equals", "value": "Yes"},
            {"personal_data_processed": ""},
        )
        assert evaluate_condition(
            {"field": "enabled", "operator": "equals", "value": True},
            {"enabled": True},
        )
        assert not evaluate_condition(
            {"field": "enabled", "operator": "equals", "value": True},
            {"enabled": "no"},
        )

    def test_in_operator(self):
        cond = {"field": "currency", "operator": "in", "value": ["LKR", "USD"]}
        assert evaluate_condition(cond, {"currency": "USD"})
        assert not evaluate_condition(cond, {"currency": "EUR"})

    def test_numeric_operators(self):
        assert evaluate_condition(
            {"field": "amount", "operator": "gte", "value": 1000},
            {"amount": 1500},
        )
        assert not evaluate_condition(
            {"field": "amount", "operator": "gte", "value": 1000},
            {"amount": 500},
        )

    def test_is_set_and_matches(self):
        assert evaluate_condition(
            {"field": "note", "operator": "is_set"}, {"note": "something"}
        )
        assert not evaluate_condition(
            {"field": "note", "operator": "is_set"}, {"note": ""}
        )
        assert evaluate_condition(
            {"field": "email", "operator": "matches", "value": "acme.com"},
            {"email": "user@acme.com"},
        )

    def test_empty_condition_is_always_visible(self):
        assert evaluate_condition(None, {})
        assert evaluate_condition({}, {})
        assert evaluate_condition({"depends_on": {"field": "x", "value": "y"}}, {"x": "y"})

    def test_filter_visible_questions(self):
        questions = [
            _q("personal_data_processed"),
            _q(
                "data_categories",
                required=True,
                condition={"field": "personal_data_processed", "operator": "equals", "value": "Yes"},
            ),
        ]
        visible = filter_visible_questions(questions, {"personal_data_processed": "No"})
        assert [q["id"] for q in visible] == ["personal_data_processed"]
        visible = filter_visible_questions(questions, {"personal_data_processed": "Yes"})
        assert len(visible) == 2


_BRANCHING_QUESTIONS = lambda: [
    _q("personal_data_processed", required=False),
    _q(
        "data_categories",
        required=True,
        condition={"field": "personal_data_processed", "operator": "equals", "value": "Yes"},
    ),
    _q(
        "data_retention_months",
        type="number",
        required=True,
        condition={"field": "personal_data_processed", "operator": "equals", "value": "Yes"},
    ),
]


class TestConditionAwareValidation:
    def test_hidden_required_field_not_required(self):
        """A branch-hidden required field must validate clean."""
        answers = {"personal_data_processed": "No"}
        assert validate_answers(
            "irrelevant", answers, questions=_BRANCHING_QUESTIONS()
        ) == []

    def test_visible_required_field_still_enforced(self):
        answers = {"personal_data_processed": "Yes"}
        errors = validate_answers(
            "irrelevant", answers, questions=_BRANCHING_QUESTIONS()
        )
        assert any("data_categories" in e for e in errors)

    def test_formal_schema_validator_respects_conditions(self):
        result = validate_agreement_data(
            {"personal_data_processed": "No", "governing_law": "LK"},
            _BRANCHING_QUESTIONS(),
        )
        assert result.valid

    def test_formal_validator_still_requires_visible_fields(self):
        result = validate_agreement_data(
            {"personal_data_processed": "Yes", "governing_law": "LK"},
            _BRANCHING_QUESTIONS(),
        )
        assert not result.valid
        assert any("data_categories" in e.message for e in result.errors)

    def test_new_numeric_types_validated(self):
        from app.services.schema_validation_service import _validate_type, ValidationIssue

        issues = []
        _validate_type("not-a-number", "money", "fee", issues)
        assert issues and "must be a number" in issues[0].message
        issues.clear()
        _validate_type(1000, "money", "fee", issues)
        assert not issues
        issues.clear()
        _validate_type("5%", "percentage", "rate", issues)
        assert issues