"""Negotiation security invariants (spec 1.5 party isolation, 1.2 optimistic concurrency).

Verifies that:
- A proposal is rejected with HTTP 409 when submitted against a base version
  that is no longer the current one (stale-version attack prevention).
- A party cannot accept/reject/counter its own change proposal (403); only
  the opposing party can resolve it.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.agreement_access import AgreementParticipant, AgreementParty
from app.models.legal_entity import LegalEntity
from app.models.user import User
from app.core.security import hash_password

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _base_version(db_session, test_agreement, test_user):
    from app.models.agreement import AgreementVersion

    version = AgreementVersion(
        agreement_id=test_agreement.id,
        version_number=1,
        content="ORIGINAL TERMS",
        content_hash="orig-hash",
        status="current",
        created_by=test_user.id,
    )
    db_session.add(version)
    await db_session.commit()
    await db_session.refresh(version)
    return version


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


async def _add_party(
    db_session: AsyncSession,
    agreement_id,
    org_id,
    name: str,
) -> AgreementParty:
    entity = LegalEntity(
        organization_id=org_id,
        legal_name=name,
        country="LK",
        entity_type="corporation",
    )
    db_session.add(entity)
    await db_session.flush()
    party = AgreementParty(
        agreement_id=agreement_id,
        legal_entity_id=entity.id,
        party_role="disclosing",
        display_name=name,
    )
    db_session.add(party)
    await db_session.flush()
    return party


async def _add_participant(db_session, agreement_id, party_id, user_id):
    participant = AgreementParticipant(
        agreement_id=agreement_id,
        agreement_party_id=party_id,
        user_id=user_id,
        participant_role="lawyer",
        can_view=True,
        can_propose_changes=True,
        can_approve=True,
    )
    db_session.add(participant)
    await db_session.flush()


async def _propose(client, headers, agreement_id, **mods):
    payload = {
        "change_type": "redline",
        "modifications": [
            {
                "clause_identifier": "payment_terms",
                "change_type": "modify",
                "new_content": mods.get("new_content", "Payment within 45 days"),
            }
        ],
    }
    if mods.get("base_version_id"):
        payload["base_version_id"] = str(mods["base_version_id"])
    return await client.post(
        f"/api/v1/agreements/{agreement_id}/changes",
        json=payload,
        headers=headers,
    )


async def test_stale_base_version_returns_409(
    client, auth_headers, db_session, test_agreement, test_user
):
    from app.models.agreement import AgreementVersion

    # Create a newer current version so v1 becomes stale.
    stale = (
        await db_session.execute(
            __import__("sqlalchemy").select(AgreementVersion).where(
                AgreementVersion.agreement_id == test_agreement.id
            )
        )
    ).scalars().one()
    second = AgreementVersion(
        agreement_id=test_agreement.id,
        version_number=2,
        content="UPDATED TERMS BY SOMEONE ELSE",
        content_hash="new-hash",
        status="current",
        created_by=test_user.id,
    )
    db_session.add(second)
    await db_session.commit()

    resp = await _propose(
        client,
        auth_headers,
        test_agreement.id,
        base_version_id=stale.id,
    )
    assert resp.status_code == 409, resp.text
    assert "stale" in resp.json()["detail"].lower()


async def test_propose_with_current_base_version_ok(
    client, auth_headers, db_session, test_agreement, test_user
):
    from app.models.agreement import AgreementVersion
    from sqlalchemy import select

    current = (
        await db_session.execute(
            select(AgreementVersion)
            .where(AgreementVersion.agreement_id == test_agreement.id)
            .order_by(AgreementVersion.version_number.desc())
        )
    ).scalars().first()
    resp = await _propose(
        client, auth_headers, test_agreement.id, base_version_id=current.id
    )
    assert resp.status_code == 201, resp.text


async def _setup_two_party_agreement(
    db_session, test_agreement, test_org, test_user
):
    from sqlalchemy import select

    from app.models.rbac import OrganizationMember, Role

    second_user = await _make_user(db_session, "counterparty@example.com", "Counterparty")
    role = (
        await db_session.execute(select(Role).limit(1))
    ).scalars().first()
    if role is not None:
        db_session.add(
            OrganizationMember(
                organization_id=test_org.id,
                user_id=second_user.id,
                role_id=role.id,
                status="active",
            )
        )
    party_a = await _add_party(db_session, test_agreement.id, test_org.id, "OrgA Corp")
    party_b = await _add_party(db_session, test_agreement.id, test_org.id, "OrgB Corp")
    await _add_participant(db_session, test_agreement.id, party_a.id, test_user.id)
    await _add_participant(db_session, test_agreement.id, party_b.id, second_user.id)
    await db_session.commit()
    return second_user, party_a, party_b


def _headers_for(user_id):
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id=user_id)}"}


async def test_party_cannot_accept_own_proposal(
    client, db_session, test_agreement, test_org, test_user
):
    second_user, party_a, _ = await _setup_two_party_agreement(
        db_session, test_agreement, test_org, test_user
    )
    party_a_id = party_a.id
    proposer_headers = _headers_for(test_user.id)
    opposing_headers = _headers_for(second_user.id)

    proposed = await _propose(client, proposer_headers, test_agreement.id)
    assert proposed.status_code == 201, proposed.text
    change_id = proposed.json()["id"]
    assert proposed.json()["proposing_party_id"] == str(party_a_id)

    # The opposing party cannot act on an un-released proposal.
    un_released = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/changes/{change_id}/accept",
        headers=opposing_headers,
    )
    assert un_released.status_code == 403, un_released.text

    # And the proposing party cannot resolve its own proposal.
    same_party = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/changes/{change_id}/accept",
        headers=proposer_headers,
    )
    assert same_party.status_code == 403, same_party.text

    # Internal gate: proposed -> client_confirmed -> released by Party A.
    confirmed = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/changes/{change_id}/confirm",
        headers=proposer_headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "client_confirmed"

    # The opposing party STILL cannot act before release.
    still_hidden = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/changes/{change_id}/accept",
        headers=opposing_headers,
    )
    assert still_hidden.status_code == 403, still_hidden.text

    released = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/changes/{change_id}/release",
        headers=proposer_headers,
    )
    assert released.status_code == 200, released.text
    assert released.json()["status"] == "released"

    # Now the opposing party can accept it.
    opposing = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/changes/{change_id}/accept",
        headers=opposing_headers,
    )
    assert opposing.status_code == 200, opposing.text
    assert opposing.json()["status"] == "accepted"


async def test_opposing_side_cannot_confirm_or_release(
    client, db_session, test_agreement, test_org, test_user
):
    second_user, _, _ = await _setup_two_party_agreement(
        db_session, test_agreement, test_org, test_user
    )
    proposer_headers = _headers_for(test_user.id)

    proposed = await _propose(client, proposer_headers, test_agreement.id)
    change_id = proposed.json()["id"]

    for action in ("confirm", "release"):
        resp = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/changes/{change_id}/{action}",
            headers=_headers_for(second_user.id),
        )
        assert resp.status_code == 403, resp.text


async def test_released_proposal_visible_only_to_opposing_side(
    client, db_session, test_agreement, test_org, test_user
):
    second_user, _, _ = await _setup_two_party_agreement(
        db_session, test_agreement, test_org, test_user
    )
    proposer_headers = _headers_for(test_user.id)
    opposing_headers = _headers_for(second_user.id)

    proposed = await _propose(client, proposer_headers, test_agreement.id)
    change_id = proposed.json()["id"]

    # Proposing side sees it immediately.
    own_view = await client.get(
        f"/api/v1/agreements/{test_agreement.id}/changes", headers=proposer_headers
    )
    assert any(d["id"] == change_id for d in own_view.json())

    # Opposite side does not see it until released.
    other_view = await client.get(
        f"/api/v1/agreements/{test_agreement.id}/changes", headers=opposing_headers
    )
    assert all(d["id"] != change_id for d in other_view.json())

    await client.post(
        f"/api/v1/agreements/{test_agreement.id}/changes/{change_id}/confirm",
        headers=proposer_headers,
    )
    await client.post(
        f"/api/v1/agreements/{test_agreement.id}/changes/{change_id}/release",
        headers=proposer_headers,
    )

    other_view = await client.get(
        f"/api/v1/agreements/{test_agreement.id}/changes", headers=opposing_headers
    )
    assert any(d["id"] == change_id for d in other_view.json())


async def test_party_cannot_reject_or_counter_own_proposal(
    client, db_session, test_agreement, test_org, test_user
):
    second_user, party_a, _ = await _setup_two_party_agreement(
        db_session, test_agreement, test_org, test_user
    )
    party_a_id = party_a.id
    proposer_headers = _headers_for(test_user.id)
    opposing_headers = _headers_for(second_user.id)

    proposed = await _propose(client, proposer_headers, test_agreement.id)
    change_id = proposed.json()["id"]
    assert proposed.json()["proposing_party_id"] == str(party_a_id)

    reject_same = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/changes/{change_id}/reject",
        headers=proposer_headers,
    )
    assert reject_same.status_code == 403

    counter_same = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/changes/{change_id}/counter",
        json={"change_type": "counter", "modifications": []},
        headers=proposer_headers,
    )
    assert counter_same.status_code == 403

    # Confirm + release, then the opposing party counters.
    await client.post(
        f"/api/v1/agreements/{test_agreement.id}/changes/{change_id}/confirm",
        headers=proposer_headers,
    )
    await client.post(
        f"/api/v1/agreements/{test_agreement.id}/changes/{change_id}/release",
        headers=proposer_headers,
    )

    counter_opposing = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/changes/{change_id}/counter",
        json={
            "change_type": "counter",
            "modifications": [
                {
                    "clause_identifier": "payment_terms",
                    "change_type": "modify",
                    "new_content": "Payment within 60 days",
                }
            ],
        },
        headers=opposing_headers,
    )
    assert counter_opposing.status_code == 201, counter_opposing.text
    assert counter_opposing.json()["change_type"] == "counter"