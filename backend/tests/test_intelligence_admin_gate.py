"""Admin-visibility gate on the intelligence governance surface.

The feedback review queue and every governance mutation (configurations,
prompt versions, evaluation examples/promotion/runs) are admin-only
(spec 2.10.39/43-45): ratings can quote user content and the review queue
decides what enters the evaluation dataset. Conversations and asking
questions remain available to every member.
"""
import uuid

import pytest
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.main import app
from app.models.user import User


async def _auth_overrides(
    app,
    user: User,
    org_id: uuid.UUID | None,
):
    app.dependency_overrides[get_current_user] = lambda: user
    if org_id is not None:
        app.dependency_overrides[get_current_organization_id] = (
            lambda: org_id
        )


def _clear_auth_overrides(app):
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_organization_id, None)


@pytest.mark.asyncio
class TestGovernanceAdminGate:
    async def test_feedback_review_queue_requires_admin(
        self, client, db_session, test_user, test_org
    ):
        await _auth_overrides(app, test_user, test_org.id)
        try:
            # A regular (non-admin) member is rejected...
            res = await client.get("/api/v1/intelligence/governance/feedback")
            assert res.status_code == 403
            assert "Admin access required" in res.json()["detail"]

            # ...including when they know valid query parameters.
            res = await client.get(
                "/api/v1/intelligence/governance/feedback?pending_only=true"
            )
            assert res.status_code == 403

            # Promotion is equally protected.
            res = await client.post(
                "/api/v1/intelligence/governance/feedback/promote",
                json={"feedback_id": str(uuid.uuid4())},
            )
            assert res.status_code == 403
        finally:
            _clear_auth_overrides(app)

    async def test_feedback_review_queue_allows_admin(
        self, client, db_session, test_user, test_org
    ):
        test_user.is_admin = True
        await db_session.commit()

        await _auth_overrides(app, test_user, test_org.id)
        try:
            res = await client.get("/api/v1/intelligence/governance/feedback")
            assert res.status_code == 200
            assert res.json() == []
        finally:
            _clear_auth_overrides(app)
            test_user.is_admin = False
            await db_session.commit()

    async def test_governance_mutations_require_admin(
        self, client, db_session, test_user, test_org
    ):
        await _auth_overrides(app, test_user, test_org.id)
        try:
            res = await client.post(
                "/api/v1/intelligence/governance/configurations",
                json={
                    "name": "prod",
                    "llm_provider": "openai",
                    "llm_model": "gpt-4o",
                    "embedding_provider": "openai",
                    "embedding_model": "text-embedding-3-small",
                },
            )
            assert res.status_code == 403

            res = await client.post(
                "/api/v1/intelligence/governance/prompts",
                json={"purpose": "ask", "version": "v1", "system_prompt": "x"},
            )
            assert res.status_code == 403

            res = await client.post(
                "/api/v1/intelligence/governance/evaluation-examples",
                json={"question": "q", "category": "c"},
            )
            assert res.status_code == 403

            res = await client.post(
                "/api/v1/intelligence/governance/evaluate"
            )
            assert res.status_code == 403

            res = await client.post(
                "/api/v1/intelligence/governance/configurations/"
                f"{uuid.uuid4()}/activate"
            )
            assert res.status_code == 403
        finally:
            _clear_auth_overrides(app)

    async def test_read_only_registry_views_stay_user_level(
        self, client, db_session, test_user, test_org
    ):
        await _auth_overrides(app, test_user, test_org.id)
        try:
            res = await client.get(
                "/api/v1/intelligence/governance/configuration"
            )
            assert res.status_code == 200

            res = await client.get("/api/v1/intelligence/governance/prompts")
            assert res.status_code == 200
        finally:
            _clear_auth_overrides(app)

    async def test_conversations_and_questions_stay_open_to_members(
        self, client, db_session, test_user, test_org
    ):
        """Only the governance surface is admin-only; ordinary Q&A is not."""
        await _auth_overrides(app, test_user, test_org.id)
        try:
            res = await client.get("/api/v1/intelligence/conversations")
            assert res.status_code == 200

            res = await client.post(
                "/api/v1/intelligence/conversations", json={}
            )
            assert res.status_code == 201
        finally:
            _clear_auth_overrides(app)
