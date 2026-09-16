"""Tests for the contract-quality endpoint (spec 77-81)."""

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token
from app.models.agreement import AgreementVersion

pytestmark = pytest.mark.asyncio


def _auth(user_id) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id=user_id)}"}


async def _add_version(db, agreement, user_id, content: str) -> None:
    from datetime import datetime, timezone

    db.add(
        AgreementVersion(
            agreement_id=agreement.id,
            version_number=1,
            content=content,
            content_hash="a" * 64,
            status="current",
            created_by=user_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()


class TestQualityCheckEndpoint:
    async def test_reports_undefined_terms_and_broken_refs(
        self, client: AsyncClient, db_session, test_agreement, test_user
    ):
        from app.services.agreement_versioning import create_version

        await create_version(
            db=db_session,
            agreement=test_agreement,
            content=(
                "1. Term\nUnder the MSA, this relationship is governed. "
                "See Section 99 for details. Schedule Z is attached "
                "to this agreement.\n"
            ),
            created_by=test_user.id,
        )
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/agreements/{test_agreement.id}/quality-check",
            headers=_auth(test_user.id),
        )
        assert resp.status_code == 200
        body = resp.json()
        codes = {f["code"] for f in body["findings"]}
        assert "UNDEFINED_TERM" in codes
        assert "SECTION_REF_MISSING" in codes
        assert "ATTACHMENT_MISSING" in codes
        assert body["counts"]["medium"] >= 2
        assert body["counts"]["high"] >= 1

    async def test_clean_document_has_no_findings(
        self, client: AsyncClient, db_session, test_agreement, test_user
    ):
        from app.services.agreement_versioning import create_version

        await create_version(
            db=db_session,
            agreement=test_agreement,
            content=(
                "1. Definitions\n\"Services\" means the services described "
                "in Schedule A.\n\nSchedule A\nStatement of work.\n"
            ),
            created_by=test_user.id,
        )
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/agreements/{test_agreement.id}/quality-check",
            headers=_auth(test_user.id),
        )
        assert resp.status_code == 200
        body = resp.json()
        codes = {f["code"] for f in body["findings"]}
        assert "UNDEFINED_TERM" not in codes
        assert "ATTACHMENT_MISSING" not in codes
        assert body["has_blockers"] is False

    async def test_no_version_returns_empty_report(
        self, client: AsyncClient, test_agreement, test_user
    ):
        resp = await client.get(
            f"/api/v1/agreements/{test_agreement.id}/quality-check",
            headers=_auth(test_user.id),
        )
        assert resp.status_code == 200
        assert resp.json()["findings"] == []

    async def test_outsider_gets_404(
        self, client: AsyncClient, db_session, test_agreement, test_org
    ):
        from app.models.organization import Organization
        from app.models.rbac import OrganizationMember, Role
        from app.models.user import User

        org = Organization(
            name="Q Org", slug="q-org", country="US", timezone="America/New_York"
        )
        db_session.add(org)
        await db_session.flush()
        user = User(
            email="quality-outsider@example.com",
            name="Q Outsider",
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

        resp = await client.get(
            f"/api/v1/agreements/{test_agreement.id}/quality-check",
            headers=_auth(user.id),
        )
        assert resp.status_code == 404
