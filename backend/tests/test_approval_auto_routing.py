"""Tests for rules-based approval auto-routing (spec 24.2).

The dynamic rules engine existed but nothing consumed it: starting an
approval required an explicit definition_id. These tests pin the new
resolve-and-start flow: explicit definition wins; otherwise the rules
engine matches on agreement metadata with the DOA matrix as fallback.
"""

import uuid

import pytest

from app.models.approval import ApprovalDefinition, ApprovalStage
from app.services.approval_engine import resolve_and_start_approval
from app.services.rules_engine import create_rule_definition


async def _make_definition(
    db, org_id, *, name, rules=None, role="cfo"
) -> ApprovalDefinition:
    definition = ApprovalDefinition(
        organization_id=org_id,
        name=name,
        is_active=True,
        rules=rules,
    )
    db.add(definition)
    await db.flush()
    db.add(
        ApprovalStage(
            definition_id=definition.id,
            name=f"{name} stage",
            order=1,
            required_role=role,
        )
    )
    await db.flush()
    return definition


@pytest.mark.asyncio
async def test_explicit_definition_wins_over_rules(db_session, test_org, test_agreement):
    explicit = await _make_definition(
        db_session, test_org.id, name="Explicit flow", role="legal"
    )
    await _make_definition(
        db_session,
        test_org.id,
        name="Value rule",
        rules={
            "conditions": {
                "all": [
                    {"name": "agreement_value", "operator": "greater_than", "value": 100},
                ]
            }
        },
        role="cfo",
    )
    # Make the agreement valuable enough to match the rule.
    test_agreement.data = {"value": 999_999}
    db_session.add(test_agreement)
    await db_session.flush()

    record, resolution = await resolve_and_start_approval(
        db_session,
        agreement_id=test_agreement.id,
        organization_id=test_org.id,
        definition_id=explicit.id,
    )
    assert record.definition_id == explicit.id
    assert resolution["source"] == "explicit"


@pytest.mark.asyncio
async def test_rules_engine_routes_by_metadata(db_session, test_org, test_agreement):
    await _make_definition(
        db_session,
        test_org.id,
        name="CFO above 100k",
        rules={
            "conditions": {
                "all": [
                    {"name": "agreement_value", "operator": "greater_than", "value": 100_000},
                    {"name": "agreement_type", "operator": "equal_to", "value": "mutual_nda"},
                ]
            }
        },
        role="cfo",
    )
    test_agreement.data = {"value": 250_000}
    db_session.add(test_agreement)
    await db_session.flush()

    record, resolution = await resolve_and_start_approval(
        db_session,
        agreement_id=test_agreement.id,
        organization_id=test_org.id,
    )
    assert resolution["source"] == "rules_engine"
    assert resolution["matched_rule"] is not None
    assert record.status in ("in_progress", "pending")


@pytest.mark.asyncio
async def test_no_match_raises_when_matrix_empty(db_session, test_org, test_agreement):
    """No rules matched and the DOA fallback returned no approvals for a
    low-value agreement -> the caller must be told routing failed, not
    silently given an approval nobody needs to action."""

    # A rule that cannot match (value far above the agreement's) and a
    # DOA band that also excludes it — otherwise the matrix fallback would
    # band-match it (min_value=None matches every value).
    decoy = await _make_definition(
        db_session,
        test_org.id,
        name="Enterprise only",
        rules={
            "conditions": {
                "all": [
                    {"name": "agreement_value", "operator": "greater_than", "value": 900_000_000},
                ]
            }
        },
        role="ceo",
    )
    decoy.min_value = 900_000_000
    decoy.max_value = None
    db_session.add(decoy)
    await db_session.flush()
    test_agreement.data = {"value": 100}
    db_session.add(test_agreement)
    await db_session.flush()

    with pytest.raises(ValueError):
        await resolve_and_start_approval(
            db_session,
            agreement_id=test_agreement.id,
            organization_id=test_org.id,
        )


@pytest.mark.asyncio
async def test_unknown_agreement_raises(db_session, test_org):
    with pytest.raises(ValueError, match="Agreement not found"):
        await resolve_and_start_approval(
            db_session,
            agreement_id=uuid.uuid4(),
            organization_id=test_org.id,
        )
