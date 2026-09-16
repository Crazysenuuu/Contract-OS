"""Integration tests for the renewal system."""
import pytest
import pytest_asyncio
from datetime import date, timedelta
from httpx import AsyncClient


@pytest.mark.integration
class TestRenewals:
    @pytest_asyncio.fixture(autouse=True)
    def setup(self, client: AsyncClient, auth_headers, test_agreement, db_session):
        self.client = client
        self.headers = auth_headers
        self.agreement = test_agreement
        self.db = db_session

    async def _make_renewable(self):
        """Make the test agreement active + renewable and due for renewal."""
        self.agreement.status = "active"
        self.agreement.effective_date = date.today() - timedelta(days=400)
        self.agreement.execution_date = date.today() - timedelta(days=400)
        await self.db.flush()

        response = await self.client.put(
            f"/api/v1/agreements/{self.agreement.id}/renewal",
            json={
                "is_renewable": True,
                "auto_renew": True,
                "renewal_term_months": 12,
                "max_renewals": 5,
                "notice_period_days": 30,
            },
            headers=self.headers,
        )
        assert response.status_code == 200
        return response.json()

    async def test_get_or_create_renewal(self):
        response = await self.client.get(
            f"/api/v1/agreements/{self.agreement.id}/renewal",
            headers=self.headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "active"
        assert body["is_renewable"] is False
        assert body["notice_period_days"] == 30

    async def test_configure_renewal(self):
        config = await self._make_renewable()
        assert config["is_renewable"] is True
        assert config["auto_renew"] is True
        assert config["max_renewals"] == 5

    async def test_process_not_due(self):
        # Agreement never configured as renewable and no expiry -> not renewable.
        response = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/renewal/process",
            json={"force": True},
            headers=self.headers,
        )
        assert response.status_code == 200
        assert response.json()["action"] in ("not_renewable", "renewed")

    async def test_renewal_process_extends_term(self):
        config = await self._make_renewable()
        assert config["next_renewal_date"] is not None

        response = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/renewal/process",
            json={"force": True},
            headers=self.headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["action"] == "renewed"
        assert body["renewal_count"] == 1
        assert body["renewal"]["current_renewal_count"] == 1

        # The agreement's term was extended.
        self.db.expire(self.agreement)
        await self.db.refresh(self.agreement)
        assert self.agreement.expiry_date is not None

    async def test_non_renewal_notice_then_expire(self):
        config = await self._make_renewable()

        notice = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/renewal/notice",
            headers=self.headers,
        )
        assert notice.status_code == 200
        assert notice.json()["notice_given"] is True

        response = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/renewal/process",
            json={"force": True},
            headers=self.headers,
        )
        assert response.status_code == 200
        assert response.json()["action"] == "expired"

    async def test_schedule_reminders(self):
        await self._make_renewable()
        response = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/renewal/reminders/schedule",
            headers=self.headers,
        )
        assert response.status_code == 200
        assert response.json()["created"] >= 1

        reminders = await self.client.get(
            f"/api/v1/agreements/{self.agreement.id}/renewal/reminders",
            headers=self.headers,
        )
        assert reminders.status_code == 200
        assert len(reminders.json()) >= 1
        types = {r["reminder_type"] for r in reminders.json()}
        assert "notice_deadline" in types

    async def test_max_renewals_reached(self):
        config = await self._make_renewable()
        # Configure a hard cap of 2 renewals.
        capped = await self.client.put(
            f"/api/v1/agreements/{self.agreement.id}/renewal",
            json={"max_renewals": 2, "auto_renew": True},
            headers=self.headers,
        )
        assert capped.status_code == 200

        for _ in range(2):
            response = await self.client.post(
                f"/api/v1/agreements/{self.agreement.id}/renewal/process",
                json={"force": True},
                headers=self.headers,
            )
            assert response.status_code == 200
            assert response.json()["action"] == "renewed"

        # Third renewal hits the cap -> expired.
        response = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/renewal/process",
            json={"force": True},
            headers=self.headers,
        )
        assert response.status_code == 200
        assert response.json()["action"] == "expired"