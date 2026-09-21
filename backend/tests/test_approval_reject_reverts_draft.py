"""Spec 24.2: rejection during the approval chain reverts the agreement to Draft.

An approval submitted for review moves the agreement out of Draft
(pending_approval); rejecting it must cancel the approval AND revert the
agreement to Draft so the amended agreement restarts the cycle.
"""

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_start_approval_moves_agreement_to_pending_approval(
    db_session, test_user, test_org, test_agreement
):
    from app.api.v1 import approvals as approvals_api
    from app.services.approval_engine import create_approval_definition

    definition = await create_approval_definition(
        db=db_session,
        organization_id=test_org.id,
        name="Legal Gate",
        min_value=0,
        stages=[{"name": "Legal Review", "order": 1, "required_role": "legal"}],
    )

    async def _noop(*args, **kwargs):
        return None

    with patch.object(approvals_api, "verify_agreement_access", _noop):
        started = await approvals_api.start_approval_endpoint(
            agreement_id=test_agreement.id,
            data=approvals_api.StartApprovalRequest(
                agreement_id=test_agreement.id,
                definition_id=definition.id,
                approval_type="legal_review",
            ),
            current_user=test_user,
            org_id=test_org.id,
            db=db_session,
        )

    assert started.id is not None
    await db_session.refresh(test_agreement)
    assert test_agreement.status == "pending_approval"


@pytest.mark.asyncio
async def test_reject_reverts_agreement_to_draft(
    db_session, test_user, test_org, test_agreement
):
    from app.api.v1 import approvals as approvals_api
    from app.services.approval_engine import create_approval_definition

    definition = await create_approval_definition(
        db=db_session,
        organization_id=test_org.id,
        name="Legal Gate",
        min_value=0,
        stages=[{"name": "Legal Review", "order": 1, "required_role": "legal"}],
    )

    async def _noop(*args, **kwargs):
        return None

    with patch.object(approvals_api, "verify_agreement_access", _noop):
        started = await approvals_api.start_approval_endpoint(
            agreement_id=test_agreement.id,
            data=approvals_api.StartApprovalRequest(
                agreement_id=test_agreement.id,
                definition_id=definition.id,
                approval_type="legal_review",
            ),
            current_user=test_user,
            org_id=test_org.id,
            db=db_session,
        )

        decision = await approvals_api.make_decision(
            agreement_id=test_agreement.id,
            record_id=started.id,
            data=approvals_api.DecisionRequest(
                decision="rejected", comment="Needs redraft before next round"
            ),
            current_user=test_user,
            org_id=test_org.id,
            db=db_session,
        )

    assert decision.decision == "rejected"
    await db_session.refresh(test_agreement)
    assert test_agreement.status == "draft"

    # The approval record is cancelled, so a fresh cycle must be started.
    from sqlalchemy import select

    from app.models.approval import ApprovalRecord

    result = await db_session.execute(
        select(ApprovalRecord).where(ApprovalRecord.id == started.id)
    )
    stored = result.scalar_one()
    assert stored.status == "cancelled"


@pytest.mark.asyncio
async def test_approve_completes_agreement_approval(
    db_session, test_user, test_org, test_agreement
):
    from app.api.v1 import approvals as approvals_api
    from app.services.approval_engine import create_approval_definition

    definition = await create_approval_definition(
        db=db_session,
        organization_id=test_org.id,
        name="Legal Gate",
        min_value=0,
        stages=[{"name": "Legal Review", "order": 1, "required_role": "legal"}],
    )

    async def _noop(*args, **kwargs):
        return None

    with patch.object(approvals_api, "verify_agreement_access", _noop):
        started = await approvals_api.start_approval_endpoint(
            agreement_id=test_agreement.id,
            data=approvals_api.StartApprovalRequest(
                agreement_id=test_agreement.id,
                definition_id=definition.id,
                approval_type="legal_review",
            ),
            current_user=test_user,
            org_id=test_org.id,
            db=db_session,
        )

        await approvals_api.make_decision(
            agreement_id=test_agreement.id,
            record_id=started.id,
            data=approvals_api.DecisionRequest(decision="approved", comment="ok"),
            current_user=test_user,
            org_id=test_org.id,
            db=db_session,
        )

    await db_session.refresh(test_agreement)
    assert test_agreement.status == "approved"