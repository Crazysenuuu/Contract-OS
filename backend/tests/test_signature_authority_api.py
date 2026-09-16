"""Integration tests for the signature-authority API (D1).

Verifies the endpoints run on the async session (no sync db.query/commit),
are org-scoped (cross-org access is 403), and the check endpoint resolves
signatory authority + DOA requirements in-context.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.legal_entity import AuthorizedSignatory, LegalEntity


async def _make_entity(db_session: AsyncSession, test_org, name="Signatory Ltd"):
    entity = LegalEntity(
        organization_id=test_org.id,
        legal_name=name,
        country="LK",
        entity_type="company",
    )
    db_session.add(entity)
    await db_session.flush()
    return entity


async def _add_signatory(db_session, entity, user_id=None):
    signatory = AuthorizedSignatory(
        legal_entity_id=entity.id,
        user_id=user_id,
        name="Alice Signer",
        title="Director",
        email="alice@corp.com",
        authority_type="director",
        authority_scope="limited",
        maximum_value=2_000_000,
        currency="LKR",
        is_active=True,
        verification_status="verified",
    )
    db_session.add(signatory)
    await db_session.commit()
    return signatory


class TestSignatureAuthorityApi:
    async def test_list_signatories_async_and_org_scoped(
        self, client, db_session, auth_headers, test_org, test_user
    ):
        entity = await _make_entity(db_session, test_org)
        await _add_signatory(db_session, entity, test_user.id)

        resp = await client.get(
            f"/api/v1/signature-authority/entity/{entity.id}/signatories",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Alice Signer"
        assert data[0]["verification_status"] == "verified"

    async def test_cross_org_access_denied(
        self, client, db_session, auth_headers, test_org, test_user
    ):
        # A second org owns an entity the test user does not belong to.
        from app.models.organization import Organization

        other = Organization(name="Other Corp", slug="other-corp", country="US", timezone="UTC")
        db_session.add(other)
        await db_session.flush()
        entity = await _make_entity(db_session, other)
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/signature-authority/entity/{entity.id}/signatories",
            headers=auth_headers,
        )
        assert resp.status_code == 403, resp.text

        resp = await client.get(
            f"/api/v1/signature-authority/entity/{entity.id}/report",
            headers=auth_headers,
        )
        assert resp.status_code == 403

    async def test_add_signatory_org_scoped(
        self, client, db_session, auth_headers, test_org
    ):
        entity = await _make_entity(db_session, test_org)
        resp = await client.post(
            "/api/v1/signature-authority/signatories",
            headers=auth_headers,
            json={
                "legal_entity_id": str(entity.id),
                "name": "Bob Approver",
                "title": "CFO",
                "authority_type": "cfo",
                "authority_scope": "limited",
                "maximum_value": 10_000_000,
                "currency": "LKR",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Bob Approver"

    async def test_check_signatory_in_scope(
        self, client, db_session, auth_headers, test_org, test_user
    ):
        entity = await _make_entity(db_session, test_org)
        await _add_signatory(db_session, entity, test_user.id)

        resp = await client.post(
            "/api/v1/signature-authority/check",
            headers=auth_headers,
            json={
                "user_id": str(test_user.id),
                "agreement_value": 1_000_000,
                "currency": "LKR",
                "legal_entity_id": str(entity.id),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["allowed"] is True

    async def test_check_exceeds_authority(
        self, client, db_session, auth_headers, test_org, test_user
    ):
        entity = await _make_entity(db_session, test_org)
        await _add_signatory(db_session, entity, test_user.id)

        resp = await client.post(
            "/api/v1/signature-authority/check",
            headers=auth_headers,
            json={
                "user_id": str(test_user.id),
                "agreement_value": 8_000_000,
                "currency": "LKR",
                "legal_entity_id": str(entity.id),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["allowed"] is False
        assert body["reason"] == "exceeds_authority"
        assert len(body["required_approvals"]) > 0

    async def test_check_unknown_user_denied(
        self, client, db_session, auth_headers, test_org, test_user
    ):
        await _make_entity(db_session, test_org)
        resp = await client.post(
            "/api/v1/signature-authority/check",
            headers=auth_headers,
            json={
                "user_id": str(test_user.id),
                "agreement_value": 100_000,
                "currency": "LKR",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["allowed"] is False
        assert resp.json()["reason"] == "user_not_authorized"

    async def test_approvals_required_returns_shapes(
        self, client, auth_headers, test_org
    ):
        resp = await client.get(
            "/api/v1/signature-authority/approvals-required?agreement_value=20000000&currency=LKR",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "approvals" in body
        assert len(body["approvals"]) >= 1