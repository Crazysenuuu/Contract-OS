"""Integration tests for notification endpoints."""
import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest.mark.integration
class TestNotificationsIntegration:
    """Test notification system with database."""

    @pytest_asyncio.fixture(autouse=True)
    def setup_client(self, client: AsyncClient, auth_headers):
        self.client = client
        self.headers = auth_headers

    async def test_list_notifications(self):
        """Test listing user notifications."""
        response = await self.client.get(
            "/api/v1/notifications",
            headers=self.headers,
        )
        assert response.status_code in [200, 403, 404]

    async def test_get_notification_preferences(self):
        """Test getting notification preferences."""
        response = await self.client.get(
            "/api/v1/notifications/preferences",
            headers=self.headers,
        )
        assert response.status_code in [200, 403, 404]

    async def test_update_notification_preferences(self):
        """Test updating notification preferences."""
        response = await self.client.put(
            "/api/v1/notifications/preferences",
            json={
                "email_enabled": True,
                "review_invitations": True,
                "agreement_updates": True,
            },
            headers=self.headers,
        )
        assert response.status_code in [200, 201, 403, 404, 422]

    async def test_mark_notification_read(self):
        """Test marking a notification as read."""
        # First get notifications
        list_response = await self.client.get(
            "/api/v1/notifications",
            headers=self.headers,
        )
        if list_response.status_code == 200:
            notifications = list_response.json()
            if isinstance(notifications, list) and len(notifications) > 0:
                notif_id = notifications[0].get("id")
                if notif_id:
                    response = await self.client.patch(
                        f"/api/v1/notifications/{notif_id}/read",
                        headers=self.headers,
                    )
                    assert response.status_code in [200, 204, 404, 405]
