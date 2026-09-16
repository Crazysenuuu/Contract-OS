"""Tests for natural-language agreement creation (spec ¶69)."""

import pytest
from sqlalchemy import select

from app.models.agreement import Agreement
from app.services.nl_creation_service import (
    NLCreationError,
    NLCreationService,
    _extract_answers,
    _extract_governing_law,
    _extract_parties,
    _extract_title,
    _resolve_template_key,
)


def test_resolve_template_key_matches_aliases():
    assert _resolve_template_key("NDA with Acme", ["mutual_nda", "msa"]) == "mutual_nda"
    assert (
        _resolve_template_key(
            "master services agreement", ["mutual_nda", "master_services_agreement"]
        )
        == "master_services_agreement"
    )
    assert _resolve_template_key("some random doc", ["mutual_nda", "msa"]) is None
    assert _resolve_template_key("some random doc", ["mutual_nda"]) == "mutual_nda"


def test_bare_nda_prompt_resolves_direction():
    """Spec §21: a bare NDA prompt auto-selects mutual vs unilateral."""
    keys = ["mutual_nda", "unilateral_nda", "msa"]
    # "NDA with Acme" — no direction signal → default one-way form.
    assert _resolve_template_key("NDA with Acme", keys) == "unilateral_nda"
    # Mutual signals pick the mutual form.
    assert _resolve_template_key("mutual NDA with Acme", keys) == "mutual_nda"
    assert (
        _resolve_template_key(
            "NDA between Acme and Globex; both parties will disclose", keys
        )
        == "mutual_nda"
    )
    # Unilateral signals override the mutual default.
    assert (
        _resolve_template_key(
            "one-way NDA where only we will disclose our source code", keys
        )
        == "unilateral_nda"
    )
    # Only the available direction is used.
    assert _resolve_template_key("NDA with Acme", ["mutual_nda"]) == "mutual_nda"
    assert _resolve_template_key("NDA with Acme", ["unilateral_nda"]) == "unilateral_nda"
    # NDA types not available and multiple candidates → no match.
    assert _resolve_template_key("NDA with Acme", ["msa", "employment"]) is None


def test_extract_title_prefers_quotes():
    assert _extract_title('Create an "Mutual NDA" for us') == "Mutual NDA"
    title = _extract_title("Set up a consulting agreement for the marketing project")
    assert "consulting agreement" in title.lower()


def test_extract_governing_law():
    assert _extract_governing_law("governed by the laws of New York") == "New York"
    assert _extract_governing_law("no jurisdiction mentioned") is None


def test_extract_parties():
    parties = _extract_parties(
        "Agreement between Acme Corp and Globex Inc for widget supply"
    )
    assert any("Acme" in p["name"] for p in parties)
    assert any("Globex" in p["name"] for p in parties)


def test_extract_answers_matches_questionnaire_keys():
    questions = [
        {"key": "company_email", "label": "Company email"},
        {"key": "party_a_name", "label": "Disclosing party company name"},
    ]
    answers = _extract_answers(
        "company_email: legal@acme.com party_a_name: Acme Corp", questions
    )
    assert answers.get("company_email") == "legal@acme.com"
    assert answers.get("party_a_name") == "Acme Corp"


@pytest.mark.asyncio
async def test_create_from_prompt_without_template_fails(
    db_session, test_org, test_user
):
    svc = NLCreationService(db_session)
    with pytest.raises(NLCreationError):
        await svc.create_from_prompt(
            "A mysterious arrangement about unspecified things",
            org_id=test_org.id,
            current_user=test_user,
        )


@pytest.mark.asyncio
async def test_create_from_prompt_creates_draft_agreement(
    db_session, test_org, test_user, test_agreement_type
):
    # Give the type a questionnaire with fields the prompt can fill.
    test_agreement_type.schema = {
        "questions": [
            {"key": "company_email", "label": "Company email"},
            {"key": "party_a_name", "label": "Disclosing company name"},
        ],
        "clauses": [],
    }
    await db_session.flush()

    svc = NLCreationService(db_session)
    agreement, intent = await svc.create_from_prompt(
        'Create an "Acme NDA" between Acme Corp and Globex under New York law. '
        "The company_email is legal@acme.com.",
        org_id=test_org.id,
        current_user=test_user,
    )
    assert agreement is not None
    assert agreement.status == "draft"
    assert agreement.organization_id == test_org.id
    assert intent["title"] == "Acme NDA"
    assert intent["governing_law"] == "New York"
    assert intent["template_key"] == test_agreement_type.key
    assert agreement.data.get("company_email") == "legal@acme.com"


@pytest.mark.asyncio
async def test_create_from_prompt_multiple_types_requires_named_kind(
    db_session, test_org, test_user, test_agreement_type
):
    from app.models.agreement_type import AgreementType

    db_session.add(
        AgreementType(
            key="master_services_agreement",
            name="Master Services Agreement",
            description="Master services",
            category="services",
            schema={"questions": [], "clauses": []},
        )
    )
    await db_session.flush()

    svc = NLCreationService(db_session)
    with pytest.raises(NLCreationError):
        await svc.create_from_prompt(
            "An agreement with no clear type mentioned anywhere here",
            org_id=test_org.id,
            current_user=test_user,
        )

    agreement, _ = await svc.create_from_prompt(
        "Set up an MSA between the parties for consulting services",
        org_id=test_org.id,
        current_user=test_user,
    )
    assert agreement.agreement_type_id is not None