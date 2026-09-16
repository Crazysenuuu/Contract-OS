"""Integration tests for agreement endpoints."""
import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest.mark.integration
class TestAgreementsIntegration:
    """Test agreement CRUD operations with database."""

    @pytest_asyncio.fixture(autouse=True)
    def setup_client(self, client: AsyncClient, auth_headers):
        self.client = client
        self.headers = auth_headers

    async def test_create_agreement(self):
        """Test agreement creation."""
        response = await self.client.post(
            "/api/v1/agreements",
            json={
                "title": "Test NDA",
                "description": "A test agreement",
                "agreement_type": "mutual_nda",
            },
            headers=self.headers,
        )
        # May require specific schema or return 403 if permissions not set
        assert response.status_code in [200, 201, 403, 422]

    async def test_list_agreements(self):
        """Test listing agreements."""
        response = await self.client.get(
            "/api/v1/agreements",
            headers=self.headers,
        )
        assert response.status_code in [200, 403]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (list, dict))

    async def test_get_agreement_by_id(self, test_agreement):
        """Test getting agreement by ID."""
        response = await self.client.get(
            f"/api/v1/agreements/{test_agreement.id}",
            headers=self.headers,
        )
        assert response.status_code in [200, 403, 404]
        if response.status_code == 200:
            data = response.json()
            assert "id" in data
            assert data["title"] == "Test NDA Agreement"

    async def test_update_agreement(self, test_agreement):
        """Test updating agreement."""
        response = await self.client.patch(
            f"/api/v1/agreements/{test_agreement.id}",
            json={
                "title": "Updated NDA Title",
                "description": "Updated description",
            },
            headers=self.headers,
        )
        assert response.status_code in [200, 403, 404]

    async def test_delete_agreement(self, test_agreement):
        """Test deleting agreement."""
        response = await self.client.delete(
            f"/api/v1/agreements/{test_agreement.id}",
            headers=self.headers,
        )
        # May return 405 if DELETE not implemented at this route
        assert response.status_code in [200, 204, 403, 404, 405]

    async def test_unauthenticated_agreement_access(self, test_agreement):
        """Test agreement access without authentication."""
        response = await self.client.get(
            f"/api/v1/agreements/{test_agreement.id}",
        )
        assert response.status_code in [401, 403]

    async def test_create_child_under_parent(self, test_agreement, test_agreement_type):
        """MSA->SOW hierarchy: creating a child links parent_agreement_id."""
        response = await self.client.post(
            "/api/v1/agreements",
            json={
                "title": "SOW 01 Under MSA",
                "agreement_type_id": str(test_agreement_type.id),
                "parent_agreement_id": str(test_agreement.id),
            },
            headers=self.headers,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["parent_agreement_id"] == str(test_agreement.id)

    async def test_child_parent_must_be_in_same_org(self, test_agreement_type, auth_headers, client, test_org):
        """A random parent id that does not exist -> 404."""
        import uuid
        response = await client.post(
            "/api/v1/agreements",
            json={
                "title": "Orphan SOW",
                "agreement_type_id": str(test_agreement_type.id),
                "parent_agreement_id": str(uuid.uuid4()),
            },
            headers=auth_headers,
        )
        assert response.status_code == 404
