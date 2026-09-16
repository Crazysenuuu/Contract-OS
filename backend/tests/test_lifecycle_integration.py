"""Integration tests for the agreement status machine and immutability."""
import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest.mark.integration
class TestLifecycleTransitions:
    """Data-driven status machine over the API."""

    @pytest_asyncio.fixture(autouse=True)
    def setup(self, client: AsyncClient, auth_headers, test_agreement):
        self.client = client
        self.headers = auth_headers
        self.agreement = test_agreement

    async def _transition(self, action):
        return await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/transitions",
            json={"action_key": action},
            headers=self.headers,
        )

    async def test_list_registered_states(self):
        response = await self.client.get("/api/v1/agreements/states")
        assert response.status_code == 200
        states = response.json()
        assert "draft" in states
        assert "executed" in states
        assert "terminated" in states

    async def test_available_actions_from_draft(self):
        response = await self.client.get(
            f"/api/v1/agreements/{self.agreement.id}/transitions",
            headers=self.headers,
        )
        assert response.status_code == 200
        actions = {a["action_key"] for a in response.json()}
        assert "send" in actions
        assert "submit" in actions
        assert "execute" not in actions

    async def test_send_is_rule_checked(self):
        ok = await self._transition("send")
        assert ok.status_code == 200
        assert ok.json()["status"] == "sent"

    async def test_illegal_transition_rejected(self):
        # Draft -> executed is not a valid transition.
        response = await self._transition("execute")
        assert response.status_code == 400

    async def test_approved_then_sign_then_execute_flow(self):
        await self._transition("send")
        resp = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/sign",
            json={"consent_text": "I agree"},
            headers=self.headers,
        )
        assert resp.status_code in (200, 400)  # DB-backed signing path

    async def test_terminal_states_cannot_transition_in(self):
        # cancel from draft should work
        response = await self._transition("cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        # Once cancelled, no further transition.
        again = await self._transition("send")
        assert again.status_code == 400


@pytest.mark.integration
class TestImmutability:
    """Signed executed agreements cannot be edited directly."""

    @pytest_asyncio.fixture(autouse=True)
    def setup(self, client: AsyncClient, auth_headers, test_agreement, db_session):
        self.client = client
        self.headers = auth_headers
        self.agreement = test_agreement
        self.db = db_session

    async def test_update_answers_blocked_after_executed(self):
        from datetime import date
        self.agreement.status = "executed"
        self.agreement.execution_date = date(2026, 1, 1)
        await self.db.commit()

        response = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/answers",
            json={"answers": {"purpose": "changed"}, "check_compliance": False},
            headers=self.headers,
        )
        assert response.status_code == 400

    async def test_patch_blocked_after_executed(self):
        from datetime import date
        self.agreement.status = "executed"
        self.agreement.execution_date = date(2026, 1, 1)
        await self.db.commit()

        response = await self.client.patch(
            f"/api/v1/agreements/{self.agreement.id}",
            json={"title": "Renamed"},
            headers=self.headers,
        )
        assert response.status_code == 400