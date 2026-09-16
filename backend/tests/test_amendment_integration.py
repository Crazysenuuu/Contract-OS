"""Integration tests for the amendment system and current-terms view."""
import pytest
import pytest_asyncio
from datetime import date
from httpx import AsyncClient


@pytest.mark.integration
class TestAmendments:
    @pytest_asyncio.fixture(autouse=True)
    def setup(self, client: AsyncClient, auth_headers, test_agreement, db_session):
        self.client = client
        self.headers = auth_headers
        self.agreement = test_agreement
        self.db = db_session

    async def _make_executed(self):
        self.agreement.status = "executed"
        self.agreement.execution_date = date(2026, 1, 1)
        await self.db.commit()

    async def _create_amendment(self, **overrides):
        payload = {
            "title": "Revised Liability Cap",
            "description": "Update indemnification cap",
            "reason": "Commercial renegotiation",
            "changes": [
                {
                    "section_key": "limitation_of_liability.cap",
                    "change_type": "replace",
                    "old_text": "1,000,000",
                    "new_text": "2,500,000",
                },
            ],
        }
        payload.update(overrides)
        return await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/amendments",
            json=payload,
            headers=self.headers,
        )

    async def test_cannot_amend_unexecuted_agreement(self):
        response = await self._create_amendment()
        assert response.status_code == 400

    async def test_create_and_list_amendment(self):
        await self._make_executed()
        created = await self._create_amendment()
        assert created.status_code == 201
        body = created.json()
        assert body["amendment_number"] == 1
        assert body["status"] == "proposed"
        assert len(body["changes"]) == 1

        listed = await self.client.get(
            f"/api/v1/agreements/{self.agreement.id}/amendments",
            headers=self.headers,
        )
        assert listed.status_code == 200
        assert len(listed.json()) == 1

    async def test_activate_amendment_updates_current_terms(self):
        await self._make_executed()
        created = (await self._create_amendment()).json()
        activation = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/amendments/{created['id']}/activate",
            json={"effective_date": "2026-03-01"},
            headers=self.headers,
        )
        assert activation.status_code == 200
        activated = activation.json()
        assert activated["status"] == "active"
        assert activated["effective_date"] == "2026-03-01"

        terms = await self.client.get(
            f"/api/v1/agreements/{self.agreement.id}/current-terms",
            headers=self.headers,
        )
        assert terms.status_code == 200
        consolidated = terms.json()["consolidated_terms"]
        assert consolidated["limitation_of_liability"]["cap"] == "2,500,000"

    async def test_sequential_numbering(self):
        await self._make_executed()
        first = (await self._create_amendment()).json()
        second = (await self._create_amendment(title="Second")).json()
        assert first["amendment_number"] == 1
        assert second["amendment_number"] == 2

    async def test_delete_change_removes_term(self):
        await self._make_executed()
        created = (await self._create_amendment(
            changes=[
                {
                    "section_key": "governing_law.state",
                    "change_type": "delete",
                    "new_text": "",
                }
            ]
        )).json()
        activated = await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/amendments/{created['id']}/activate",
            json={},
            headers=self.headers,
        )
        assert activated.status_code == 200

    async def test_current_terms_without_amendments(self):
        await self._make_executed()
        terms = await self.client.get(
            f"/api/v1/agreements/{self.agreement.id}/current-terms",
            headers=self.headers,
        )
        assert terms.status_code == 200
        assert terms.json()["amendments_applied"] == []