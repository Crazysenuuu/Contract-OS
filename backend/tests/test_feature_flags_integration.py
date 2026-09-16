"""Integration tests for feature flag endpoints."""
import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest.mark.integration
class TestFeatureFlagsIntegration:
    """Test feature flag operations."""

    @pytest_asyncio.fixture(autouse=True)
    def setup_client(self, client: AsyncClient, auth_headers):
        self.client = client
        self.headers = auth_headers

    async def test_list_flags_empty(self):
        """Test listing feature flags."""
        response = await self.client.get(
            "/api/v1/feature-flags/flags",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    async def test_create_flag(self):
        """Test creating a feature flag."""
        response = await self.client.post(
            "/api/v1/feature-flags/flags",
            json={
                "name": "test_feature",
                "description": "A test feature flag",
                "flag_type": "boolean",
                "enabled": False,
            },
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test_feature"
        assert data["enabled"] is False

    async def test_create_percentage_flag(self):
        """Test creating a percentage-based feature flag."""
        response = await self.client.post(
            "/api/v1/feature-flags/flags",
            json={
                "name": "gradual_rollout",
                "description": "Gradual rollout feature",
                "flag_type": "percentage",
                "enabled": True,
                "percentage": 50.0,
            },
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "gradual_rollout"
        assert data["enabled"] is True

    async def test_get_flag(self):
        """Test getting a specific feature flag."""
        # Create
        await self.client.post(
            "/api/v1/feature-flags/flags",
            json={
                "name": "get_test_flag",
                "description": "Test",
                "flag_type": "boolean",
                "enabled": True,
            },
            headers=self.headers,
        )

        # Get
        response = await self.client.get(
            "/api/v1/feature-flags/flags/get_test_flag",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "get_test_flag"

    async def test_enable_disable_flag(self):
        """Test enabling and disabling a flag."""
        # Create disabled
        await self.client.post(
            "/api/v1/feature-flags/flags",
            json={
                "name": "toggle_flag",
                "description": "Test",
                "flag_type": "boolean",
                "enabled": False,
            },
            headers=self.headers,
        )

        # Enable
        response = await self.client.post(
            "/api/v1/feature-flags/flags/toggle_flag/enable",
            headers=self.headers,
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is True

        # Disable
        response = await self.client.post(
            "/api/v1/feature-flags/flags/toggle_flag/disable",
            headers=self.headers,
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False

    async def test_evaluate_flag(self):
        """Test evaluating a feature flag."""
        # Create enabled flag
        await self.client.post(
            "/api/v1/feature-flags/flags",
            json={
                "name": "eval_flag",
                "description": "Test",
                "flag_type": "boolean",
                "enabled": True,
            },
            headers=self.headers,
        )

        # Evaluate
        response = await self.client.post(
            "/api/v1/feature-flags/flags/eval_flag/evaluate",
            json={"user_id": "user-123"},
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "reason" in data

    async def test_check_flag_quick(self):
        """Test quick flag check."""
        # Create
        await self.client.post(
            "/api/v1/feature-flags/flags",
            json={
                "name": "quick_check",
                "description": "Test",
                "flag_type": "boolean",
                "enabled": True,
            },
            headers=self.headers,
        )

        # Quick check
        response = await self.client.get(
            "/api/v1/feature-flags/check/quick_check",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True

    async def test_set_user_override(self):
        """Test setting a user-specific override."""
        # Create disabled flag
        await self.client.post(
            "/api/v1/feature-flags/flags",
            json={
                "name": "override_flag",
                "description": "Test",
                "flag_type": "boolean",
                "enabled": False,
            },
            headers=self.headers,
        )

        # Set override for user
        response = await self.client.post(
            "/api/v1/feature-flags/flags/override_flag/overrides",
            json={"user_id": "user-456", "enabled": True},
            headers=self.headers,
        )
        assert response.status_code == 200

        # Verify override is set
        response = await self.client.get(
            "/api/v1/feature-flags/flags/override_flag/overrides",
            headers=self.headers,
        )
        assert response.status_code == 200
        overrides = response.json()["overrides"]
        assert "user-456" in overrides
        assert overrides["user-456"] is True

    async def test_delete_flag(self):
        """Test deleting a feature flag."""
        # Create
        await self.client.post(
            "/api/v1/feature-flags/flags",
            json={
                "name": "delete_flag",
                "description": "To delete",
                "flag_type": "boolean",
                "enabled": False,
            },
            headers=self.headers,
        )

        # Delete
        response = await self.client.delete(
            "/api/v1/feature-flags/flags/delete_flag",
            headers=self.headers,
        )
        assert response.status_code == 200

        # Verify deleted
        response = await self.client.get(
            "/api/v1/feature-flags/flags/delete_flag",
            headers=self.headers,
        )
        assert response.status_code == 404

    async def test_get_flag_stats(self):
        """Test getting flag statistics."""
        response = await self.client.get(
            "/api/v1/feature-flags/stats",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    async def test_export_flags(self):
        """Test exporting all flags."""
        response = await self.client.get(
            "/api/v1/feature-flags/export",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "flags" in data

    async def test_evaluate_all_flags(self):
        """Test evaluating all flags for a user."""
        response = await self.client.post(
            "/api/v1/feature-flags/evaluate-all",
            json={"user_id": "user-789"},
            headers=self.headers,
        )
        assert response.status_code == 200

    async def test_get_enabled_flags(self):
        """Test getting enabled flags for a user."""
        response = await self.client.get(
            "/api/v1/feature-flags/enabled",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "enabled_flags" in data
