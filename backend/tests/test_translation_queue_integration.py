"""Integration tests for translation queue endpoints."""
import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest.mark.integration
class TestTranslationQueueIntegration:
    """Test translation queue operations with database."""

    @pytest_asyncio.fixture(autouse=True)
    def setup_client(self, client: AsyncClient, auth_headers, test_org):
        # test_org: /templates resolves the caller's organization, which the
        # bare auth_headers user has none of.
        self.client = client
        self.headers = auth_headers

    async def test_stats_requires_auth(self):
        """Test queue stats requires authentication."""
        response = await self.client.get("/api/v1/translation-queue/stats")
        assert response.status_code in [401, 403]

    async def test_list_items_requires_auth(self):
        """Test listing queue items requires authentication."""
        response = await self.client.get("/api/v1/translation-queue/items")
        assert response.status_code in [401, 403]

    async def test_enqueue_requires_auth(self):
        """Test enqueue requires authentication."""
        response = await self.client.post(
            "/api/v1/translation-queue/enqueue",
            json={
                "source_type": "agreement",
                "source_id": "test-123",
                "target_language": "si",
                "source_content": "Test content",
            },
        )
        assert response.status_code in [401, 403]

    async def test_process_next_requires_auth(self):
        """Test process next requires authentication."""
        response = await self.client.post(
            "/api/v1/translation-queue/process",
        )
        assert response.status_code in [401, 403]

    async def test_worker_heartbeat_requires_auth(self):
        """Test worker heartbeat requires authentication."""
        response = await self.client.post(
            "/api/v1/translation-queue/workers/test-worker/heartbeat",
        )
        assert response.status_code in [401, 403]

    async def test_queue_stats(self):
        """Test getting queue statistics."""
        resp = await self.client.get(
            "/api/v1/translation-queue/stats",
            headers=self.headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "total" in body
        assert "by_status" in body

    async def test_list_items(self):
        """Test listing queue items."""
        resp = await self.client.get(
            "/api/v1/translation-queue/items",
            headers=self.headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_list_workers(self):
        """Test listing translation workers."""
        resp = await self.client.get(
            "/api/v1/translation-queue/workers",
            headers=self.headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_list_templates(self):
        """Test listing translation templates."""
        resp = await self.client.get(
            "/api/v1/translation-queue/templates",
            headers=self.headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
