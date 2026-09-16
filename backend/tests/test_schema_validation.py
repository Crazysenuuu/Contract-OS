"""Tests for the formal agreement schema validation pipeline (spec 1.9)."""

import uuid

import pytest

from app.services.schema_validation_service import (
    validate_agreement_data,
    validate_agreement_schema,
    validate_field_level,
    validate_parties,
    validate_signatories,
    validate_jurisdiction,
)

QUESTIONS = [
    {"key": "effective_date", "type": "date", "label": "Effective Date", "required": True},
    {"key": "party_a_name", "type": "text", "label": "Party A", "required": True},
    {"key": "party_b_name", "type": "text", "label": "Party B", "required": True},
    {"key": "party_a_signatory_name", "type": "text", "label": "Party A Signatory", "required": False},
    {"key": "confidentiality_period", "type": "select", "label": "Period", "options": ["1 year", "2 years"], "required": True},
    {"key": "governing_law", "type": "text", "label": "Governing Law", "required": True},
    {"key": "contract_value", "type": "number", "label": "Value", "required": False, "min": 1000},
    {"key": "contact_email", "type": "email", "label": "Email", "required": False},
]


def test_valid_answers_pass():
    result = validate_agreement_data(
        {
            "effective_date": "2026-01-01",
            "party_a_name": "Acme Corp",
            "party_b_name": "Globex Inc",
            "confidentiality_period": "2 years",
            "governing_law": "LK",
            "contract_value": 5000,
            "contact_email": "a@b.com",
        },
        QUESTIONS,
    )
    assert result.valid is True
    assert result.error_messages == []


def test_missing_required_field():
    result = validate_agreement_data(
        {"party_a_name": "Acme", "party_b_name": "Globex", "confidentiality_period": "1 year", "governing_law": "LK"},
        QUESTIONS,
    )
    assert result.valid is False
    assert any("Effective Date" in e for e in result.error_messages)


def test_invalid_enum_option():
    result = validate_agreement_data(
        {
            "effective_date": "2026-01-01",
            "party_a_name": "Acme",
            "party_b_name": "Globex",
            "confidentiality_period": "Forever!!",
            "governing_law": "LK",
        },
        QUESTIONS,
    )
    assert result.valid is False
    assert any("one of" in e for e in result.error_messages)


def test_number_min_max():
    result = validate_agreement_data(
        {
            "effective_date": "2026-01-01",
            "party_a_name": "Acme",
            "party_b_name": "Globex",
            "confidentiality_period": "1 year",
            "governing_law": "LK",
            "contract_value": 100,
        },
        QUESTIONS,
    )
    assert result.valid is False
    assert any("at least 1000" in e for e in result.error_messages)


def test_invalid_date_format():
    errors, _ = validate_field_level(
        {"effective_date": "01/01/2026"},
        QUESTIONS,
    )
    assert any("ISO date" in e.message for e in errors)


def test_invalid_email():
    errors, _ = validate_field_level(
        {"contact_email": "not-an-email"},
        QUESTIONS,
    )
    assert any("email" in e.message for e in errors)


def test_parties_must_be_distinct():
    errors = validate_parties({"party_a_name": "Same Co", "party_b_name": "Same Co"})
    assert any("distinct" in e.message for e in errors)


def test_parties_ok_when_distinct():
    errors = validate_parties({"party_a_name": "Acme", "party_b_name": "Globex"})
    assert errors == []


def test_signatory_required_when_party_present():
    errors = validate_signatories(
        {"party_a_name": "Acme", "party_a_signatory_name": ""}
    )
    assert any("Signatory name" in e.message for e in errors)


def test_jurisdiction_required():
    errors, warnings = validate_jurisdiction({})
    assert any("required" in e.message for e in errors)


def test_jurisdiction_recognized_code():
    errors, warnings = validate_jurisdiction({"governing_law": "LK"})
    assert errors == []
    assert warnings == []


def test_jurisdiction_unknown_is_warning_not_error():
    errors, warnings = validate_jurisdiction({"governing_law": "Atlantis"})
    assert errors == []
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_validate_against_stored_schema(db_session, test_agreement_type):
    test_agreement_type.schema = {
        "questions": [
            {"key": "effective_date", "type": "date", "label": "Effective Date", "required": True},
            {"key": "party_a_name", "type": "text", "label": "Party A", "required": True},
            {"key": "party_b_name", "type": "text", "label": "Party B", "required": True},
            {"key": "governing_law", "type": "text", "label": "Governing Law", "required": True},
        ]
    }
    await db_session.flush()

    result = await validate_agreement_schema(
        db_session,
        agreement_type_id=test_agreement_type.id,
        answers={"party_a_name": "Acme", "party_b_name": "Globex", "governing_law": "LK"},
    )
    assert result.valid is False
    assert any("Effective Date" in e for e in result.error_messages)
    assert "field" in result.stages_run
    assert "party" in result.stages_run
    assert "jurisdiction" in result.stages_run


@pytest.mark.asyncio
async def test_validate_missing_type_returns_error(db_session):
    result = await validate_agreement_schema(
        db_session,
        agreement_type_id=uuid.uuid4(),
        answers={},
    )
    assert result.valid is False