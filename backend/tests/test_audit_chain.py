"""Tests for the tamper-evident audit hash chain (spec 1.20)."""
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditChainRoot, AuditEvent
from app.services.audit_service import (
    create_evidence,
    record_event,
    verify_chain,
)


@pytest_asyncio.fixture
async def chained_events(db_session: AsyncSession, test_org):
    """Record three chained audit events."""
    events = []
    for i, action in enumerate(["CREATED", "SENT", "SIGNED"]):
        events.append(
            await record_event(
                db_session,
                tenant_id=test_org.id,
                agreement_id=None,
                actor_id=None,
                actor_type="system",
                action=action,
                resource_type="agreement",
                metadata_json={"step": i},
            )
        )
    await db_session.commit()
    return events


class TestAuditChain:
    async def test_events_form_a_hash_chain(self, chained_events):
        e1, e2, e3 = chained_events
        assert e1.sequence_number == 1
        assert e2.sequence_number == 2
        assert e3.sequence_number == 3
        assert e1.prev_hash is None
        assert e2.prev_hash == e1.event_hash
        assert e3.prev_hash == e2.event_hash
        assert all(e.event_hash for e in chained_events)

    async def test_chain_root_persisted(self, chained_events, test_org, db_session):
        e1 = chained_events[0]
        result = await db_session.execute(
            select_chain_root(test_org.id)
        )
        root = result.scalar_one_or_none()
        assert root is not None
        assert root.root_hash == e1.event_hash

    async def test_verify_intact_chain(self, chained_events, test_org, db_session):
        result = await verify_chain(db_session, tenant_id=test_org.id)
        assert result["valid"] is True
        assert result["checked"] == 3

    async def test_verify_detects_tampered_event(self, chained_events, test_org, db_session):
        # Tamper: modify event metadata directly (bypassing the service).
        e2 = chained_events[1]
        e2.metadata_json = {"step": 999}
        await db_session.flush()

        result = await verify_chain(db_session, tenant_id=test_org.id)
        assert result["valid"] is False
        assert result["first_broken"]["event_id"] == str(e2.id)

    async def test_verify_detects_deleted_event(self, chained_events, test_org, db_session):
        # Delete an event in the middle of the chain.
        e2 = chained_events[1]
        await db_session.delete(e2)
        await db_session.flush()

        result = await verify_chain(db_session, tenant_id=test_org.id)
        assert result["valid"] is False
        assert "sequence" in result["first_broken"]["reason"]

    async def test_verify_detects_truncated_head(self, chained_events, test_org, db_session):
        # Delete the FIRST event and its chain root → truncation detected.
        e1 = chained_events[0]
        await db_session.delete(e1)
        await db_session.flush()

        result = await verify_chain(db_session, tenant_id=test_org.id)
        assert result["valid"] is False


class TestAuditEvidence:
    async def test_evidence_lifecycle(self, db_session, test_org, test_agreement):
        evidence = await create_evidence(
            db_session,
            tenant_id=test_org.id,
            agreement_id=test_agreement.id,
            evidence_type="document_snapshot",
            content_hash="a" * 64,
            content_ref="s3://bucket/doc.pdf",
            metadata_json={"version_number": 1},
        )
        await db_session.commit()

        from app.services.audit_service import list_evidence

        items = await list_evidence(db_session, test_agreement.id)
        assert len(items) == 1
        assert items[0].content_hash == "a" * 64


class TestAuditApi:
    async def test_verify_endpoint(self, client, test_agreement, test_user, test_org, auth_headers, db_session):
        # Create events via the chain service on the shared test DB.
        from app.services.audit_service import record_event

        await record_event(
            db_session,
            tenant_id=test_org.id,
            agreement_id=test_agreement.id,
            actor_id=test_user.id,
            actor_type="user",
            action="TEST_EVENT",
            resource_type="agreement",
            resource_id=test_agreement.id,
        )
        await db_session.commit()

        response = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/audit/verify",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["valid"] is True

    async def test_evidence_endpoint(self, client, test_agreement, auth_headers):
        response = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/audit/evidence",
            headers=auth_headers,
            json={
                "evidence_type": "signature",
                "content_hash": "b" * 64,
                "metadata_json": {"signer": "A"},
            },
        )
        assert response.status_code == 201

        listing = await client.get(
            f"/api/v1/agreements/{test_agreement.id}/audit/evidence",
            headers=auth_headers,
        )
        assert listing.status_code == 200
        assert len(listing.json()) == 1

    async def test_audit_export_includes_hashes(self, client, test_agreement, test_user, test_org, auth_headers, db_session):
        from app.services.audit_service import record_event

        await record_event(
            db_session,
            tenant_id=test_org.id,
            agreement_id=test_agreement.id,
            actor_id=test_user.id,
            actor_type="user",
            action="EXPORT_TEST",
        )
        await db_session.commit()

        response = await client.get(
            f"/api/v1/agreements/{test_agreement.id}/audit/export",
            headers=auth_headers,
        )
        assert response.status_code == 200
        exported = response.json()
        assert len(exported) == 1
        assert exported[0]["event_hash"] is not None
        assert exported[0]["sequence_number"] == 1


def select_chain_root(tenant_id):
    from sqlalchemy import select
    return select(AuditChainRoot).where(AuditChainRoot.tenant_id == tenant_id)