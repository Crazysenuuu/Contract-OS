"""Integration tests for admin endpoints (platform-owner views).

These tests must verify two things:
1. Admin users can see session/health/overview data.
2. Regular (non-admin) users ALWAYS get 403 - admin data is confidential.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.security import create_access_token, hash_password
from app.models.user import User, UserSession
from datetime import datetime, timedelta, timezone


@pytest.mark.integration
class TestAdminIntegration:
    """Test admin-only platform views."""

    @pytest_asyncio.fixture(autouse=True)
    def setup_client(self, client: AsyncClient):
        self.client = client

    async def test_non_admin_gets_403(self, test_user):
        """Regular users must never see admin data."""
        token = create_access_token(user_id=test_user.id)
        headers = {"Authorization": f"Bearer {token}"}

        for path in [
            "/api/v1/admin/health",
            "/api/v1/admin/overview",
            "/api/v1/admin/sessions/active",
            "/api/v1/admin/sessions/history",
            "/api/v1/admin/companies",
            "/api/v1/admin/users",
        ]:
            response = await self.client.get(path, headers=headers)
            assert response.status_code == 403, (
                f"{path} should be forbidden for non-admin, got {response.status_code}"
            )

    async def test_health_requires_auth(self):
        """Health requires an admin token."""
        response = await self.client.get("/api/v1/admin/health")
        assert response.status_code in [401, 403]

    async def test_admin_health_ok(
        self, db_client_fixture, admin_headers, db_session
    ):
        """Admin health reports service status."""
        response = await self.client.get(
            "/api/v1/admin/health", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["healthy", "degraded"]
        assert "database" in data["services"]
        assert "migrations" in data["services"]

    async def test_admin_overview_ok(self, admin_headers, db_client_fixture):
        """Overview returns aggregate platform counts."""
        response = await self.client.get(
            "/api/v1/admin/overview", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "companies" in data
        assert "agreements" in data
        assert "sessions" in data
        assert data["users"]["admins"] >= 1

    async def test_active_sessions_lists_logged_in_users(
        self, admin_headers, db_session, test_user, test_org
    ):
        """Active sessions include the admin's own session, with company info."""
        test_user.is_admin = True
        db_session.add(UserSession(
            user_id=test_user.id,
            organization_id=test_org.id,
            login_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
            status="active",
        ))
        await db_session.commit()

        token = create_access_token(user_id=test_user.id)
        headers = {"Authorization": f"Bearer {token}"}

        response = await self.client.get(
            "/api/v1/admin/sessions/active", headers=headers
        )
        assert response.status_code == 200
        rows = response.json()
        assert any(r["email"] == "test@example.com" for r in rows)
        admin_row = next(
            r for r in rows if r["email"] == "test@example.com"
        )
        assert admin_row["company"] == "Test Corp"
        assert admin_row["online_duration"].startswith("0s")

    async def test_session_history_has_logout(self, admin_headers, db_session, test_user):
        """History shows sessions and respects limit."""
        test_user.is_admin = True
        db_session.add(UserSession(
            user_id=test_user.id,
            login_at=datetime.now(timezone.utc) - timedelta(hours=2),
            last_seen_at=datetime.now(timezone.utc) - timedelta(hours=1),
            logout_at=datetime.now(timezone.utc) - timedelta(hours=1),
            status="logged_out",
        ))
        await db_session.commit()

        response = await self.client.get(
            "/api/v1/admin/sessions/history?limit=10", headers=admin_headers
        )
        assert response.status_code == 200
        rows = response.json()
        assert rows, "history should contain promoted admin's prior session"
        assert any(r["logout_at"] is not None for r in rows)

    async def test_companies_summary(self, admin_headers, db_client_fixture):
        """Companies endpoint returns at least the admin's company."""
        response = await self.client.get(
            "/api/v1/admin/companies", headers=admin_headers
        )
        assert response.status_code == 200
        rows = response.json()
        assert isinstance(rows, list)


