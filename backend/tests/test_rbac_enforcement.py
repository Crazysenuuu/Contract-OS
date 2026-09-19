"""Spec §51 — route-level RBAC enforcement."""

import pytest
from sqlalchemy import select

from app.dependencies.rbac import PERMISSION_KEYS, has_permission, require_permission
from app.models.rbac import OrganizationMember, Permission, Role, RolePermission


def test_permission_catalogue_matches_seed():
    from seed_data import PERMISSION_CATALOG

    assert {p["key"] for p in PERMISSION_CATALOG} == set(PERMISSION_KEYS)


def test_unknown_permission_key_is_a_programming_error():
    with pytest.raises(ValueError):
        require_permission("agreement.fly")


@pytest.mark.asyncio
async def test_owner_role_holds_every_permission(db_session, test_org, test_user):
    for key in ("agreement.delete", "org.manage_roles", "template.manage"):
        assert await has_permission(db_session, user_id=test_user.id, org_id=test_org.id, permission=key)


@pytest.mark.asyncio
async def test_custom_role_requires_explicit_grant(db_session, test_org, test_user):
    reviewer = Role(organization_id=test_org.id, name="reviewer")
    db_session.add(reviewer)
    await db_session.flush()

    member = (
        await db_session.execute(
            select(OrganizationMember).where(
                OrganizationMember.user_id == test_user.id,
                OrganizationMember.organization_id == test_org.id,
            )
        )
    ).scalar_one()
    member.role_id = reviewer.id
    await db_session.flush()

    assert not await has_permission(
        db_session, user_id=test_user.id, org_id=test_org.id, permission="template.manage"
    )

    perm = (await db_session.execute(select(Permission).where(Permission.key == "template.manage"))).scalar_one_or_none()
    if perm is None:
        perm = Permission(key="template.manage", description="test")
        db_session.add(perm)
        await db_session.flush()
    db_session.add(RolePermission(role_id=reviewer.id, permission_id=perm.id))
    await db_session.flush()

    assert await has_permission(
        db_session, user_id=test_user.id, org_id=test_org.id, permission="template.manage"
    )
    assert not await has_permission(
        db_session, user_id=test_user.id, org_id=test_org.id, permission="agreement.delete"
    )


@pytest.mark.asyncio
async def test_template_mutation_forbidden_without_permission(client, db_session, test_org, test_user, auth_headers):
    viewer = Role(organization_id=test_org.id, name="viewer")
    db_session.add(viewer)
    await db_session.flush()
    member = (
        await db_session.execute(
            select(OrganizationMember).where(OrganizationMember.user_id == test_user.id)
        )
    ).scalar_one()
    member.role_id = viewer.id
    await db_session.commit()

    resp = await client.post(
        "/api/v1/templates",
        json={"name": "Blocked", "description": None, "is_system": False},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["required_permission"] == "template.manage"

    # Read access is unaffected.
    resp = await client.get("/api/v1/templates", headers=auth_headers)
    assert resp.status_code == 200
