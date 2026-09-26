"""Clause library tests (spec 1.8).

Covers:
- safe condition evaluation (no eval, closed operator set)
- strict variable rendering (missing variables fail, never invented)
- clause hashing
- approved clause version immutability + supersede chain
- applicability (jurisdiction + conditions)
- type-binding-driven assembly with required-clause enforcement
- exact clause provenance on generated versions
"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from app.services.clause_condition_engine import ClauseConditionEngine
from app.services.clause_hash import calculate_clause_hash
from app.services.clause_renderer import ClauseRenderer, MissingClauseVariableError


# --- Condition engine --------------------------------------------------


def test_condition_engine_all():
    engine = ClauseConditionEngine()
    cond = {
        "all": [
            {"path": "transaction.contains_personal_data", "operator": "equals", "value": True},
            {"path": "agreement.governing_law", "operator": "exists"},
        ]
    }
    data = {"transaction": {"contains_personal_data": True}, "agreement": {"governing_law": "LK"}}
    assert engine.evaluate(cond, data).matched
    data_bad = {"transaction": {"contains_personal_data": False}, "agreement": {"governing_law": "LK"}}
    assert not engine.evaluate(cond, data_bad).matched


def test_condition_engine_any_and_operators():
    engine = ClauseConditionEngine()
    assert engine.evaluate(
        {"any": [{"path": "a.x", "operator": "equals", "value": 1}, {"path": "a.y", "operator": "equals", "value": 2}]},
        {"a": {"y": 2}},
    ).matched
    assert engine.evaluate({"path": "a.n", "operator": "greater_than", "value": 5}, {"a": {"n": 10}}).matched
    assert not engine.evaluate({"path": "a.n", "operator": "greater_than", "value": 5}, {"a": {"n": 3}}).matched
    assert engine.evaluate({"path": "a.role", "operator": "in", "value": ["buyer", "seller"]}, {"a": {"role": "buyer"}}).matched
    with pytest.raises(ValueError):
        engine.evaluate({"path": "a.x", "operator": "regex_match", "value": ".*"}, {"a": {"x": "y"}})


def test_condition_engine_nested_paths():
    engine = ClauseConditionEngine()
    cond = {"path": "parties_by_role.buyer.legal_name", "operator": "exists"}
    assert engine.evaluate(cond, {"parties_by_role": {"buyer": {"legal_name": "Acme"}}}).matched
    assert not engine.evaluate(cond, {"parties_by_role": {}}).matched


# --- Renderer ----------------------------------------------------------


def test_renderer_resolves_variables():
    r = ClauseRenderer()
    out = r.render(
        "{{party_a.legal_name}} agrees to pay {{transaction.amount}}.",
        {"party_a": {"legal_name": "Acme Ltd"}, "transaction": {"amount": "USD 1,000"}},
    )
    assert out == "Acme Ltd agrees to pay USD 1,000."


def test_renderer_is_strict_about_missing_variables():
    r = ClauseRenderer()
    with pytest.raises(MissingClauseVariableError) as exc:
        r.render("Between {{party_a.legal_name}} and {{party_b.legal_name}}", {"party_a": {"legal_name": "Acme"}})
    assert "party_b.legal_name" in exc.value.missing


def test_renderer_never_invents_values():
    """No [COMPANY NAME] style placeholders - missing data fails the render."""
    r = ClauseRenderer()
    with pytest.raises(MissingClauseVariableError):
        r.render("The Employer: {{parties_by_role.employer.legal_name}}", {})


# --- Hashing -----------------------------------------------------------


def test_clause_hash_is_stable_sha256():
    h1 = calculate_clause_hash("Confidentiality clause text")
    h2 = calculate_clause_hash("Confidentiality clause text")
    h3 = calculate_clause_hash("Confidentiality clause text ")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64


# --- DB-backed behaviour ------------------------------------------------


@pytest_asyncio.fixture
async def clause_setup(db_session, test_org, test_user, test_agreement_type):
    """A clause with an approved v1 and a draft v2, bound to the type."""
    from app.models.clause import Clause, ClauseVersion
    from app.models.clause import AgreementTypeClauseBinding

    clause = Clause(
        organization_id=test_org.id,
        key="confidentiality",
        name="Confidentiality",
        category="confidentiality",
        status="active",
    )
    db_session.add(clause)
    await db_session.flush()

    v1 = ClauseVersion(
        clause_id=clause.id,
        version_number=1,
        title="Confidentiality",
        content="{{party_a.legal_name}} shall keep all information confidential.",
        content_hash=calculate_clause_hash(
            "{{party_a.legal_name}} shall keep all information confidential."
        ),
        status="approved",
        effective_from=datetime.now(timezone.utc) - timedelta(days=1),
        approved_by=test_user.id,
    )
    v2 = ClauseVersion(
        clause_id=clause.id,
        version_number=2,
        title="Confidentiality v2",
        content="DRAFT - not yet effective.",
        content_hash=calculate_clause_hash("DRAFT - not yet effective."),
        status="draft",
    )
    db_session.add_all([v1, v2])

    binding = AgreementTypeClauseBinding(
        organization_id=test_org.id,
        agreement_type_id=test_agreement_type.id,
        clause_id=clause.id,
        display_order=1,
        required=True,
    )
    db_session.add(binding)
    await db_session.commit()
    await db_session.refresh(clause)
    await db_session.refresh(v1)
    await db_session.refresh(v2)
    return {"clause": clause, "v1": v1, "v2": v2, "binding": binding}


@pytest.mark.asyncio
async def test_selector_returns_only_approved_version(db_session, clause_setup):
    from app.services.clause_selector import ClauseSelector

    setup = clause_setup
    selector = ClauseSelector()
    selected = await selector.select_current_approved_version(
        db_session, clause_id=setup["clause"].id
    )
    assert selected is not None
    assert selected.id == setup["v1"].id


@pytest.mark.asyncio
async def test_select_clauses_for_agreement_no_lazy_load(db_session, clause_setup):
    """Regression: the old code guarded condition loading with
    ``if not version.conditions:`` — an attribute access that itself attempts
    an async lazy-load and raises MissingGreenlet under asyncio. The selector
    must use an explicit query and never touch the relationship."""
    from app.models.clause import ClauseCondition
    from app.services.clause_condition_engine import ClauseConditionEngine
    from app.services.clause_selector import ClauseSelector

    setup = clause_setup
    db_session.add(
        ClauseCondition(
            clause_version_id=setup["v1"].id,
            condition={
                "all": [
                    {
                        "path": "agreement.governing_law",
                        "operator": "exists",
                    }
                ]
            },
            display_order=1,
        )
    )
    await db_session.commit()

    selector = ClauseSelector()
    # The version object here is freshly selected inside the service, so any
    # relationship access on it would previously explode with MissingGreenlet.
    selected = await selector.select_clauses_for_agreement(
        db_session,
        agreement_type_id=setup["binding"].agreement_type_id,
        agreement_data={"agreement": {"governing_law": "LK"}},
        organization_id=setup["clause"].organization_id,
    )
    assert len(selected) == 1
    assert selected[0].version.id == setup["v1"].id


@pytest.mark.asyncio
async def test_select_clauses_for_agreement_condition_fails_excludes(
    db_session, clause_setup
):
    """A clause whose condition evaluates false is excluded from assembly."""
    from app.models.clause import ClauseCondition
    from app.services.clause_selector import ClauseSelector

    setup = clause_setup
    db_session.add(
        ClauseCondition(
            clause_version_id=setup["v1"].id,
            condition={
                "all": [
                    {
                        "path": "agreement.governing_law",
                        "operator": "equals",
                        "value": "DE",
                    }
                ]
            },
            display_order=1,
        )
    )
    await db_session.commit()

    selector = ClauseSelector()
    selected = await selector.select_clauses_for_agreement(
        db_session,
        agreement_type_id=setup["binding"].agreement_type_id,
        agreement_data={"agreement": {"governing_law": "LK"}},
        organization_id=setup["clause"].organization_id,
    )
    assert selected == []


@pytest.mark.asyncio
async def test_approved_version_is_immutable_in_lifecycle(db_session, clause_setup):
    """Editing approval flow never rewrites approved content: superseding
    creates a new current version while v1 remains referenceable."""
    from app.models.clause import ClauseVersion
    from sqlalchemy import select

    setup = clause_setup
    v2 = setup["v2"]
    v2.status = "approved"
    v2.effective_from = datetime.now(timezone.utc)
    v1 = setup["v1"]
    v1.status = "superseded"
    v1.effective_until = datetime.now(timezone.utc)
    await db_session.commit()

    # v1 content untouched
    result = await db_session.execute(
        select(ClauseVersion).where(ClauseVersion.id == v1.id)
    )
    persisted = result.scalar_one()
    assert persisted.content == "{{party_a.legal_name}} shall keep all information confidential."
    assert persisted.status == "superseded"


@pytest.mark.asyncio
async def test_jurisdiction_exclusion(db_session, clause_setup, test_org):
    from app.models.clause import ClauseJurisdiction
    from app.models.jurisdiction import Jurisdiction
    from app.services.clause_applicability import ClauseApplicabilityService

    setup = clause_setup
    jur = Jurisdiction(code="XX", name="Test Jurisdiction", language="en")
    db_session.add(jur)
    await db_session.flush()
    db_session.add(
        ClauseJurisdiction(
            clause_version_id=setup["v1"].id, jurisdiction_id=jur.id, applicable=False
        )
    )
    await db_session.commit()

    service = ClauseApplicabilityService()
    resolved = await service.resolve_clause(
        db_session,
        clause_id=setup["clause"].id,
        jurisdiction_id=jur.id,
        agreement_data={"party_a": {"legal_name": "Acme"}},
    )
    assert resolved is None  # excluded by jurisdiction binding

    resolved_ok = await service.resolve_clause(
        db_session,
        clause_id=setup["clause"].id,
        jurisdiction_id=None,
        agreement_data={"party_a": {"legal_name": "Acme"}},
    )
    assert resolved_ok is not None


@pytest.mark.asyncio
async def test_draft_generation_records_provenance(
    db_session, test_user, test_org, test_agreement_type, clause_setup
):
    """generate_draft: context from DB -> applicable clauses -> version +
    exact clause provenance (spec 1.8.17)."""
    from app.models.agreement import Agreement
    from app.models.agreement_access import AgreementParty
    from app.models.legal_entity import LegalEntity
    from app.models.clause import AgreementVersionClause
    from app.services.agreement_draft_assembler import generate_draft

    entity = LegalEntity(
        organization_id=test_org.id,
        legal_name="Acme Holdings Ltd",
        country="LK",
        entity_type="corporation",
    )
    db_session.add(entity)
    await db_session.flush()

    agreement = Agreement(
        organization_id=test_org.id,
        agreement_type_id=test_agreement_type.id,
        title="Clause-generated NDA",
        status="draft",
        created_by=test_user.id,
        data={},
    )
    db_session.add(agreement)
    await db_session.flush()

    party = AgreementParty(
        agreement_id=agreement.id,
        legal_entity_id=entity.id,
        party_role="disclosing_party",
    )
    db_session.add(party)
    await db_session.commit()
    await db_session.refresh(agreement)

    version = await generate_draft(db_session, agreement, test_user.id)
    assert version.content, "assembled draft must have content"
    assert "Acme Holdings Ltd" in version.content, "variables resolved from real entity"

    links = await db_session.execute(
        select(AgreementVersionClause).where(
            AgreementVersionClause.agreement_version_id == version.id
        )
    )
    links = links.scalars().all()
    assert len(links) == 1
    assert links[0].clause_version_id == clause_setup["v1"].id


@pytest.mark.asyncio
async def test_required_clause_missing_fails_loudly(db_session, test_user, test_org, test_agreement_type):
    """A required binding with no approved version blocks generation."""
    from app.models.agreement import Agreement
    from app.models.clause import AgreementTypeClauseBinding
    from app.services.agreement_draft_assembler import (
        AgreementDraftAssembler,
        MissingRequiredClauseError,
    )

    binding = AgreementTypeClauseBinding(
        organization_id=test_org.id,
        agreement_type_id=test_agreement_type.id,
        clause_id=None,  # will be set after creating clause with no versions
        display_order=2,
        required=True,
    )
    # clause with zero approved versions
    from app.models.clause import Clause, ClauseVersion

    clause = Clause(
        organization_id=test_org.id, key="governing_law_x", name="Governing Law"
    )
    db_session.add(clause)
    await db_session.flush()
    db_session.add(
        ClauseVersion(
            clause_id=clause.id,
            version_number=1,
            title="GL",
            content="Draft only.",
            content_hash=calculate_clause_hash("Draft only."),
            status="draft",
        )
    )
    binding.clause_id = clause.id
    db_session.add(binding)

    agreement = Agreement(
        organization_id=test_org.id,
        agreement_type_id=test_agreement_type.id,
        title="Failing draft",
        status="draft",
        created_by=test_user.id,
        data={},
    )
    db_session.add(agreement)
    await db_session.commit()
    await db_session.refresh(agreement)

    assembler = AgreementDraftAssembler()
    with pytest.raises(MissingRequiredClauseError):
        await assembler.assemble(
            db_session,
            agreement=agreement,
            agreement_data={"party_a": {"legal_name": "X"}},
        )
