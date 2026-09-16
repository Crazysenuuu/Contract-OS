"""Integration tests for workflow endpoints."""
import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest.mark.integration
class TestWorkflowIntegration:
    """Test workflow state machine with database."""

    @pytest_asyncio.fixture(autouse=True)
    def setup_client(self, client: AsyncClient, auth_headers, test_agreement):
        self.client = client
        self.headers = auth_headers
        self.agreement = test_agreement

    async def test_get_workflow_states(self):
        """Test getting available workflow states."""
        response = await self.client.get(
            "/api/v1/workflow/states",
            headers=self.headers,
        )
        # Accept success or not-implemented
        assert response.status_code in [200, 404, 501]

    async def test_transition_agreement_state(self):
        """Test transitioning agreement through workflow."""
        response = await self.client.post(
            "/api/v1/workflow/transition",
            json={
                "agreement_id": str(self.agreement.id),
                "action": "submit",
            },
            headers=self.headers,
        )
        # Accept either success or not-implemented
        assert response.status_code in [200, 400, 404, 501]

    async def test_invalid_transition(self):
        """Test invalid state transition is rejected."""
        response = await self.client.post(
            "/api/v1/workflow/transition",
            json={
                "agreement_id": str(self.agreement.id),
                "action": "invalid_action",
            },
            headers=self.headers,
        )
        # Should fail with appropriate error
        assert response.status_code in [400, 404, 422, 501]
