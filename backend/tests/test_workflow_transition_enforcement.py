"""Workflow transition enforcement (spec gap: requires_permission + prerequisite gates).

Verifies that:
- A transition declaring requires_permission is rejected with HTTP 403 for an
  actor who is not the agreement creator and lacks the participant permission.
- A participant holding the required permission can perform the transition.
- A transition cannot advance while the agreement has open negotiation changes
  (HTTP 409 gate) -- e.g. sending/executing during live redlines is illegal.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.agreement_access import AgreementParticipant
from app.models.user import User
from app.core.security import hash_password
from app.services.workflow_engine import WorkflowEngine
from app.services.workflow_seed import seed_workflow

pytestmark = pytest.mark.asyncio

WORKFLOW_KEY = "mutual_nda_lk_v1"


@pytest_asyncio.fixture(autouse=True)
async def _seeded_workflow(db_session: AsyncSession, test_agreement: Agreement):
    from app.models.workflow import WorkflowInstance

    await seed_workflow(db_session, workflow_key=WORKFLOW_KEY)
    engine = WorkflowEngine()
    await engine.get_or_create_instance(
        db_session, agreement_id=test_agreement.id, workflow_key=WORKFLOW_KEY
    )
    yield engine


async def _make_user(db_session: AsyncSession, email: str, name: str) -> User:
    user = User(
        email=email,
        name=name,
        password_hash=hash_password("TestPass123!"),
        status="active",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _join_org(db_session, org_id, user_id: int, role_name: str | None = None):
    from app.models.rbac import Role, OrganizationMember

    role = Role(
        organization_id=org_id,
        name=role_name or f"editor-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(role)
    await db_session.flush()
    member = OrganizationMember(
        organization_id=org_id,
        user_id=user_id,
        role_id=role.id,
        status="active",
    )
    db_session.add(member)
    await db_session.flush()


async def _add_participant(db_session, agreement_id, user_id, *, can_approve: bool):
    from app.models.agreement_access import AgreementParty
    from app.models.legal_entity import LegalEntity

    entity = LegalEntity(
        organization_id=uuid.UUID(int=0),
        legal_name="Counterparty Ltd",
        country="LK",
        entity_type="corporation",
    )
    db_session.add(entity)
    await db_session.flush()
    party = AgreementParty(
        agreement_id=agreement_id,
        legal_entity_id=entity.id,
        party_role="receiving",
        display_name="Counterparty Ltd",
    )
    db_session.add(party)
    await db_session.flush()

    participant = AgreementParticipant(
        agreement_id=agreement_id,
        agreement_party_id=party.id,
        user_id=user_id,
        participant_role="lawyer",
        status="active",
        can_view=True,
        can_comment=True,
        can_propose_changes=True,
        can_approve=can_approve,
        can_sign=can_approve,
    )
    db_session.add(participant)
    await db_session.flush()


async def _transition(engine, db, agreement_id, action_key, actor_id, org_id):
    from fastapi import HTTPException

    try:
        result = await engine.transition(
            db,
            agreement_id=agreement_id,
            action_key=action_key,
            actor_id=actor_id,
            actor_type="user",
            tenant_id=org_id,
        )
        return result
    except HTTPException as exc:
        return exc


async def test_non_participant_org_member_is_blocked(
    db_session, test_agreement, test_org, _seeded_workflow
):
    outsider = await _make_user(db_session, "outsider@example.com", "Outsider")
    await _join_org(db_session, test_org.id, outsider.id)

    result = await _transition(
        _seeded_workflow,
        db_session,
        test_agreement.id,
        "send_directly",
        outsider.id,
        test_org.id,
    )
    assert getattr(result, "status_code", 200) == 403


async def test_send_directly_requires_manage_participants_permission(
    db_session, test_agreement, test_org, _seeded_workflow
):
    engine = _seeded_workflow

    authorized = await _make_user(db_session, "approver@example.com", "Approver")
    await _join_org(db_session, test_org.id, authorized.id)
    await _add_participant(db_session, test_agreement.id, authorized.id, can_approve=True)

    blocked = await _make_user(db_session, "blocked@example.com", "Blocked")
    await _join_org(db_session, test_org.id, blocked.id)
    await _add_participant(db_session, test_agreement.id, blocked.id, can_approve=False)

    unauthorized = await _transition(
        engine,
        db_session,
        test_agreement.id,
        "send_directly",
        blocked.id,
        test_org.id,
    )
    assert unauthorized.status_code == 403
    assert "Missing permission" in unauthorized.detail

    result = await _transition(
        engine,
        db_session,
        test_agreement.id,
        "send_directly",
        authorized.id,
        test_org.id,
    )
    assert getattr(result, "status_code", 200) == 200
    assert result.current_state == "SENT"


async def test_send_gate_blocks_open_negotiation_change(
    db_session, test_agreement, test_org, test_user, _seeded_workflow
):
    from app.models.agreement import AgreementVersion
    from app.models.negotiation import AgreementChange

    version = AgreementVersion(
        agreement_id=test_agreement.id,
        version_number=1,
        content="ORIGINAL TERMS",
        content_hash="v1-hash",
        status="current",
        created_by=test_user.id,
    )
    db_session.add(version)
    await db_session.flush()

    change = AgreementChange(
        agreement_id=test_agreement.id,
        base_version_id=version.id,
        proposed_by=test_user.id,
        change_type="redline",
        explanation="open redline",
        status="client_confirmed",
    )
    db_session.add(change)
    await db_session.commit()

    result = await _transition(
        _seeded_workflow,
        db_session,
        test_agreement.id,
        "send_directly",
        test_user.id,
        test_org.id,
    )
    assert result.status_code == 409
    assert "open negotiation changes" in result.detail


async def test_send_gate_allows_transition_without_open_changes(
    db_session, test_agreement, test_org, test_user, _seeded_workflow
):
    result = await _transition(
        _seeded_workflow,
        db_session,
        test_agreement.id,
        "send_directly",
        test_user.id,
        test_org.id,
    )
    assert getattr(result, "status_code", 200) == 200
    assert result.current_state == "SENT"