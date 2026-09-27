"""Agreement lifecycle over the HTTP API (regression suite).

Promoted from the manual smoke scripts that drove a Mutual NDA through
create -> answers -> validate -> render -> version snapshot -> send -> sign
-> auto-execute against a live server.

Covers the gates discovered while promoting the flow:

* ``POST /render`` refuses to render when required answers are missing
  (schema validation + template variable validation, spec 1.9).
* ``POST /sign`` refuses signatures outside SIGNABLE_STATES (spec §67).
* Auto-execution fires when every required signer has signed
  (services/signing_completion.py) and records SYSTEM audit events —
  which historically crashed on PostgreSQL because the zero-UUID
  "system actor" sentinel violates the audit_events.actor_id FK
  (tests run on SQLite, which does not enforce the constraint; the
  sentinel is now normalized to NULL in audit_service.record_event).

The test speaks HTTP (ASGI transport) — no service-layer shortcuts.
"""

import uuid

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token
from seed_data import AGREEMENT_TYPES

pytestmark = pytest.mark.asyncio

# The first MVP catalog entry: mutual_nda with template mutual_nda_lk_v1.
NDA_TYPE = next(t for t in AGREEMENT_TYPES if t["key"] == "mutual_nda")

# Complete answer set: the 9 questionnaire ids from the type schema plus the
# template variables required by mutual_nda_lk_v1.jinja2 (party blocks,
# duration, notice periods, dispute resolution).
ANSWERS = {
    # questionnaire (schema questions)
    "effective_date": "2026-10-01",
    "disclosing_party_name": "Acme Corp",
    "disclosing_party_address": "1 Acme Plaza, Wilmington, DE 19801, USA",
    "receiving_party_name": "Globex Ltd",
    "receiving_party_address": "42 Globex Avenue, London EC2A 1NE, UK",
    "purpose": "Evaluating a potential joint R&D collaboration",
    "confidentiality_period": "2 years",
    "governing_law": "United States",
    "dispute_resolution": "Arbitration",
    # template variables
    "party_a_legal_name": "Acme Corp",
    "party_a_address": "1 Acme Plaza, Wilmington, DE 19801, USA",
    "party_a_country": "United States",
    "party_a_signatory_name": "Jane Doe",
    "party_a_signatory_title": "Chief Legal Officer",
    "party_b_legal_name": "Globex Ltd",
    "party_b_address": "42 Globex Avenue, London EC2A 1NE, UK",
    "party_b_country": "United Kingdom",
    "party_b_signatory_name": "John Smith",
    "party_b_signatory_title": "Managing Director",
    "duration_years": 2,
    "termination_notice_days": 30,
    "confidentiality_survival_years": 3,
    "dispute_resolution_method": "Arbitration",
}


def _auth(user_id) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id=user_id)}"}


async def _create_nda_type(db_session):
    """Persist the seeded mutual_nda type (schema + template_key) for a test."""
    from app.models.agreement_type import AgreementType

    atype = AgreementType(
        id=uuid.UUID(NDA_TYPE["id"]),
        key=NDA_TYPE["key"],
        template_key=NDA_TYPE["template_key"],
        name=NDA_TYPE["name"],
        description=NDA_TYPE["description"],
        category=NDA_TYPE["category"],
        status=NDA_TYPE["status"],
        version=NDA_TYPE["version"],
        schema=NDA_TYPE["schema"],
    )
    db_session.add(atype)
    await db_session.commit()
    await db_session.refresh(atype)
    return atype


