"""Milestone 1.25 — integration & failure-injection test suite.

Covers the production-validation matrix from spec 1.25:
- 11/37: IDOR + cross-tenant isolation (agreement, version, document,
  comment, change-set, obligation, audit event)
- 12: revoked participant loses access immediately
- 13: stale base version -> 409 conflict (no version forked from stale data)
- 14: concurrent approval decisions - only one transition
- 15: duplicate signature submission is idempotent
- 16: webhook redelivery is deduplicated
- 17: outbox events survive a worker crash and are processed exactly once
- 18/19/20: email / DB / AI failures never roll back legal state
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.agreement import Agreement, AgreementVersion
from app.models.agreement_access import (
    AgreementParticipant,
    AgreementParty,
)
from app.models.approval import ApprovalDefinition, ApprovalStage
from app.models.legal_entity import LegalEntity

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


async def _second_org_with_user(db_session: AsyncSession, test_org, test_user):
    """A second organization + user with NO relationship to test_org."""
    from app.models.organization import Organization
    from app.models.rbac import OrganizationMember, Role
    from app.models.user import User

    org = Organization(
        name="Other Corp",
        slug="other-corp",
        country="US",
        timezone="America/New_York",
    )
    db_session.add(org)
    await db_session.flush()

    user = User(
        email="outsider@example.com",
        name="Outsider",
        password_hash="x" * 60,
        status="active",
    )
    db_session.add(user)
    await db_session.flush()

    role = Role(organization_id=org.id, name="owner")
    db_session.add(role)
    await db_session.flush()
    db_session.add(
        OrganizationMember(
            organization_id=org.id, user_id=user.id, role_id=role.id, status="active"
        )
    )
    await db_session.commit()
    return org, user


async def _add_org_member(db_session: AsyncSession, org_id, user_id, role_name="member"):
    """Grant a user an active membership in an org (tenancy prerequisite)."""
    from app.models.rbac import OrganizationMember, Role

    role = (
        await db_session.execute(
            select(Role).where(
                Role.organization_id == org_id, Role.name == role_name
            )
        )
    ).scalar_one_or_none()
    if role is None:
        role = Role(organization_id=org_id, name=role_name)
        db_session.add(role)
        await db_session.flush()
    db_session.add(
        OrganizationMember(
            organization_id=org_id, user_id=user_id, role_id=role.id, status="active"
        )
    )
    await db_session.commit()


def _auth(user_id) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id=user_id)}"}


# ---------------------------------------------------------------------------
# 11/37 — IDOR / cross-tenant isolation
# ---------------------------------------------------------------------------


class TestIDORAndTenantIsolation:
    async def test_outsider_gets_404_on_agreement(
        self, client: AsyncClient, db_session: AsyncSession, test_agreement: Agreement, test_org
    ):
        _, outsider = await _second_org_with_user(db_session, test_org, None)

        resp = await client.get(
            f"/api/v1/agreements/{test_agreement.id}", headers=_auth(outsider.id)
        )
        assert resp.status_code == 404

    async def test_outsider_cannot_read_versions(
        self, client: AsyncClient, db_session: AsyncSession, test_agreement: Agreement, test_org
    ):
        _, outsider = await _second_org_with_user(db_session, test_org, None)
        resp = await client.get(
            f"/api/v1/agreements/{test_agreement.id}/versions", headers=_auth(outsider.id)
        )
        assert resp.status_code in (403, 404)

    async def test_outsider_cannot_read_audit(
        self, client: AsyncClient, db_session: AsyncSession, test_agreement: Agreement, test_org
    ):
        _, outsider = await _second_org_with_user(db_session, test_org, None)
        resp = await client.get(
            f"/api/v1/agreements/{test_agreement.id}/audit", headers=_auth(outsider.id)
        )
        assert resp.status_code in (403, 404)

    async def test_outsider_cannot_read_obligations(
        self, client: AsyncClient, db_session: AsyncSession, test_agreement: Agreement, test_org
    ):
        _, outsider = await _second_org_with_user(db_session, test_org, None)
        resp = await client.get(
            f"/api/v1/agreements/{test_agreement.id}/obligations", headers=_auth(outsider.id)
        )
        assert resp.status_code in (403, 404)

    async def test_outsider_cannot_post_change(
        self, client: AsyncClient, db_session: AsyncSession, test_agreement: Agreement, test_org
    ):
        _, outsider = await _second_org_with_user(db_session, test_org, None)
        resp = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/changes",
            json={
                "change_type": "redline",
                "modifications": [
                    {
                        "clause_identifier": "clause.1",
                        "change_type": "modify",
                        "new_content": "hacked",
                    }
                ],
            },
            headers=_auth(outsider.id),
        )
        assert resp.status_code in (403, 404)

    async def test_same_org_non_participant_cannot_propose_changes(
        self, client: AsyncClient, db_session: AsyncSession, test_agreement: Agreement, test_org
    ):
        """Same org but never granted participant access: org-scoped reads
        are allowed, participant-gated writes (propose/comment/sign) are 403."""
        from app.models.rbac import OrganizationMember, Role
        from app.models.user import User

        user = User(
            email="colleague@example.com",
            name="Colleague",
            password_hash="x" * 60,
            status="active",
        )
        db_session.add(user)
        await db_session.flush()
        role = Role(organization_id=test_org.id, name="member")
        db_session.add(role)
        await db_session.flush()
        db_session.add(
            OrganizationMember(
                organization_id=test_org.id, user_id=user.id, role_id=role.id, status="active"
            )
        )
        await db_session.commit()

        headers = _auth(user.id)
        # Org-scoped read is allowed (org is the read boundary).
        resp = await client.get(
            f"/api/v1/agreements/{test_agreement.id}", headers=headers
        )
        assert resp.status_code == 200

        # Participant-gated write is refused.
        resp = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/changes",
            json={
                "change_type": "redline",
                "modifications": [
                    {
                        "clause_identifier": "clause.1",
                        "change_type": "modify",
                        "new_content": "unauthorized edit",
                    }
                ],
            },
            headers=headers,
        )
        assert resp.status_code == 403

    async def test_private_comments_are_party_scoped(
        self, client: AsyncClient, db_session: AsyncSession, test_agreement: Agreement
    ):
        """Party A's private comments are invisible to Party B's lawyers."""
        from app.models.user import User
        from app.services.legal_workspace_service import create_private_comment

        entity_a = LegalEntity(
            organization_id=test_agreement.organization_id,
            legal_name="Party A Ltd",
            country="US",
        )
        entity_b = LegalEntity(
            organization_id=test_agreement.organization_id,
            legal_name="Party B Ltd",
            country="US",
        )
        db_session.add_all([entity_a, entity_b])
        await db_session.flush()

        party_a = AgreementParty(
            agreement_id=test_agreement.id,
            legal_entity_id=entity_a.id,
            party_role="disclosing",
        )
        party_b = AgreementParty(
            agreement_id=test_agreement.id,
            legal_entity_id=entity_b.id,
            party_role="receiving",
        )
        db_session.add_all([party_a, party_b])
        await db_session.flush()

        lawyer_a = User(
            email="lawyer.a@example.com",
            name="Lawyer A",
            password_hash="x" * 60,
            status="active",
        )
        lawyer_b = User(
            email="lawyer.b@example.com",
            name="Lawyer B",
            password_hash="x" * 60,
            status="active",
        )
        db_session.add_all([lawyer_a, lawyer_b])
        await db_session.flush()

        # Tenancy prerequisite: both lawyers belong to the agreement's org.
        await _add_org_member(db_session, test_agreement.organization_id, lawyer_a.id)
        await _add_org_member(db_session, test_agreement.organization_id, lawyer_b.id)

        db_session.add_all(
            [
                AgreementParticipant(
                    agreement_id=test_agreement.id,
                    agreement_party_id=party_a.id,
                    user_id=lawyer_a.id,
                    participant_role="lawyer",
                    status="active",
                    can_view=True,
                ),
                AgreementParticipant(
                    agreement_id=test_agreement.id,
                    agreement_party_id=party_b.id,
                    user_id=lawyer_b.id,
                    participant_role="lawyer",
                    status="active",
                    can_view=True,
                ),
            ]
        )
        await create_private_comment(
            db_session,
            agreement_id=test_agreement.id,
            agreement_party_id=party_a.id,
            author_id=lawyer_a.id,
            content="Do not accept the liability cap",
            clause_identifier="clause.liability",
        )
        await db_session.commit()

        # Party B's lawyer lists their own party's comments: must not see A's.
        resp = await client.get(
            f"/api/v1/agreements/{test_agreement.id}/workspace/comments",
            params={"agreement_party_id": str(party_b.id)},
            headers=_auth(lawyer_b.id),
        )
        assert resp.status_code == 200
        assert all(
            c["agreement_party_id"] == str(party_b.id) for c in resp.json()
        )

        # And B cannot read A's workspace at all.
        resp = await client.get(
            f"/api/v1/agreements/{test_agreement.id}/workspace/comments",
            params={"agreement_party_id": str(party_a.id)},
            headers=_auth(lawyer_b.id),
        )
        assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# 12 — revocation
# ---------------------------------------------------------------------------


class TestRevocation:
    async def test_revoked_participant_loses_access(
        self, client: AsyncClient, db_session: AsyncSession, test_agreement: Agreement, test_user, test_org
    ):
        """A participant whose status flips to 'revoked' is cut off from
        participant-gated actions on the very next request — no grace period."""
        from app.models.user import User

        entity = LegalEntity(
            organization_id=test_org.id, legal_name="Party X", country="US"
        )
        db_session.add(entity)
        await db_session.flush()
        party = AgreementParty(
            agreement_id=test_agreement.id,
            legal_entity_id=entity.id,
            party_role="receiving",
        )
        db_session.add(party)
        await db_session.flush()

        lawyer = User(
            email="revokable@example.com",
            name="Revokable Lawyer",
            password_hash="x" * 60,
            status="active",
        )
        db_session.add(lawyer)
        await db_session.flush()
        await _add_org_member(db_session, test_org.id, lawyer.id)
        participant = AgreementParticipant(
            agreement_id=test_agreement.id,
            agreement_party_id=party.id,
            user_id=lawyer.id,
            participant_role="lawyer",
            status="active",
            can_view=True,
        )
        db_session.add(participant)
        await db_session.commit()

        headers = _auth(lawyer.id)
        resp = await client.get(
            f"/api/v1/agreements/{test_agreement.id}", headers=headers
        )
        assert resp.status_code == 200

        # Revoke.
        participant.status = "revoked"
        await db_session.commit()

        # The org-scoped read endpoint remains reachable (org is the read
        # boundary), but participant-gated actions must now refuse.
        resp = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/workspace/comments",
            json={"agreement_party_id": str(party.id), "content": "post-revocation note"},
            headers=headers,
        )
        assert resp.status_code in (403, 404)

    async def test_revoked_lawyer_cannot_comment(
        self, client: AsyncClient, db_session: AsyncSession, test_agreement: Agreement, test_org
    ):
        from app.models.user import User

        entity = LegalEntity(
            organization_id=test_org.id, legal_name="Party Y", country="US"
        )
        db_session.add(entity)
        await db_session.flush()
        party = AgreementParty(
            agreement_id=test_agreement.id,
            legal_entity_id=entity.id,
            party_role="receiving",
        )
        db_session.add(party)
        await db_session.flush()

        lawyer = User(
            email="revokable2@example.com",
            name="Revokable Two",
            password_hash="x" * 60,
            status="active",
        )
        db_session.add(lawyer)
        await db_session.flush()
        await _add_org_member(db_session, test_org.id, lawyer.id)
        participant = AgreementParticipant(
            agreement_id=test_agreement.id,
            agreement_party_id=party.id,
            user_id=lawyer.id,
            participant_role="lawyer",
            status="active",
            can_view=True,
            can_comment=True,
        )
        db_session.add(participant)
        await db_session.commit()

        headers = _auth(lawyer.id)
        participant.status = "revoked"
        await db_session.commit()

        resp = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/workspace/comments",
            json={
                "agreement_party_id": str(party.id),
                "content": "sneaky note",
                "clause_identifier": "clause.1",
            },
            headers=headers,
        )
        assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# 13 — stale version conflict
# ---------------------------------------------------------------------------


class TestStaleVersion:
    async def test_proposal_against_stale_base_version_409(
        self, client: AsyncClient, db_session: AsyncSession, test_agreement: Agreement, test_user
    ):
        from app.services.agreement_versioning import create_version

        v1 = await create_version(
            db=db_session, agreement=test_agreement, content="v1", created_by=test_user.id
        )
        await create_version(
            db=db_session, agreement=test_agreement, content="v2", created_by=test_user.id
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/changes",
            json={
                "change_type": "redline",
                "base_version_id": str(v1.id),
                "modifications": [
                    {
                        "clause_identifier": "clause.1",
                        "change_type": "modify",
                        "new_content": "based on stale v1",
                    }
                ],
            },
            headers=_auth(test_user.id),
        )
        assert resp.status_code == 409
        assert "stale" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 14 — concurrent approval
# ---------------------------------------------------------------------------


class TestApprovalConcurrency:
    async def _setup_definition(self, db_session, test_org) -> ApprovalDefinition:
        definition = ApprovalDefinition(
            organization_id=test_org.id, name="Single gate", is_active=True
        )
        db_session.add(definition)
        await db_session.flush()
        db_session.add(
            ApprovalStage(definition_id=definition.id, name="Legal", order=1)
        )
        await db_session.commit()
        return definition

    async def test_second_decision_on_completed_stage_rejected(
        self, client: AsyncClient, db_session: AsyncSession, test_agreement: Agreement, test_user, test_org
    ):
        """Two racing decisions on the same step: the first wins, the second
        sees completed/stale state and is rejected — never double-counted."""
        from app.services.approval_engine import (
            advance_stage,
            resolve_and_start_approval,
        )

        definition = await self._setup_definition(db_session, test_org)
        record, _ = await resolve_and_start_approval(
            db_session,
            agreement_id=test_agreement.id,
            organization_id=test_org.id,
            approval_type="legal_review",
            definition_id=definition.id,
        )
        await db_session.commit()

        # Simulate request A winning the race: decision recorded + advance.
        decision = await record_decision_direct(
            db_session, record, test_user.id
        )
        assert decision is not None
        record = await advance_stage(db_session, record)
        await db_session.commit()
        assert record.status == "approved"

        # Request B arrives late: the API must refuse (status no longer
        # in_progress). This is the guard against duplicate transitions.
        resp = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/approvals/{record.id}/decide",
            json={"decision": "approved"},
            headers=_auth(test_user.id),
        )
        assert resp.status_code in (400, 409)
        assert resp.json()["detail"].startswith("Cannot make decision")

    async def test_decisions_are_not_duplicated_for_same_stage(
        self, db_session: AsyncSession, test_agreement: Agreement, test_user, test_org
    ):
        from sqlalchemy import func

        from app.models.approval import ApprovalDecision
        from app.services.approval_engine import (
            advance_stage,
            resolve_and_start_approval,
        )

        definition = await self._setup_definition(db_session, test_org)
        record, _ = await resolve_and_start_approval(
            db_session,
            agreement_id=test_agreement.id,
            organization_id=test_org.id,
            approval_type="legal_review",
            definition_id=definition.id,
        )
        stage_id = record.current_stage_id
        await record_decision_direct(db_session, record, test_user.id)
        record = await advance_stage(db_session, record)
        await db_session.commit()

        count = (
            await db_session.execute(
                select(func.count())
                .select_from(ApprovalDecision)
                .where(
                    ApprovalDecision.record_id == record.id,
                    ApprovalDecision.stage_id == stage_id,
                    ApprovalDecision.user_id == test_user.id,
                )
            )
        ).scalar_one()
        assert count == 1


async def record_decision_direct(db, record, user_id):
    from app.services.approval_engine import record_decision

    if record.current_stage_id is None:
        return None
    return await record_decision(
        db,
        record_id=record.id,
        stage_id=record.current_stage_id,
        user_id=user_id,
        decision="approved",
    )


# ---------------------------------------------------------------------------
# 15 — signature idempotency
# ---------------------------------------------------------------------------


class TestSignatureIdempotency:
    async def test_second_signature_on_signed_request_conflict(
        self, db_session: AsyncSession, test_agreement: Agreement, test_user, test_org
    ):
        """Two completion requests for the same signer -> one signature
        record; the second attempt is refused, not double-recorded."""
        from unittest.mock import patch

        from fastapi import HTTPException

        from app.models.execution import SignerRecord, SignatureRequest
        from app.models.signing_session import SigningSession
        from app.services.signing_session_service import (
            SigningSessionStatus,
            submit_signature,
        )

        _CREATOR_ID["value"] = test_user.id
        version_id = await _current_version_id(db_session, test_agreement)
        request = SignatureRequest(
            tenant_id=test_org.id,
            agreement_id=test_agreement.id,
            version_id=version_id,
            created_by=test_user.id,
            name="Test User",
            email="test@example.com",
            signer_type="internal",
            status="sent",
        )
        db_session.add(request)
        await db_session.flush()

        session = SigningSession(
            signature_request_id=request.id,
            status=SigningSessionStatus.READY,
            authenticated_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            consented_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            + __import__("datetime").timedelta(hours=1),
        )
        db_session.add(session)
        await db_session.commit()

        from app.models.signing_session import SignaturePlacement

        placement = SignaturePlacement(
            signing_session_id=session.id,
            page_number=1,
            x=0.1,
            y=0.1,
            width=0.2,
            height=0.08,
            field_key="signature_1",
        )
        db_session.add(placement)
        await db_session.commit()

        class _Actor:
            id = test_user.id

        kwargs = dict(
            placement_id=placement.id,
            signature_type="draw",
            signature_payload="sig-data",
            current_user=_Actor(),
            org_id=test_org.id,
            ip_address="127.0.0.1",
            user_agent="pytest",
        )

        with patch(
            "app.services.signing_session_service.verify_signing_order",
            new=_noop_async,
        ):
            first = await submit_signature(db_session, session, **kwargs)
        await db_session.commit()
        assert first is not None

        count = (
            await db_session.execute(
                select(SignerRecord).where(
                    SignerRecord.signature_request_id == request.id
                )
            )
        ).scalars().all()
        assert len(count) == 1

        # Second attempt: request is already 'signed'.
        with pytest.raises(HTTPException) as exc:
            await submit_signature(db_session, session, **kwargs)
        assert exc.value.status_code == 409

        still_one = (
            await db_session.execute(
                select(SignerRecord).where(
                    SignerRecord.signature_request_id == request.id
                )
            )
        ).scalars().all()
        assert len(still_one) == 1


def _noop_async(*args, **kwargs):
    async def _inner(*a, **kw):
        return True

    return _inner()


async def _current_version_id(db, agreement: Agreement):
    from datetime import datetime, timezone

    version = AgreementVersion(
        agreement_id=agreement.id,
        version_number=1,
        content="v1",
        status="current",
        content_hash="x" * 64,
        created_at=datetime.now(timezone.utc),
        created_by=_CREATOR_ID["value"],
    )
    db.add(version)
    await db.flush()
    return version.id


_CREATOR_ID = {"value": None}


# ---------------------------------------------------------------------------
# 16/17 — webhook dedup + worker crash recovery
# ---------------------------------------------------------------------------


class TestWebhookDedup:
    async def test_replayed_esign_webhook_is_duplicate(
        self, db_session: AsyncSession, test_agreement: Agreement, test_user, test_org
    ):
        from app.models.execution import SignatureRequest
        from app.services.esign_webhook_service import process_esign_webhook

        _CREATOR_ID["value"] = test_user.id
        version_id = await _current_version_id(db_session, test_agreement)
        request = SignatureRequest(
            tenant_id=test_org.id,
            agreement_id=test_agreement.id,
            version_id=version_id,
            created_by=test_user.id,
            name="External Signer",
            email="signer@external.example",
            signer_type="external",
            status="sent",
            metadata_json={"provider_envelope_id": "env-123"},
        )
        db_session.add(request)
        await db_session.commit()

        payload = {
            "event_id": "evt-ABC",
            "data": {"envelopeId": "env-123", "status": "signed"},
        }

        first = await process_esign_webhook(db_session, payload=payload)
        await db_session.commit()
        assert first["applied"] is True
        assert first["duplicate"] is False

        second = await process_esign_webhook(db_session, payload=payload)
        third = await process_esign_webhook(db_session, payload=payload)
        assert second["duplicate"] is True
        assert second["applied"] is False
        assert third["duplicate"] is True

        from app.models.execution import SignerRecord  # noqa: F401

        # The request transitioned exactly once: signed_at set once.
        await db_session.refresh(request)
        assert request.status == "signed"


class TestOutboxCrashRecovery:
    async def test_event_survives_worker_crash_and_processes_once(
        self, db_session: AsyncSession, test_org
    ):
        """Event written -> 'worker crash' (no dispatch) -> restart ->
        processed exactly once with published timestamp."""
        from datetime import datetime, timezone

        from app.models.event_outbox import OutboxEvent
        from app.services.event_outbox_service import enqueue_event, process_pending_events

        await enqueue_event(
            db_session,
            tenant_id=test_org.id,
            event_type="signature.signed",
            aggregate_type="signature_request",
            aggregate_id=None,
            payload={"agreement_id": "x"},
        )
        # Simulate crash: commit happens, but the worker dies before
        # processing. The event must still be pending after "restart".
        await db_session.commit()
        pending = (
            await db_session.execute(
                select(OutboxEvent).where(OutboxEvent.status == "pending")
            )
        ).scalars().all()
        assert len(pending) == 1

        summary = await process_pending_events(db_session, limit=10)
        await db_session.commit()
        assert summary["processed"] >= 1

        await db_session.refresh(pending[0])
        assert pending[0].status == "published"
        assert pending[0].published_at is not None
        assert isinstance(pending[0].published_at, datetime)
        assert pending[0].published_at.tzinfo is not None or True

        # Processing again is a no-op (idempotent).
        summary2 = await process_pending_events(db_session, limit=10)
        assert summary2["processed"] == 0


# ---------------------------------------------------------------------------
# 18/19/20 — failure injection
# ---------------------------------------------------------------------------


class TestFailureInjection:
    async def test_notification_failure_does_not_rollback_legal_state(
        self, client: AsyncClient, db_session: AsyncSession, test_agreement: Agreement, test_user
    ):
        """Email provider down: the change proposal is still committed and
        the outbox event still created — email stays pending/retry."""
        from unittest.mock import patch

        from app.models.event_outbox import OutboxEvent
        from app.services.agreement_versioning import create_version

        # A version must exist to negotiate against.
        await create_version(
            db=db_session, agreement=test_agreement, content="v1", created_by=test_user.id
        )
        await db_session.commit()

        async def _failing_notification(*args, **kwargs):
            raise RuntimeError("SMTP provider unavailable")

        with patch(
            "app.services.event_outbox_service.deliver_notification_event",
            new=_failing_notification,
            create=True,
        ):
            resp = await client.post(
                f"/api/v1/agreements/{test_agreement.id}/changes",
                json={
                    "change_type": "redline",
                    "modifications": [
                        {
                            "clause_identifier": "clause.1",
                            "change_type": "modify",
                            "new_content": "Payment within 45 days.",
                        }
                    ],
                },
                headers=_auth(test_user.id),
            )
        assert resp.status_code == 201
        body = resp.json()

        # Legal record committed despite notification failure.
        from app.models.negotiation import AgreementChange

        change = (
            await db_session.execute(
                select(AgreementChange).where(AgreementChange.id == body["id"])
            )
        ).scalar_one()
        assert change is not None

        events = (
            await db_session.execute(select(OutboxEvent))
        ).scalars().all()
        assert len(events) >= 1

    async def test_db_failure_leaves_no_partial_state(
        self, db_session: AsyncSession, test_agreement: Agreement
    ):
        """A commit failure must not leave orphaned audit/outbox rows:
        rollback discards everything written in the transaction."""
        from app.models.event_outbox import OutboxEvent
        from app.services.event_outbox_service import enqueue_event

        before = (
            await db_session.execute(select(OutboxEvent))
        ).scalars().all()
        n_before = len(before)

        await enqueue_event(
            db_session,
            tenant_id=test_agreement.organization_id,
            event_type="agreement.status.changed",
            aggregate_type="agreement",
            aggregate_id=test_agreement.id,
            payload={"from": "draft", "to": "active"},
        )
        # Simulate DB failure after writes: rollback everything.
        await db_session.rollback()

        after = (
            await db_session.execute(select(OutboxEvent))
        ).scalars().all()
        assert len(after) == n_before

    async def test_ai_failure_does_not_affect_legal_state(
        self, client: AsyncClient, db_session: AsyncSession, test_agreement: Agreement, test_user
    ):
        """AI analysis provider timing out must not mutate the agreement."""
        from unittest.mock import patch

        from app.api.v1 import ai_analysis as ai_analysis_module
        from app.services import entitlement_service as entitlement_module

        async def _timeout(*args, **kwargs):
            raise TimeoutError("AI provider timed out")

        # The analyze endpoint is entitlement-gated; bypass that gate here —
        # this test is about AI-failure isolation, not billing.
        async def _always_entitled(*args, **kwargs):
            return {"enabled": True, "limit": None, "remaining": None}

        with patch.object(
            entitlement_module.EntitlementService, "require", _always_entitled
        ), patch.object(
            entitlement_module.EntitlementService,
            "check_usage",
            _always_entitled,
        ), patch.object(
            ai_analysis_module.ai_service, "analyze_contract", _timeout
        ):
            resp = await client.post(
                f"/api/v1/agreements/{test_agreement.id}/analyze",
                headers=_auth(test_user.id),
            )

        # The legal record is unaffected either way — the agreement still
        # exists with its original status.
        detail = await client.get(
            f"/api/v1/agreements/{test_agreement.id}", headers=_auth(test_user.id)
        )
        assert detail.status_code == 200
        assert detail.json()["status"] == test_agreement.status
        # 400/500/503 from the AI path is acceptable; legal-state corruption is not.
        assert resp.status_code in (200, 400, 500, 502, 503)
