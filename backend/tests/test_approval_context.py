"""Tests for the 2.05 executive approval workspace context endpoint."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest


@pytest.fixture
async def agreement_version(db_session, test_agreement, test_user):
    """A current version row for negotiation FK requirements."""
    from app.models.agreement import AgreementVersion

    version = AgreementVersion(
        agreement_id=test_agreement.id,
        version_number=1,
        status="current",
        content="[rendered]",
        content_hash="abc123",
        created_by=test_user.id,
    )
    db_session.add(version)
    await db_session.flush()
    return version


@pytest.mark.asyncio
async def test_approval_context_draft_shape(
    db_session, test_user, test_org, test_agreement
):
    from app.api.v1 import approvals as approvals_api

    async def _noop(*args, **kwargs):
        return None

    with patch.object(approvals_api, "verify_agreement_access", _noop):
        ctx = await approvals_api.get_approval_context(
            agreement_id=test_agreement.id,
            current_user=test_user,
            org_id=test_org.id,
            db=db_session,
        )

    assert ctx["agreement"]["id"] == str(test_agreement.id)
    assert ctx["agreement"]["status"] == "draft"
    assert ctx["workflow"]["current_step"] == "drafting"
    assert ctx["legal_review"]["status"] == "pending"
    assert ctx["version"] is None  # no current version created yet
    assert ctx["viewer"]["can_approve"] is False
    assert ctx["changes"] == {"added": 0, "modified": 0, "removed": 0}


@pytest.mark.asyncio
async def test_approval_context_creator_with_org_party_can_approve(
    db_session, test_user, test_org, test_agreement, test_legal_entity
):
    from app.api.v1 import approvals as approvals_api
    from app.models.agreement_access import AgreementParty

    party = AgreementParty(
        agreement_id=test_agreement.id,
        legal_entity_id=test_legal_entity.id,
        party_role="large",
        display_name="Test Corp Ltd",
    )
    db_session.add(party)
    await db_session.commit()

    async def _noop(*args, **kwargs):
        return None

    with patch.object(approvals_api, "verify_agreement_access", _noop):
        ctx = await approvals_api.get_approval_context(
            agreement_id=test_agreement.id,
            current_user=test_user,
            org_id=test_org.id,
            db=db_session,
        )

    assert ctx["viewer"]["role"] == "authorized_representative"
    assert ctx["viewer"]["can_approve"] is True
    assert ctx["viewer"]["party_id"] == str(party.id)


@pytest.mark.asyncio
async def test_approval_context_data_step_mapping(
    db_session, test_user, test_org, test_agreement
):
    from app.api.v1 import approvals as approvals_api

    test_agreement.status = "pending_approval"
    await db_session.commit()

    async def _noop(*args, **kwargs):
        return None

    with patch.object(approvals_api, "verify_agreement_access", _noop):
        ctx = await approvals_api.get_approval_context(
            agreement_id=test_agreement.id,
            current_user=test_user,
            org_id=test_org.id,
            db=db_session,
        )

    assert ctx["workflow"]["current_step"] == "party_a_confirmation"


@pytest.mark.asyncio
async def test_approval_context_counts_accepted_changes(
    db_session, test_user, test_org, test_agreement, agreement_version
):
    from app.api.v1 import approvals as approvals_api
    from app.models.negotiation import AgreementChange, AgreementChangeItem

    change = AgreementChange(
        agreement_id=test_agreement.id,
        base_version_id=agreement_version.id,
        proposed_by=test_user.id,
        change_type="amendment",
        status="accepted",
    )
    db_session.add(change)
    await db_session.flush()
    for ct in ("modify", "add", "remove"):
        db_session.add(
            AgreementChangeItem(
                change_id=change.id,
                clause_identifier=f"clause.{ct}",
                change_type=ct,
                status="accepted",
            )
        )
    for ct in ("modify",):
        db_session.add(
            AgreementChangeItem(
                change_id=change.id,
                clause_identifier="clause.skipped",
                change_type=ct,
                status="proposed",
            )
        )
    await db_session.commit()

    async def _noop(*args, **kwargs):
        return None

    with patch.object(approvals_api, "verify_agreement_access", _noop):
        ctx = await approvals_api.get_approval_context(
            agreement_id=test_agreement.id,
            current_user=test_user,
            org_id=test_org.id,
            db=db_session,
        )

    assert ctx["changes"]["modified"] == 1
    assert ctx["changes"]["added"] == 1
    assert ctx["changes"]["removed"] == 1


@pytest.mark.asyncio
async def test_approval_context_reports_completed_legal_review(
    db_session, test_user, test_org, test_agreement
):
    from app.api.v1 import approvals as approvals_api
    from app.models.approval import ApprovalDecision, ApprovalRecord, ApprovalStage
    from app.services.approval_engine import create_approval_definition
    from sqlalchemy import select

    definition = await create_approval_definition(
        db=db_session,
        organization_id=test_org.id,
        name="Legal Gate",
        min_value=0,
        stages=[{"name": "Legal Review", "order": 1, "required_role": "legal"}],
    )
    stage_id = (
        await db_session.execute(
            select(ApprovalStage).where(ApprovalStage.definition_id == definition.id)
        )
    ).scalar_one().id
    record = ApprovalRecord(
        agreement_id=test_agreement.id,
        definition_id=definition.id,
        current_stage_id=stage_id,
        status="approved",
        approval_type="legal_review",
        agreement_version_id=None,
    )
    db_session.add(record)
    await db_session.flush()
    db_session.add(
        ApprovalDecision(
            record_id=record.id,
            stage_id=stage_id,
            user_id=test_user.id,
            decision="approved",
            decided_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    async def _noop(*args, **kwargs):
        return None

    with patch.object(approvals_api, "verify_agreement_access", _noop):
        ctx = await approvals_api.get_approval_context(
            agreement_id=test_agreement.id,
            current_user=test_user,
            org_id=test_org.id,
            db=db_session,
        )

    assert ctx["legal_review"]["status"] == "confirmed"
    assert ctx["legal_review"]["confirmed_by"] == str(test_user.id)