class TestAgreementLifecycleAPI:
    async def test_full_lifecycle_create_to_executed(
        self, client: AsyncClient, db_session, test_user, test_org
    ):
        atype = await _create_nda_type(db_session)
        h = _auth(test_user.id)

        # 1. Create the agreement shell.
        r = await client.post(
            "/api/v1/agreements",
            headers=h,
            json={
                "title": "Lifecycle Test Mutual NDA",
                "agreement_type_id": str(atype.id),
                "governing_law": "US",
                "is_test_data": True,
            },
        )
        assert r.status_code == 201, r.text
        aid = r.json()["id"]
        assert r.json()["status"] == "draft"

        # 2. Questionnaire exposes the type's schema questions.
        r = await client.get(
            f"/api/v1/agreements/types/{atype.id}/questions", headers=h
        )
        assert r.status_code == 200
        questions = r.json()
        assert len(questions) == 9

        # 3. Submit answers (wizard contract: answers update, provenance kept).
        r = await client.post(
            f"/api/v1/agreements/{aid}/answers",
            headers=h,
            json={"answers": ANSWERS, "source": "USER_PROVIDED"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "updated"

        # 4. Validation passes with the complete answer set.
        r = await client.post(f"/api/v1/agreements/{aid}/validate", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["valid"] is True

        # 5. Render produces the document with the answers substituted.
        r = await client.post(
            f"/api/v1/agreements/{aid}/render",
            headers=h,
            json={"generate_pdf": False},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["content_hash"]
        for fragment in (
            "MUTUAL NON-DISCLOSURE AGREEMENT",
            "Acme Corp",
            "Globex Ltd",
            "2026-10-01",
        ):
            assert fragment in body["rendered_text"]

        # 6. Snapshot the rendered text as an immutable version — this is the
        # content the signature hash commits to.
        r = await client.post(
            f"/api/v1/agreements/{aid}/versions",
            headers=h,
            json={"content": body["rendered_text"], "note": "Rendered for signature"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["content_hash"] == body["content_hash"]

        # 7. Send to counterparty: draft -> sent.
        r = await client.post(f"/api/v1/agreements/{aid}/send", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "sent"

        # 8. No signatures yet.
        r = await client.get(f"/api/v1/agreements/{aid}/signature-progress", headers=h)
        assert r.status_code == 200
        assert r.json()["all_signed"] is False

        # 9. Internal user signs; the completion engine auto-executes because
        # there are no required external signers.
        r = await client.post(
            f"/api/v1/agreements/{aid}/sign",
            headers=h,
            json={"consent_text": "I agree to execute this agreement electronically."},
        )
        assert r.status_code == 200, r.text
        assert r.json()["signature_id"]
        assert r.json()["agreement_status"] == "executed"

        # 10. Terminal state is persisted.
        r = await client.get(f"/api/v1/agreements/{aid}", headers=h)
        assert r.status_code == 200
        assert r.json()["status"] == "executed"

        # 11. The hash-linked audit chain records user AND system events.
        r = await client.get(f"/api/v1/agreements/{aid}/audit", headers=h)
        assert r.status_code == 200, r.text
        events = r.json() if isinstance(r.json(), list) else r.json().get("events", [])
        actions = {e["action"] for e in events}
        assert {"STATUS_SEND", "SENT", "STATUS_SIGN", "SIGNED", "EXECUTED"} <= actions
        # The system-actor events must not carry the zero-UUID sentinel.
        system_events = [e for e in events if e.get("actor_type") == "system"]
        assert system_events, "expected SYSTEM audit events for auto-execution"
        for e in system_events:
            assert e.get("actor_id") in (None, ""), (
                "system actor must be recorded with actor_id=NULL, "
                f"got {e.get('actor_id')!r} (zero-UUID sentinel breaks the FK)"
            )

    async def test_render_rejected_while_required_answers_missing(
        self, client: AsyncClient, db_session, test_user, test_org
    ):
        atype = await _create_nda_type(db_session)
        h = _auth(test_user.id)

        r = await client.post(
            "/api/v1/agreements",
            headers=h,
            json={
                "title": "Unanswered NDA",
                "agreement_type_id": str(atype.id),
                "is_test_data": True,
            },
        )
        assert r.status_code == 201
        aid = r.json()["id"]

        r = await client.post(
            f"/api/v1/agreements/{aid}/render",
            headers=h,
            json={"generate_pdf": False},
        )
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert "Validation failed" in detail["message"]

    async def test_sign_rejected_from_draft(
        self, client: AsyncClient, db_session, test_user, test_org
    ):
        atype = await _create_nda_type(db_session)
        h = _auth(test_user.id)

        r = await client.post(
            "/api/v1/agreements",
            headers=h,
            json={
                "title": "Draft-only NDA",
                "agreement_type_id": str(atype.id),
                "is_test_data": True,
            },
        )
        assert r.status_code == 201
        aid = r.json()["id"]

        r = await client.post(
            f"/api/v1/agreements/{aid}/sign",
            headers=h,
            json={"consent_text": "Premature consent"},
        )
        assert r.status_code == 400
        assert "draft" in r.json()["detail"].lower()

    async def test_send_rejected_from_sent_state(
        self, client: AsyncClient, db_session, test_user, test_org
    ):
        """A second send is refused: SENT is not a sendable source state."""
        atype = await _create_nda_type(db_session)
        h = _auth(test_user.id)

        r = await client.post(
            "/api/v1/agreements",
            headers=h,
            json={
                "title": "Double-send NDA",
                "agreement_type_id": str(atype.id),
                "is_test_data": True,
            },
        )
        aid = r.json()["id"]
        await client.post(
            f"/api/v1/agreements/{aid}/answers",
            headers=h,
            json={"answers": ANSWERS, "source": "USER_PROVIDED"},
        )
        r = await client.post(f"/api/v1/agreements/{aid}/send", headers=h)
        assert r.status_code == 200

        r = await client.post(f"/api/v1/agreements/{aid}/send", headers=h)
        assert r.status_code == 400


class TestSystemActorAuditNormalization:
    """Regression: the zero-UUID system-actor sentinel must never reach the DB.

    signing_completion/lifecycle used to pass UUID(int=0) as actor_id for
    SYSTEM events; audit_events.actor_id carries a FK to users.id, so every
    auto-execution crashed with IntegrityError on PostgreSQL (invisible to
    SQLite-based tests). record_event now normalizes the sentinel to NULL.
    """

    async def test_record_event_normalizes_sentinel_to_null(self, db_session, test_org):
        from app.services.audit_service import record_event

        event = await record_event(
            db_session,
            tenant_id=test_org.id,
            actor_id=uuid.UUID(int=0),  # legacy sentinel
            actor_type="system",
            action="STATUS_EXECUTE",
            resource_type="agreement",
            resource_id=None,
            metadata_json={"from_status": "signing", "to_status": "executed"},
        )
        await db_session.flush()

        assert event.actor_id is None
        assert event.actor_type == "system"
        assert event.action == "STATUS_EXECUTE"
        assert event.event_hash  # chain integrity preserved

    async def test_record_event_keeps_real_actors(self, db_session, test_org, test_user):
        from app.services.audit_service import record_event

        event = await record_event(
            db_session,
            tenant_id=test_org.id,
            actor_id=test_user.id,
            actor_type="user",
            action="SIGNED",
            resource_type="agreement",
            resource_id=None,
            metadata_json={},
        )
        await db_session.flush()

        assert event.actor_id == test_user.id
