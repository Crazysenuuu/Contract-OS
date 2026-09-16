"""Obligation lifecycle tests (spec 2.08).

Verifies the state machine, deadline calculator, assignee validation,
evidence workflow (submit -> review -> verify), event history and overdue
detection.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.obligation import Obligation, ObligationEvent
from app.services import obligation_lifecycle
from app.services.obligation_lifecycle import (
    calculate_relative_deadline,
    ensure_valid_transition,
)

pytestmark = pytest.mark.asyncio


def _ob(**kw):
    base = dict(
        agreement_id="11111111-1111-1111-1111-111111111111",
        owner_party="Test Corp",
        description="Deliver quarterly report",
        obligation_type="reporting",
        status="CANDIDATE",
    )
    base.update(kw)
    return base


class TestStateMachine:
    def test_allowed_transition_map(self):
        ensure_valid_transition("CANDIDATE", "CONFIRMATION_REQUIRED")
        ensure_valid_transition("CONFIRMATION_REQUIRED", "CONFIRMED")
        ensure_valid_transition("CONFIRMED", "ASSIGNED")
        ensure_valid_transition("ASSIGNED", "OPEN")
        ensure_valid_transition("OPEN", "IN_PROGRESS")
        ensure_valid_transition("IN_PROGRESS", "COMPLETED")
        ensure_valid_transition("OVERDUE", "COMPLETED")

    def test_invalid_transition_raises(self):
        with pytest.raises(ValueError):
            ensure_valid_transition("CONFIRMED", "COMPLETED")
        with pytest.raises(ValueError):
            ensure_valid_transition("COMPLETED", "OPEN")

    def test_legacy_statuses_normalised(self):
        assert obligation_lifecycle.normalise_status("upcoming") == "OPEN"
        assert obligation_lifecycle.normalise_status("overdue") == "OVERDUE"


class TestDeadlineCalculator:
    def test_absolute(self):
        d = datetime(2026, 12, 31, 12)
        assert obligation_lifecycle.calculate_absolute_deadline(d) == d

    def test_relative_calendar_days(self):
        src = datetime(2026, 10, 1, 9)
        assert calculate_relative_deadline(src, 30, "DAYS") == datetime(2026, 10, 31, 9)

    def test_relative_business_days_skips_weekend(self):
        src = datetime(2026, 9, 4, 9)  # Friday
        # +1 business day -> Monday 2026-09-07
        assert calculate_relative_deadline(
            src, 1, "DAYS", "BUSINESS_DAYS"
        ) == datetime(2026, 9, 7, 9)

    def test_relative_months(self):
        src = datetime(2026, 1, 31, 9)
        assert calculate_relative_deadline(
            src, 1, "MONTHS"
        ) == datetime(2026, 2, 28, 9)

    def test_unsupported_unit(self):
        with pytest.raises(ValueError):
            calculate_relative_deadline(datetime(2026, 1, 1), 1, "FORTNIGHTS")

    def test_unsupported_calendar(self):
        with pytest.raises(ValueError):
            calculate_relative_deadline(
                datetime(2026, 1, 1), 1, "DAYS", "LUNAR_DAYS"
            )


class TestLifecycleEndpoints:
    async def _mk_obligation(
        self, db_session, test_agreement, status="CANDIDATE"
    ):
        obligation = Obligation(
            agreement_id=test_agreement.id,
            owner_party="Test Corp",
            description="Submit quarterly compliance report",
            obligation_type="reporting",
            status=status,
            criticality="HIGH",
            evidence_status="NOT_REQUIRED",
        )
        db_session.add(obligation)
        await db_session.flush()
        return obligation

    async def test_full_lifecycle(
        self, client, auth_headers, db_session, test_agreement
    ):
        obligation = await self._mk_obligation(db_session, test_agreement, "CANDIDATE")
        await db_session.commit()
        oid = str(obligation.id)

        r = await client.post(
            f"/api/v1/obligations/{oid}/confirm", headers=auth_headers
        )
        assert r.status_code == 409, r.text  # CANDIDATE -> confirm not allowed

    async def test_confirm_requires_confirmation_required(
        self, client, auth_headers, db_session, test_agreement
    ):
        obligation = await self._mk_obligation(db_session, test_agreement, "CONFIRMATION_REQUIRED")
        await db_session.commit()

        r = await client.post(
            f"/api/v1/obligations/{obligation.id}/confirm", headers=auth_headers
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CONFIRMED"

    async def test_assign_validate_party_and_member(
        self, client, auth_headers, db_session, test_agreement
    ):
        obligation = await self._mk_obligation(db_session, test_agreement, "CONFIRMED")
        await db_session.commit()
        oid = str(obligation.id)

        r = await client.post(
            f"/api/v1/obligations/{oid}/assignees",
            json={
                "member_id": "99999999-9999-9999-9999-999999999999",
                "responsibility_type": "PERFORMER",
            },
            headers=auth_headers,
        )
        assert r.status_code == 400, r.text
        assert "Member does not belong" in r.json()["detail"]

    async def test_assign_to_org_member_opens_obligation(
        self, client, auth_headers, db_session, test_agreement, test_user
    ):
        obligation = await self._mk_obligation(db_session, test_agreement, "CONFIRMED")
        await db_session.commit()

        r = await client.post(
            f"/api/v1/obligations/{obligation.id}/assignees",
            json={
                "member_id": str(test_user.id),
                "responsibility_type": "PERFORMER",
                "primary_assignee": True,
            },
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "ASSIGNED"
        assert len(r.json()["assignees"]) == 1
        assert r.json()["assignees"][0]["primary_assignee"] is True

    async def test_complete_requires_evidence_when_required(
        self, client, auth_headers, db_session, test_agreement
    ):
        obligation = await self._mk_obligation(db_session, test_agreement, "IN_PROGRESS")
        obligation.evidence_status = "REQUIRED"
        await db_session.commit()

        r = await client.post(
            f"/api/v1/obligations/{obligation.id}/complete", headers=auth_headers
        )
        assert r.status_code == 422, r.text

    async def test_complete_without_evidence_requirement(
        self, client, auth_headers, db_session, test_agreement
    ):
        obligation = await self._mk_obligation(db_session, test_agreement, "IN_PROGRESS")
        await db_session.commit()

        r = await client.post(
            f"/api/v1/obligations/{obligation.id}/complete", headers=auth_headers
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "COMPLETED"
        assert len(r.json()["events"]) >= 1

    async def test_evidence_submit_and_review(
        self, client, auth_headers, db_session, test_agreement
    ):
        obligation = await self._mk_obligation(db_session, test_agreement, "IN_PROGRESS")
        obligation.evidence_status = "REQUIRED"
        await db_session.commit()
        oid = str(obligation.id)

        ev = await client.post(
            f"/api/v1/obligations/{oid}/evidence",
            json={"description": "Bank transfer receipt"},
            headers=auth_headers,
        )
        assert ev.status_code == 201, ev.text
        evidence_id = ev.json()["evidence"][-1]["id"]
        assert ev.json()["evidence"][-1]["status"] == "SUBMITTED"

        review = await client.post(
            f"/api/v1/obligations/{oid}/evidence/{evidence_id}/review",
            json={"approve": True, "comment": "Looks correct"},
            headers=auth_headers,
        )
        assert review.status_code == 200, review.text
        assert review.json()["evidence_status"] == "VERIFIED"

        complete = await client.post(
            f"/api/v1/obligations/{oid}/complete", headers=auth_headers
        )
        assert complete.status_code == 200, complete.text
        assert complete.json()["status"] == "COMPLETED"

    async def test_review_rejects_evidence_on_bad_document(
        self, client, auth_headers, db_session, test_agreement
    ):
        obligation = await self._mk_obligation(db_session, test_agreement, "IN_PROGRESS")
        await db_session.commit()
        oid = str(obligation.id)

        ev = await client.post(
            f"/api/v1/obligations/{oid}/evidence",
            json={"description": "Screenshot"},
            headers=auth_headers,
        )
        evidence_id = ev.json()["evidence"][-1]["id"]

        # Attach a document that belongs to a different agreement.
        from app.models.document import Document, DocumentType

        doc_type = DocumentType(
            code="evidence",
            name="Evidence",
            configuration={},
        )
        db_session.add(doc_type)
        await db_session.flush()
        other_agreement_id = "00000000-0000-0000-0000-000000000001"
        doc = Document(
            organization_id=test_agreement.organization_id,
            agreement_id=other_agreement_id,
            document_type_id=doc_type.id,
            title="unrelated",
            filename="unrelated.pdf",
            media_type="application/pdf",
            storage_key="evidence-test-unrelated.pdf",
            size_bytes=10,
            sha256="a" * 64,
            status="stored",
        )
        db_session.add(doc)
        await db_session.flush()
        from app.models.obligation import ObligationEvidence

        evidence_row = (
            await db_session.execute(
                select(ObligationEvidence).where(
                    ObligationEvidence.id == evidence_id
                )
            )
        ).scalars().one()
        evidence_row.document_id = doc.id
        await db_session.commit()

        review = await client.post(
            f"/api/v1/obligations/{oid}/evidence/{evidence_id}/review",
            json={"approve": True},
            headers=auth_headers,
        )
        assert review.status_code == 200, review.text
        state = review.json()["evidence"][-1]["status"]
        assert state == "REJECTED"

    async def test_invalid_transition_from_completed(
        self, client, auth_headers, db_session, test_agreement
    ):
        obligation = await self._mk_obligation(db_session, test_agreement, "COMPLETED")
        await db_session.commit()

        r = await client.post(
            f"/api/v1/obligations/{obligation.id}/start", headers=auth_headers
        )
        assert r.status_code == 409, r.text


class TestEventsAndDeadlines:
    async def test_events_are_recorded(
        self, client, auth_headers, db_session, test_agreement
    ):
        obligation = Obligation(
            agreement_id=test_agreement.id,
            owner_party="Test Corp",
            description="Renew insurance certificate",
            obligation_type="compliance",
            status="CONFIRMATION_REQUIRED",
            criticality="CRITICAL",
        )
        db_session.add(obligation)
        await db_session.commit()

        await client.post(
            f"/api/v1/obligations/{obligation.id}/confirm", headers=auth_headers
        )

        events = (
            await db_session.execute(
                select(ObligationEvent).where(
                    ObligationEvent.obligation_id == obligation.id
                )
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].event_type == "CONFIRMED"
        assert events[0].previous_status == "CONFIRMATION_REQUIRED"
        assert events[0].new_status == "CONFIRMED"

    async def test_deadline_rule_and_detail(
        self, client, auth_headers, db_session, test_agreement
    ):
        from app.models.obligation import Obligation

        obligation = Obligation(
            agreement_id=test_agreement.id,
            owner_party="Test Corp",
            description="File annual return",
            obligation_type="reporting",
            status="CONFIRMED",
        )
        db_session.add(obligation)
        await db_session.commit()

        r = await client.post(
            f"/api/v1/obligations/{obligation.id}/deadlines",
            json={
                "deadline_type": "FULFILMENT",
                "calculation_rule": {
                    "type": "RELATIVE",
                    "unit": "DAYS",
                    "amount": 30,
                    "calendar": "CALENDAR_DAYS",
                    "reference": "FISCAL_YEAR_END",
                },
                "source_date": "2026-09-01T00:00:00Z",
                "source_event": "FISCAL_YEAR_END",
            },
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text
        deadline = r.json()["deadlines"][0]
        assert "2026-10-01" in deadline["due_at"]

    async def test_detail_returns_source_and_sections(
        self, client, auth_headers, db_session, test_agreement
    ):
        obligation = Obligation(
            agreement_id=test_agreement.id,
            owner_party="Test Corp",
            description="Deliver source code to escrow",
            obligation_type="delivery",
            status="OPEN",
            source_text="Escrow clause 9.3",
            source_version_id="22222222-2222-2222-2222-222222222222",
        )
        db_session.add(obligation)
        await db_session.commit()

        r = await client.get(
            f"/api/v1/obligations/{obligation.id}", headers=auth_headers
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source"]["text"] == "Escrow clause 9.3"
        assert body["status"] == "OPEN"
        assert body["criticality"] == "MEDIUM"
        for section in ("assignees", "deadlines", "evidence", "events"):
            assert section in body