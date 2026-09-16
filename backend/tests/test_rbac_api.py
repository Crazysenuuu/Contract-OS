"""Tests for the RBAC governance API (D2/D3).

Verifies org-scoped role management, permission assignment, and membership
role changes over the async session.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rbac import Permission, Role


async def _seed_permissions(db_session):
    perms = [
        Permission(key="clause.publish", description="Publish clauses"),
        Permission(key="agreement.export", description="Export agreements"),
        Permission(key="retention.manage", description="Manage retention"),
    ]
    for p in perms:
        db_session.add(p)
    await db_session.flush()


class TestRbacApi:
    async def test_list_permissions(self, client, auth_headers, db_session):
        await _seed_permissions(db_session)
        await db_session.commit()

        resp = await client.get("/api/v1/rbac/permissions", headers=auth_headers)
        assert resp.status_code == 200
        keys = {p["key"] for p in resp.json()}
        assert "clause.publish" in keys

    async def test_create_and_list_roles(
        self, client, auth_headers, db_session, test_org
    ):
        await _seed_permissions(db_session)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/rbac/roles",
            headers=auth_headers,
            json={
                "name": "legal-admin",
                "permission_keys": ["clause.publish", "agreement.export"],
            },
        )
        assert resp.status_code == 201, resp.text

        listing = await client.get("/api/v1/rbac/roles", headers=auth_headers)
        assert listing.status_code == 200
        roles = listing.json()
        assert any(r["name"] == "legal-admin" for r in roles)

        role_id = next(r["id"] for r in roles if r["name"] == "legal-admin")
        detail = await client.get(
            f"/api/v1/rbac/roles/{role_id}", headers=auth_headers
        )
        assert detail.status_code == 200
        assert {p["key"] for p in detail.json()["permissions"]} == {
            "clause.publish",
            "agreement.export",
        }

    async def test_role_duplicate_conflict(
        self, client, auth_headers, db_session, test_org
    ):
        await _seed_permissions(db_session)
        await db_session.commit()

        payload = {"name": "reviewer", "permission_keys": []}
        first = await client.post("/api/v1/rbac/roles", headers=auth_headers, json=payload)
        assert first.status_code == 201
        second = await client.post("/api/v1/rbac/roles", headers=auth_headers, json=payload)
        assert second.status_code == 409

    async def test_update_role_permissions(
        self, client, auth_headers, db_session, test_org
    ):
        await _seed_permissions(db_session)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/rbac/roles",
            headers=auth_headers,
            json={"name": "analyst", "permission_keys": ["agreement.export"]},
        )
        role_id = resp.json()["id"]

        updated = await client.put(
            f"/api/v1/rbac/roles/{role_id}/permissions",
            headers=auth_headers,
            json={"permission_keys": ["retention.manage"]},
        )
        assert updated.status_code == 200
        assert updated.json()["permissions"] == ["retention.manage"]

    async def test_owner_role_permissions_fixed(
        self, client, auth_headers, db_session, test_org
    ):
        result = await db_session.execute(
            select(Role).where(
                Role.organization_id == test_org.id,
                Role.name == "owner",
            )
        )
        owner = result.scalar_one_or_none()
        if owner is None:
            pytest.skip("No owner role seeded in this org")

        resp = await client.put(
            f"/api/v1/rbac/roles/{str(owner.id)}/permissions",
            headers=auth_headers,
            json={"permission_keys": []},
        )
        assert resp.status_code == 403

    async def test_member_role_management(
        self, client, auth_headers, db_session, test_org, test_user
    ):
        from app.core.security import hash_password
        from app.models.user import User

        await _seed_permissions(db_session)
        await db_session.commit()

        other = User(
            email="newhr@corp.com",
            name="New HR",
            password_hash=hash_password("TestPass123!"),
            status="active",
        )
        db_session.add(other)
        await db_session.commit()

        role = Role(organization_id=test_org.id, name="viewer")
        db_session.add(role)
        await db_session.commit()

        # Add the member under the viewer role.
        add = await client.post(
            "/api/v1/rbac/members",
            headers=auth_headers,
            json={"user_id": str(other.id), "role_id": str(role.id)},
        )
        assert add.status_code == 201, add.text
        member_id = add.json()["id"]

        # Change their role.
        admin_role = await db_session.execute(
            select(Role).where(
                Role.organization_id == test_org.id,
                Role.name == "owner",
            )
        )
        owner = admin_role.scalar_one_or_none()
        change = await client.put(
            f"/api/v1/rbac/members/{member_id}/role",
            headers=auth_headers,
            json={"role_id": str(owner.id)},
        )
        assert change.status_code == 200
        assert change.json()["role_id"] == str(owner.id)

        # Remove the member.
        remove = await client.delete(
            f"/api/v1/rbac/members/{member_id}", headers=auth_headers
        )
        assert remove.status_code == 200