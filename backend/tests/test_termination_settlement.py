"""Termination settlement tests (spec 1.17 §15-16).

Covers the settlement checklist lifecycle:

- A checklist is generated at initiation with required vs recommended items.
- Required-by-agreement items trace to their source obligation rows.
- Surviving clauses are required but never block completion.
- Outstanding financial position is computed with integer minor units
  (never floating-point).
- Open required items block completion; resolving them unblocks; force
  waives them and records the fact.
- Settling requires every blocking item closed.
"""
import pytest
import pytest_asyncio
from datetime import date
from httpx import AsyncClient

from app.models.obligation import Obligation
from app.models.termination import (
    AgreementTermination,
    TerminationSettlement,
)


@pytest.mark.integration
class TestTerminationSettlement:
    @pytest_asyncio.fixture(autouse=True)
    def setup(self, client: AsyncClient, auth_headers, test_agreement, db_session):
        self.client = client
        self.headers = auth_headers
        self.agreement = test_agreement
        self.db = db_session

    def _term_url(self, term_id: str) -> str:
        return f"/api/v1/agreements/{self.agreement.id}/terminations/{term_id}"

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

    # ------------------------------------------------------------------
    # Checklist generation
    # ------------------------------------------------------------------

    async def test_settlement_generated_at_initiation(self):
        await self._make_active()
        resp = await self._initiate()
        assert resp.status_code == 201, resp.text
        term_id = resp.json()["id"]

        listing = await self.client.get(
            f"{self._term_url(term_id)}/settlement", headers=self.headers
        )
        assert listing.status_code == 200, listing.text
        body = listing.json()
        assert body["status"] in ("pending", "in_progress")

        kinds = {i["item_key"].split(":")[0]: i["kind"] for i in body["items"]}
        # Obligation and surviving-clause items are required by the agreement;
        # recommended operational items are not.
        assert kinds.get("surviving") == "required"
        assert kinds.get("recommended") == "recommended"

        # Surviving items never block completion.
        surviving = [i for i in body["items"] if i["source_type"] == "surviving_clause"]
        assert surviving, "surviving-clause items must be generated"
        assert all(i["blocks_completion"] is False for i in surviving)

    async def test_required_items_trace_to_obligations(self):
        from datetime import date as _date

        await self._make_active()
        ob = Obligation(
            agreement_id=self.agreement.id,
            owner_party="Acme Corp",
            description="Final invoice",
            obligation_type="payment",
            status="overdue",
            amount="12,500.75",
            currency="LKR",
        )
        self.db.add(ob)
        await self.db.commit()

        resp = await self._initiate()
        assert resp.status_code == 201
        term_id = resp.json()["id"]

        body = (
            await self.client.get(
                f"{self._term_url(term_id)}/settlement", headers=self.headers
            )
        ).json()

        ob_items = [i for i in body["items"] if i["source_type"] == "obligation"]
        assert len(ob_items) == 1
        assert ob_items[0]["source_id"] == str(ob.id)
        assert ob_items[0]["kind"] == "required"
        assert ob_items[0]["blocks_completion"] is True
        assert ob_items[0]["category"] == "financial"

        # Financial position: integer minor units, no float.
        assert body["outstanding_amount_minor"] == 1_250_075
        assert body["currency"] == "LKR"
        assert body["obligations_remaining"] == 1

    async def test_get_settlement_404_without_termination(self):
        await self._make_active()
        fake = "00000000-0000-0000-0000-000000000001"
        resp = await self.client.get(
            f"/api/v1/agreements/{self.agreement.id}/terminations/{fake}/settlement",
            headers=self.headers,
        )
        assert resp.status_code == 404

    # ------------------------------------------------------------------
    # Item lifecycle
    # ------------------------------------------------------------------

    async def _setup_with_obligation(self):
        await self._make_active()
        ob = Obligation(
            agreement_id=self.agreement.id,
            owner_party="Acme Corp",
            description="Return customer equipment",
            obligation_type="return_property",
            status="due",
        )
        self.db.add(ob)
        await self.db.commit()
        term = (await self._initiate()).json()
        body = (
            await self.client.get(
                f"{self._term_url(term['id'])}/settlement", headers=self.headers
            )
        ).json()
        item = next(
            i for i in body["items"] if i["source_id"] == str(ob.id)
        )
        return term, body, item

    async def test_resolve_item_updates_remaining_and_termination(self):
        term, body, item = await self._setup_with_obligation()

        resp = await self.client.patch(
            f"{self._term_url(term['id'])}/settlement/items/{item['id']}",
            json={"status": "resolved", "resolution_note": "Equipment returned"},
            headers=self.headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "resolved"

        # Settlement count refreshed.
        after = (
            await self.client.get(
                f"{self._term_url(term['id'])}/settlement", headers=self.headers
            )
        ).json()
        assert after["obligations_remaining"] == 0

        # Parent termination record kept in sync.
        term_after = (
            await self.client.get(
                f"/api/v1/agreements/{self.agreement.id}/terminations/{term['id']}",
                headers=self.headers,
            )
        ).json()
        assert term_after["obligations_check"]["resolved"] is True

    async def test_invalid_item_status_rejected(self):
        term, body, item = await self._setup_with_obligation()
        resp = await self.client.patch(
            f"{self._term_url(term['id'])}/settlement/items/{item['id']}",
            json={"status": "completed"},
            headers=self.headers,
        )
        assert resp.status_code == 400

    # ------------------------------------------------------------------
    # Completion gating
    # ------------------------------------------------------------------

    async def _serve_notice(self, term_id: str):
        resp = await self.client.post(
            f"{self._term_url(term_id)}/notice",
            json={"notice_date": "2026-05-01", "evidence": {"method": "email"}},
            headers=self.headers,
        )
        assert resp.status_code == 200

    async def test_completion_blocked_by_open_settlement_item(self):
        term, body, item = await self._setup_with_obligation()
        await self._serve_notice(term["id"])

        blocked = await self.client.post(
            f"{self._term_url(term['id'])}/complete",
            json={},
            headers=self.headers,
        )
        assert blocked.status_code == 400
        assert "settlement" in blocked.json()["detail"].lower()

    async def test_completion_allowed_after_resolving_items(self):
        term, body, item = await self._setup_with_obligation()
        await self._serve_notice(term["id"])

        resolved = await self.client.patch(
            f"{self._term_url(term['id'])}/settlement/items/{item['id']}",
            json={"status": "resolved"},
            headers=self.headers,
        )
        assert resolved.status_code == 200

        done = await self.client.post(
            f"{self._term_url(term['id'])}/complete",
            json={"effective_date": "2026-06-01"},
            headers=self.headers,
        )
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "effective"

    async def test_force_completion_waives_open_items(self):
        term, body, item = await self._setup_with_obligation()
        await self._serve_notice(term["id"])

        done = await self.client.post(
            f"{self._term_url(term['id'])}/complete",
            json={"effective_date": "2026-06-01", "force": True},
            headers=self.headers,
        )
        assert done.status_code == 200, done.text

        settlement = await self.db.get(TerminationSettlement, body["id"])
        await self.db.refresh(settlement)
        assert settlement.status == "waived"
        assert "settlement item(s) open" in (settlement.notes or "")

    # ------------------------------------------------------------------
    # Settle endpoint
    # ------------------------------------------------------------------

    async def test_settle_requires_no_open_blocking_items(self):
        term, body, item = await self._setup_with_obligation()

        early = await self.client.post(
            f"{self._term_url(term['id'])}/settlement/settle",
            json={},
            headers=self.headers,
        )
        assert early.status_code == 400

        resolved = await self.client.patch(
            f"{self._term_url(term['id'])}/settlement/items/{item['id']}",
            json={"status": "resolved"},
            headers=self.headers,
        )
        assert resolved.status_code == 200

        settled = await self.client.post(
            f"{self._term_url(term['id'])}/settlement/settle",
            json={"notes": "All items discharged"},
            headers=self.headers,
        )
        assert settled.status_code == 200, settled.text
        assert settled.json()["status"] == "settled"

        # Double-settle refused.
        again = await self.client.post(
            f"{self._term_url(term['id'])}/settlement/settle",
            json={},
            headers=self.headers,
        )
        assert again.status_code == 400

    async def test_regenerate_keeps_states_and_picks_up_new_obligations(self):
        term, body, item = await self._setup_with_obligation()

        resolved = await self.client.patch(
            f"{self._term_url(term['id'])}/settlement/items/{item['id']}",
            json={"status": "resolved"},
            headers=self.headers,
        )
        assert resolved.status_code == 200

        # A new obligation appears before completion.
        self.db.add(
            Obligation(
                agreement_id=self.agreement.id,
                owner_party="Acme Corp",
                description="Security certificate renewal",
                obligation_type="compliance",
                status="overdue",
            )
        )
        await self.db.commit()

        regen = await self.client.post(
            f"{self._term_url(term['id'])}/settlement/regenerate",
            headers=self.headers,
        )
        assert regen.status_code == 200, regen.text
        assert regen.json()["obligations_remaining"] == 1

        after = (
            await self.client.get(
                f"{self._term_url(term['id'])}/settlement", headers=self.headers
            )
        ).json()
        keys = {i["item_key"]: i for i in after["items"]}
        # Resolved state preserved for the same source.
        assert keys[f"obligation:{item['source_id']}"]["status"] == "resolved"
        # New obligation added.
        new_items = [
            i
            for i in after["items"]
            if i["description"] == "Security certificate renewal"
        ]
        assert len(new_items) == 1
        assert new_items[0]["status"] == "open"

    # ------------------------------------------------------------------
    # Money handling
    # ------------------------------------------------------------------

    def test_amount_parse_is_integer_minor_units(self):
        from app.services.termination_service import _parse_amount_minor
        from decimal import Decimal

        assert _parse_amount_minor("12,500.75") == 1_250_075
        assert _parse_amount_minor("LKR 1,000") == 100_000
        assert _parse_amount_minor("0.10") == 10
        assert _parse_amount_minor(None) is None
        assert _parse_amount_minor("no amount") is None
        # Decimal-based: 0.1 + 0.2 stays exact in minor units.
        assert _parse_amount_minor("0.1") + _parse_amount_minor("0.2") == 30
