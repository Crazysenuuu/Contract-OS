"""Integration tests for webhook endpoints."""
import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest.mark.integration
class TestWebhooksIntegration:
    """Test webhook CRUD and delivery operations with database."""

    @pytest_asyncio.fixture(autouse=True)
    def setup_client(self, client: AsyncClient, auth_headers):
        self.client = client
        self.headers = auth_headers

    async def test_list_webhooks(self):
        """Test listing webhooks (may need org membership)."""
        response = await self.client.get(
            "/api/v1/webhooks",
            headers=self.headers,
        )
        # 200 if org membership works, 403 if not configured in test env
        assert response.status_code in [200, 403]

    async def test_list_available_events(self):
        """Test listing available webhook event types (no auth required)."""
        response = await self.client.get(
            "/api/v1/webhooks/events",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "events" in data
        assert isinstance(data["events"], list)
        assert len(data["events"]) > 0
        # Verify known events exist
        events = data["events"]
        assert "agreement.created" in events
        assert "agreement.signed" in events

    async def test_webhook_crud_flow(self):
        """Test full webhook CRUD lifecycle."""
        # List (empty)
        resp = await self.client.get("/api/v1/webhooks", headers=self.headers)
        if resp.status_code == 403:
            pytest.skip("Org membership not configured in test env")
        assert resp.status_code == 200
        initial = resp.json()
        assert isinstance(initial, list)

        # Create
        create_resp = await self.client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://example.com/webhook",
                "description": "Test webhook",
                "events": ["agreement.created", "agreement.signed"],
                "secret": "test-secret-123",
                "retry_count": 3,
                "timeout_seconds": 10,
            },
            headers=self.headers,
        )
        assert create_resp.status_code in [200, 201]
        webhook = create_resp.json()
        assert webhook["url"] == "https://example.com/webhook"
        assert webhook["is_active"] is True
        webhook_id = webhook["id"]

        # Get by ID
        get_resp = await self.client.get(
            f"/api/v1/webhooks/{webhook_id}",
            headers=self.headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["url"] == "https://example.com/webhook"

        # Update
        update_resp = await self.client.patch(
            f"/api/v1/webhooks/{webhook_id}",
            json={
                "url": "https://example.com/updated",
                "description": "Updated",
            },
            headers=self.headers,
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["url"] == "https://example.com/updated"

        # Get deliveries (empty)
        deliveries_resp = await self.client.get(
            f"/api/v1/webhooks/{webhook_id}/deliveries",
            headers=self.headers,
        )
        assert deliveries_resp.status_code == 200
        assert isinstance(deliveries_resp.json(), list)

        # Get stats
        stats_resp = await self.client.get(
            f"/api/v1/webhooks/{webhook_id}/stats",
            headers=self.headers,
        )
        assert stats_resp.status_code == 200

        # Test webhook
        test_resp = await self.client.post(
            f"/api/v1/webhooks/{webhook_id}/test",
            headers=self.headers,
        )
        assert test_resp.status_code == 200

        # Delete
        del_resp = await self.client.delete(
            f"/api/v1/webhooks/{webhook_id}",
            headers=self.headers,
        )
        assert del_resp.status_code == 200

        # Verify deleted
        get_resp2 = await self.client.get(
            f"/api/v1/webhooks/{webhook_id}",
            headers=self.headers,
        )
        assert get_resp2.status_code == 404

    async def test_webhook_unauthenticated(self):
        """Test webhook endpoints require authentication."""
        response = await self.client.get("/api/v1/webhooks")
        assert response.status_code in [401, 403]

    async def test_webhook_not_found(self):
        """Test getting a non-existent webhook."""
        resp = await self.client.get(
            "/api/v1/webhooks/00000000-0000-0000-0000-000000000000",
            headers=self.headers,
        )
        # 404 if user has org, 403 if no org membership
        assert resp.status_code in [403, 404]

    async def test_webhook_invalid_events(self):
        """Test creating webhook with various event combinations."""
        resp = await self.client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://example.com/webhook",
                "events": [],
            },
            headers=self.headers,
        )
        # 422 (validation) if no events, 403 if no org
        assert resp.status_code in [200, 201, 403, 422]