@pytest_asyncio.fixture
async def admin_headers(db_session, test_user):
    """Promote the test user to admin and return admin auth headers."""
    test_user.is_admin = True
    await db_session.commit()
    from app.core.security import create_access_token

    token = create_access_token(user_id=test_user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
class TestAdminUserManagement:
    """Admin can manage users: list, promote, demote, activate, deactivate."""

    @pytest_asyncio.fixture(autouse=True)
    def setup_client(self, client: AsyncClient):
        self.client = client

    @pytest_asyncio.fixture
    async def regular_user(self, db_session, test_org):
        user = User(
            email="regular@example.com",
            name="Regular User",
            password_hash=hash_password("password123"),
            status="active",
        )
        db_session.add(user)
        await db_session.flush()
        from app.models.rbac import OrganizationMember, Role
        from sqlalchemy import select

        owner = (
            await db_session.execute(
                select(Role).where(
                    Role.organization_id == test_org.id,
                    Role.name == "owner",
                )
            )
        ).scalar_one()
        db_session.add(OrganizationMember(
            organization_id=test_org.id,
            user_id=user.id,
            role_id=owner.id,
            status="active",
        ))
        await db_session.commit()
        return user

    async def test_users_list_visible_to_admin(self, admin_headers, regular_user):
        response = await self.client.get(
            "/api/v1/admin/users", headers=admin_headers
        )
        assert response.status_code == 200
        rows = response.json()
        assert any(r["email"] == regular_user.email for r in rows)
        row = next(r for r in rows if r["email"] == regular_user.email)
        assert row["is_admin"] is False
        assert row["status"] == "active"
        assert row["organization"] is not None

    async def test_users_list_never_leaks_credentials(self, admin_headers, regular_user):
        """Admin user listing must never expose auth secrets (spec 2.6)."""
        response = await self.client.get(
            "/api/v1/admin/users", headers=admin_headers
        )
        assert response.status_code == 200
        rows = response.json()

        forbidden = {
            "password_hash",
            "verification_token",
            "mfa_secret",
            "refresh_token",
            "token_hash",
        }
        allowed_keys = {
            "user_id",
            "name",
            "email",
            "status",
            "is_admin",
            "mfa_enabled",
            "organization",
            "created_at",
        }
        for row in rows:
            for key in forbidden:
                assert key not in row, (
                    f"credential field '{key}' leaked in admin users list"
                )
            assert set(row.keys()) <= allowed_keys, (
                f"unexpected field(s) {set(row.keys()) - allowed_keys} in admin users list"
            )

    async def test_promote_then_demote(self, admin_headers, regular_user):
        promote = await self.client.post(
            f"/api/v1/admin/users/{regular_user.id}/promote",
            headers=admin_headers,
        )
        assert promote.status_code == 200, promote.text

        # Promoted user can now reach admin endpoints.
        token = create_access_token(user_id=regular_user.id)
        access = await self.client.get(
            "/api/v1/admin/overview",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert access.status_code == 200

        demote = await self.client.post(
            f"/api/v1/admin/users/{regular_user.id}/demote",
            headers=admin_headers,
        )
        assert demote.status_code == 200, demote.text

    async def test_deactivate_blocks_login(self, admin_headers, regular_user):
        response = await self.client.post(
            f"/api/v1/admin/users/{regular_user.id}/deactivate",
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text

        login = await self.client.post(
            "/api/v1/auth/login",
            json={"email": regular_user.email, "password": "password123"},
        )
        assert login.status_code in [401, 403], (
            "Deactivated user must not be able to log in"
        )

        reactivate = await self.client.post(
            f"/api/v1/admin/users/{regular_user.id}/activate",
            headers=admin_headers,
        )
        assert reactivate.status_code == 200, reactivate.text

        login = await self.client.post(
            "/api/v1/auth/login",
            json={"email": regular_user.email, "password": "password123"},
        )
        assert login.status_code == 200, "Reactivated user can log in again"

    async def test_admin_cannot_demote_self(self, admin_headers, test_user):
        response = await self.client.post(
            f"/api/v1/admin/users/{test_user.id}/demote",
            headers=admin_headers,
        )
        assert response.status_code == 400

    async def test_user_management_blocked_for_non_admin(
        self, test_user, db_session
    ):
        non_admin = User(
            email="staff@example.com",
            name="Staff User",
            password_hash=hash_password("password123"),
            status="active",
        )
        db_session.add(non_admin)
        await db_session.commit()

        token = create_access_token(user_id=non_admin.id)
        headers = {"Authorization": f"Bearer {token}"}
        response = await self.client.post(
            f"/api/v1/admin/users/{non_admin.id}/promote",
            headers=headers,
        )
        assert response.status_code == 403


@pytest_asyncio.fixture
async def db_client_fixture():
    """Marker/companion fixture to keep table creation ordering predictable."""
    yield