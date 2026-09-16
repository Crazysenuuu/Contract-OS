"""Version-bound approvals (spec 1.2 / 24: approvals must not outlive the version they reviewed).

Verifies that:
- Starting an approval snapshots the agreement's current version.
- A decision is rejected with HTTP 409 once a newer version has become current.
- Promoting a new version to current cancels open (pending/in_progress) approvals.
- approval_type defaults to 'legal_review' and separates party approvals.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement, AgreementVersion
from app.services.agreement_versioning import get_latest_version, promote_version

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _current_version(db_session: AsyncSession, test_agreement, test_user):
    version = AgreementVersion(
        agreement_id=test_agreement.id,
        version_number=1,
        content="TERMS V1",
        content_hash="v1-hash",
        status="current",
        created_by=test_user.id,
    )
    db_session.add(version)
    await db_session.commit()
    await db_session.refresh(version)
    return version


async def _definition(db_session, test_org) -> str:
    from app.services.approval_engine import create_approval_definition

    definition = await create_approval_definition(
        db=db_session,
        organization_id=test_org.id,
        name="Legal Review",
        stages=[
            {"name": "Lawyer", "order": 1, "required_role": "legal"},
        ],
    )
    await db_session.commit()
    return str(definition.id)


async def _start(client, headers, agreement_id, definition_id, **mods):
    payload = {
        "agreement_id": str(agreement_id),
        "definition_id": definition_id,
    }
    payload.update(mods)
    return await client.post(
        f"/api/v1/agreements/{agreement_id}/approvals/start",
        json=payload,
        headers=headers,
    )


async def _start_the_approval(client, headers, db_session, agreement_id, test_org):
    definition_id = await _definition(db_session, test_org)
    resp = await _start(client, headers, agreement_id, definition_id)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _promote_new_current(db_session, test_agreement, test_user) -> AgreementVersion:
    latest = await get_latest_version(db_session, test_agreement.id)
    version = AgreementVersion(
        agreement_id=test_agreement.id,
        version_number=(latest.version_number + 1) if latest else 1,
        content="TERMS V2",
        content_hash="v2-hash",
        status="proposed",
        created_by=test_user.id,
    )
    db_session.add(version)
    await db_session.flush()
    await promote_version(db_session, test_agreement, version)
    await db_session.commit()
    return version


async def test_start_snapshots_current_version(
    client, auth_headers, db_session, test_agreement, test_org, _current_version
):
    record = await _start_the_approval(
        client, auth_headers, db_session, test_agreement.id, test_org
    )
    assert record["agreement_version_id"] == str(_current_version.id)
    assert record["approval_type"] == "legal_review"


async def test_decision_ok_while_version_unchanged(
    client, auth_headers, db_session, test_agreement, test_org, _current_version
):
    record = await _start_the_approval(
        client, auth_headers, db_session, test_agreement.id, test_org
    )
    decide = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/approvals/{record['id']}/decide",
        json={"decision": "approved", "comment": "fine"},
        headers=auth_headers,
    )
    assert decide.status_code == 201, decide.text


async def test_decision_rejected_after_new_version_promoted(
    client, auth_headers, db_session, test_agreement, test_org, test_user, _current_version
):
    record = await _start_the_approval(
        client, auth_headers, db_session, test_agreement.id, test_org
    )
    await _promote_new_current(db_session, test_agreement, test_user)

    decide = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/approvals/{record['id']}/decide",
        json={"decision": "approved"},
        headers=auth_headers,
    )
    assert decide.status_code == 409, decide.text
    assert "version changed" in decide.json()["detail"]


async def test_promote_cancels_open_approvals(
    client, auth_headers, db_session, test_agreement, test_org, test_user, _current_version
):
    from sqlalchemy import select

    from app.models.approval import ApprovalRecord

    record = await _start_the_approval(
        client, auth_headers, db_session, test_agreement.id, test_org
    )
    await _promote_new_current(db_session, test_agreement, test_user)

    result = await db_session.execute(
        select(ApprovalRecord).where(ApprovalRecord.id == record["id"])
    )
    stored = result.scalar_one()
    assert stored.status == "cancelled"


async def test_party_approval_type_accepted(
    client, auth_headers, db_session, test_agreement, test_org, _current_version
):
    definition_id = await _definition(db_session, test_org)
    resp = await _start(
        client,
        auth_headers,
        test_agreement.id,
        definition_id,
        approval_type="party_approval",
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["approval_type"] == "party_approval"

    bad = await _start(
        client,
        auth_headers,
        test_agreement.id,
        definition_id,
        approval_type="wardrobe",
    )
    assert bad.status_code == 400