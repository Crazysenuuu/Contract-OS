"""Integration tests for the termination system."""
import pytest
import pytest_asyncio
from datetime import date, timedelta
from httpx import AsyncClient


@pytest.mark.integration
class TestTerminations:
    @pytest_asyncio.fixture(autouse=True)
    def setup(self, client: AsyncClient, auth_headers, test_agreement, db_session):
        self.client = client
        self.headers = auth_headers
        self.agreement = test_agreement
        self.db = db_session

    async def _make_active(self):
        self.agreement.status = "active"
        self.agreement.execution_date = date(2026, 1, 1)
        await self.db.commit()

    async def _initiate(self, **overrides):
        payload = {
            "reason_code": "for_convenience",
            "reason_detail": "Business realignment",
            "notice_period_days": 30,
        }
        payload.update(overrides)
        return await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/terminations",
            json=payload,
            headers=self.headers,
        )

    async def test_cannot_terminate_draft(self):
        response = await self._initiate()
        assert response.status_code == 400

    async def test_initiate_termination(self):
        await self._make_active()
        response = await self._initiate()
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "draft"
        assert body["reason_code"] == "for_convenience"

    async def test_initiate_with_cure_period(self):
        await self._make_active()
        response = await self._initiate(
            reason_code="material_breach",
            cure_required=True,
            cure_period_days=30,
        )
        assert response.status_code == 201
        assert response.json()["cure_required"] is True

    async def test_issue_notice_and_cure_flow(self):
        await self._make_active()
        term = (await self._initiate(cure_required=True, cure_period_days=30)).json()

        notice = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/terminations/{term['id']}/notice",
            json={
                "notice_date": "2026-05-01",
                "evidence": {"method": "email", "delivered_at": "2026-05-01T09:00:00Z"},
            },
            headers=self.headers,
        )
        assert notice.status_code == 200
        assert notice.json()["status"] == "in_cure"
        assert notice.json()["cure_deadline"] is not None

        cure = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/terminations/{term['id']}/cure",
            json={"cured": True},
            headers=self.headers,
        )
        assert cure.status_code == 200
        assert cure.json()["cured"] is True

    async def test_complete_termination_changes_agreement_status(self):
        await self._make_active()
        term = (await self._initiate()).json()

        notice = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/terminations/{term['id']}/notice",
            json={"notice_date": "2026-05-01", "evidence": {"method": "email"}},
            headers=self.headers,
        )
        assert notice.status_code == 200

        complete = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/terminations/{term['id']}/complete",
            json={"effective_date": "2026-06-01"},
            headers=self.headers,
        )
        assert complete.status_code == 200
        body = complete.json()
        assert body["status"] == "effective"
        assert body["effective_date"] == "2026-06-01"

        obligations = await self.client.get(
            f"/api/v1/agreements/{self.agreement.id}/terminations/{term['id']}/obligations",
            headers=self.headers,
        )
        assert obligations.status_code == 200
        ob_types = {o["obligation_type"] for o in obligations.json()}
        assert "pay_accrued" in ob_types

        # The agreement itself must now be terminated.
        self.db.expire(self.agreement)
        await self.db.refresh(self.agreement)
        assert self.agreement.status == "terminated"

    async def test_cancel_termination(self):
        await self._make_active()
        term = (await self._initiate()).json()
        cancel = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/terminations/{term['id']}/cancel",
            headers=self.headers,
        )
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"

    async def test_termination_listing(self):
        await self._make_active()
        await self._initiate()
        listing = await self.client.get(
            f"/api/v1/agreements/{self.agreement.id}/terminations",
            headers=self.headers,
        )
        assert listing.status_code == 200
        assert len(listing.json()) == 1

    async def test_blocked_by_outstanding_obligation_then_force(self):
        """Outstanding obligations block completion; force overrides."""
        from app.models.obligation import Obligation
        from datetime import date

        await self._make_active()
        self.db.add(Obligation(
            agreement_id=self.agreement.id,
            owner_party="Acme Corp",
            description="Final invoice",
            obligation_type="pay_accrued",
            status="overdue",
        ))
        await self.db.commit()

        term = (await self._initiate()).json()
        notice = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/terminations/{term['id']}/notice",
            json={"notice_date": "2026-05-01", "evidence": {"method": "email"}},
            headers=self.headers,
        )
        assert notice.status_code == 200

        # Blocked without force.
        blocked = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/terminations/{term['id']}/complete",
            json={"effective_date": "2026-06-01", "force": False},
            headers=self.headers,
        )
        assert blocked.status_code == 400

        # Succeeds with force.
        forced = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/terminations/{term['id']}/complete",
            json={"effective_date": "2026-06-01", "force": True},
            headers=self.headers,
        )
        assert forced.status_code == 200
        assert forced.json()["obligations_check"]["resolved"] is True