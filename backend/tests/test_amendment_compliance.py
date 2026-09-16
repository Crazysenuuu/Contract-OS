"""Amendment compliance on activation (spec gap: stale-base conflicts + obligation relink).

Verifies that:
- Activating an amendment whose old_text no longer matches the consolidated
  current terms is rejected with HTTP 400 (prevents silently overwriting
  newer terms).
- Activating an amendment supersedes active obligations sourced from the
  clauses the amendment touches, wiring supersede_obligation into the flow.
- Unrelated obligations are left untouched.
"""

import pytest
import pytest_asyncio
from datetime import date
from httpx import AsyncClient


@pytest.mark.integration
class TestAmendmentCompliance:
    @pytest_asyncio.fixture(autouse=True)
    async def setup(self, client, auth_headers, test_agreement, db_session):
        self.client = client
        self.headers = auth_headers
        self.agreement = test_agreement
        self.db = db_session
        self.agreement.data = {
            "limitation_of_liability": {"cap": "1,000,000"},
            "confidentiality": {"term": "12 months"},
        }
        self.agreement.status = "executed"
        self.agreement.execution_date = date(2026, 1, 1)
        self.db.add(self.agreement)
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

    async def _activate(self, amendment_id):
        return await self.client.post(
            f"/api/v1/agreements/{self.agreement.id}/amendments/{amendment_id}/activate",
            json={"effective_date": "2026-03-01"},
            headers=self.headers,
        )

    async def _add_obligation(self, clause_identifier: str):
        from app.services.obligation_service import ObligationService

        service = ObligationService(self.db)
        obligation = await service.create_obligation(
            agreement_id=self.agreement.id,
            owner_party="counterparty",
            description=f"Obligation for {clause_identifier}",
            obligation_type="payment",
            amount="1,000,000",
            due_date=date(2026, 6, 30),
            clause_identifier=clause_identifier,
        )
        await self.db.commit()
        await self.db.refresh(obligation)
        return obligation

    async def test_activate_rejects_stale_base_conflict(self):
        created = await self._create_amendment(
            changes=[
                {
                    "section_key": "limitation_of_liability.cap",
                    "change_type": "replace",
                    "old_text": "500,000",
                    "new_text": "2,500,000",
                }
            ]
        )
        assert created.status_code == 201
        activation = await self._activate(created.json()["id"])
        assert activation.status_code == 400
        assert "Conflict on section 'limitation_of_liability.cap'" in activation.json()["detail"]

    async def test_activate_supersedes_obligations_of_amended_clause(self):
        await self._add_obligation("limitation_of_liability.cap")

        created = (await self._create_amendment()).json()
        activation = await self._activate(created["id"])
        assert activation.status_code == 200, activation.text

        from sqlalchemy import select

        from app.models.obligation import Obligation

        result = await self.db.execute(
            select(Obligation).where(
                Obligation.agreement_id == self.agreement.id
            )
        )
        obligation = result.scalars().one()
        assert obligation.clause_identifier == "limitation_of_liability.cap"
        assert obligation.status == "SUPERSEDED"

    async def test_activate_keeps_unrelated_obligations(self):
        await self._add_obligation("confidentiality.term")

        created = (await self._create_amendment()).json()
        activation = await self._activate(created["id"])
        assert activation.status_code == 200, activation.text

        from sqlalchemy import select

        from app.models.obligation import Obligation

        result = await self.db.execute(
            select(Obligation).where(
                Obligation.agreement_id == self.agreement.id
            )
        )
        obligation = result.scalars().one()
        assert obligation.status != "SUPERSEDED"

    async def test_activate_allows_clean_base(self):
        created = (await self._create_amendment()).json()
        activation = await self._activate(created["id"])
        assert activation.status_code == 200, activation.text
        assert activation.json()["status"] == "active"