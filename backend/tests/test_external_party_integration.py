"""Integration tests for external party review endpoints."""
import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest.mark.integration
class TestExternalPartyIntegration:
    """Test external party review flow with database."""

    @pytest_asyncio.fixture(autouse=True)
    def setup_client(self, client: AsyncClient, auth_headers, test_agreement):
        self.client = client
        self.headers = auth_headers
        self.agreement = test_agreement

    async def test_list_external_parties_empty(self):
        """Test listing external parties when none exist."""
        response = await self.client.get(
            f"/api/v1/agreements/{self.agreement.id}/external-parties",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    async def test_review_with_invalid_token(self):
        """Test reviewing agreement with invalid token."""
        response = await self.client.get(
            "/api/v1/review/invalid-token-12345",
        )
        assert response.status_code == 404

    async def test_comment_with_invalid_token(self):
        """Test adding comment with invalid token."""
        response = await self.client.post(
            "/api/v1/review/invalid-token/comment",
            json={"content": "Test comment"},
        )
        assert response.status_code == 404

    async def test_accept_with_invalid_token(self):
        """Test accepting with invalid token."""
        response = await self.client.post(
            "/api/v1/review/invalid-token/accept",
        )
        assert response.status_code == 404

    async def test_reject_with_invalid_token(self):
        """Test rejecting with invalid token."""
        response = await self.client.post(
            "/api/v1/review/invalid-token/reject",
        )
        assert response.status_code == 404

    async def test_sign_with_invalid_token(self):
        """Test signing with invalid token."""
        response = await self.client.post(
            "/api/v1/review/invalid-token/sign",
            json={"consent_text": "I agree to the terms"},
        )
        assert response.status_code == 404

    async def test_external_party_unauthenticated(self):
        """Test external party endpoints require auth for internal routes."""
        response = await self.client.get(
            f"/api/v1/agreements/{self.agreement.id}/external-parties",
        )
        assert response.status_code in [401, 403]
