"""Tests for multi-organization switching.

A user may hold active memberships in several organizations. Tenant
resolution refuses without an explicit ``X-Organization-Id`` in that case, so
these tests cover both halves of the contract: the endpoint that lets a
client discover its options, and the resolution that consumes the choice.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.organization import Organization
from app.models.rbac import OrganizationMember, Role

ORG_HEADER = "X-Organization-Id"


@pytest_asyncio.fixture
async def second_membership(db_session, test_user):
    """Add a second active organization for the same user."""
    org = Organization(
        name="Second Corp",
        slug="second-corp",
        country="US",
        timezone="America/New_York",
    )
    db_session.add(org)
    await db_session.flush()

    role = Role(organization_id=org.id, name="owner")
    db_session.add(role)
    await db_session.flush()

    db_session.add(
        OrganizationMember(
            organization_id=org.id,
            user_id=test_user.id,
            role_id=role.id,
            status="active",
        )
    )
    await db_session.commit()
    return org


async def test_lists_every_active_membership(
    client, auth_headers, test_org, second_membership
):
    response = await client.get(
        "/api/v1/organizations/me/memberships", headers=auth_headers
    )

    assert response.status_code == 200
    body = response.json()
    ids = {m["organization_id"] for m in body}
    assert ids == {str(test_org.id), str(second_membership.id)}
    assert all(m["role_id"] for m in body)


async def test_requires_authentication(client):
    response = await client.get("/api/v1/organizations/me/memberships")
    assert response.status_code in (401, 403)


async def test_suspended_membership_is_not_offered(
    client, auth_headers, test_org, second_membership, db_session, test_user
):
    """
    A revoked membership must not appear as a switch target.

    Hiding it client-side is not enough: the id would still be accepted by
    tenant resolution if it were not also excluded there.
    """
    member = (
        await db_session.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == second_membership.id,
                OrganizationMember.user_id == test_user.id,
            )
        )
    ).scalar_one()
    member.status = "suspended"
    await db_session.commit()

    response = await client.get(
        "/api/v1/organizations/me/memberships", headers=auth_headers
    )

    assert response.status_code == 200
    ids = {m["organization_id"] for m in response.json()}
    assert ids == {str(test_org.id)}


async def test_single_membership_needs_no_header(client, auth_headers, test_org):
    """Single-organization users are unaffected by the switching feature."""
    response = await client.get("/api/v1/organizations/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["id"] == str(test_org.id)


async def test_multi_membership_requires_header(
    client, auth_headers, test_org, second_membership
):
    response = await client.get("/api/v1/organizations/me", headers=auth_headers)

    assert response.status_code == 400
    assert ORG_HEADER in response.json()["detail"]


async def test_header_selects_the_organization(
    client, auth_headers, test_org, second_membership
):
    response = await client.get(
        "/api/v1/organizations/me",
        headers={**auth_headers, ORG_HEADER: str(second_membership.id)},
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(second_membership.id)


async def test_unknown_organization_is_refused(client, auth_headers, test_org):
    response = await client.get(
        "/api/v1/organizations/me",
        headers={**auth_headers, ORG_HEADER: str(uuid.uuid4())},
    )

    assert response.status_code == 403


async def test_malformed_header_is_a_client_error(client, auth_headers, test_org):
    response = await client.get(
        "/api/v1/organizations/me",
        headers={**auth_headers, ORG_HEADER: "not-a-uuid"},
    )

    assert response.status_code == 400


async def test_header_cannot_escape_the_user_s_own_memberships(
    client, auth_headers, test_org, db_session
):
    """
    An organization the caller does not belong to must stay unreachable even
    when its id is known.
    """
    from app.models.user import User

    other = User(
        email="outsider@example.com",
        password_hash="x",
        name="Outsider",
    )
    db_session.add(other)
    await db_session.flush()

    foreign_org = Organization(
        name="Foreign Corp",
        slug="foreign-corp",
        country="US",
        timezone="America/New_York",
    )
    db_session.add(foreign_org)
    await db_session.flush()
    foreign_role = Role(organization_id=foreign_org.id, name="owner")
    db_session.add(foreign_role)
    await db_session.flush()
    db_session.add(
        OrganizationMember(
            organization_id=foreign_org.id,
            user_id=other.id,
            role_id=foreign_role.id,
            status="active",
        )
    )
    await db_session.commit()

    response = await client.get(
        "/api/v1/organizations/me",
        headers={**auth_headers, ORG_HEADER: str(foreign_org.id)},
    )

    assert response.status_code == 403
    # And it must not be advertised as a switch target either.
    listed = await client.get(
        "/api/v1/organizations/me/memberships", headers=auth_headers
    )
    assert str(foreign_org.id) not in {m["organization_id"] for m in listed.json()}