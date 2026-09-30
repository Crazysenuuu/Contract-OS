"""Integration tests for authentication endpoints."""
import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest.mark.integration
class TestAuthIntegration:
    """Test authentication flows with database."""

    @pytest_asyncio.fixture(autouse=True)
    def setup_client(self, client: AsyncClient):
        self.client = client

    async def test_register_user(self):
        """Test user registration creates user in database."""
        response = await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "newuser@test.com",
                "name": "New User",
                "password": "SecurePass123!",
                "date_of_birth": "1990-01-01",
            },
        )
        assert response.status_code in [200, 201]
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    async def test_register_duplicate_email(self):
        """Test duplicate email registration fails."""
        # First registration
        await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "duplicate@test.com",
                "name": "First User",
                "password": "SecurePass123!",
                "date_of_birth": "1990-01-01",
            },
        )
        # Second registration with same email
        response = await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "duplicate@test.com",
                "name": "Second User",
                "password": "SecurePass456!",
                "date_of_birth": "1990-01-01",
            },
        )
        assert response.status_code in [400, 409, 422]

    async def test_login_success(self):
        """Test successful login returns token."""
        # Register first
        await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "login@test.com",
                "name": "Login User",
                "password": "TestPass123!",
                "date_of_birth": "1990-01-01",
            },
        )
        # Login
        response = await self.client.post(
            "/api/v1/auth/login",
            json={
                "email": "login@test.com",
                "password": "TestPass123!",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data

    async def test_login_wrong_password(self):
        """Test login with wrong password fails."""
        # Register first
        await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "wrongpass@test.com",
                "name": "Wrong Pass User",
                "password": "CorrectPass123!",
                "date_of_birth": "1990-01-01",
            },
        )
        # Login with wrong password
        response = await self.client.post(
            "/api/v1/auth/login",
            json={
                "email": "wrongpass@test.com",
                "password": "WrongPass!",
            },
        )
        assert response.status_code == 401

    async def test_get_me_authenticated(self, test_user, auth_headers):
        """Test /me endpoint with valid token."""
        response = await self.client.get(
            "/api/v1/auth/me",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "test@example.com"
        assert data["name"] == "Test User"

    async def test_get_me_unauthenticated(self):
        """Test /me endpoint without token."""
        response = await self.client.get("/api/v1/auth/me")
        assert response.status_code in [401, 403]

    async def test_token_refresh(self):
        """Test token refresh flow."""
        # Register
        reg_response = await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "refresh@test.com",
                "name": "Refresh User",
                "password": "RefreshPass123!",
                "date_of_birth": "1990-01-01",
            },
        )
        tokens = reg_response.json()

        # Refresh token if endpoint exists
        if "refresh_token" in tokens:
            response = await self.client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": tokens["refresh_token"]},
            )
            assert response.status_code == 200
            assert "access_token" in response.json()
