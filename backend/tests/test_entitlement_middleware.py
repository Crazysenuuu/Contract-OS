"""Entitlement enforcement tests (spec 1.24.16 / 1.24.38).

Authorization and entitlement are independent and BOTH must pass: a user
with agreement access still receives 403 / 402 when the tenant's plan does
not grant the feature.
"""

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.models.billing import BillingPlan
from app.services.billing_service import subscribe
from app.services.entitlement_service import EntitlementService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def test_plan(db_session, test_org):
    from app.models.billing import BillingPlan as BP

    plan = BP(
        tenant_id=None,
        name="Starter",
        code="starter",
        monthly_price_cents=2900,
        currency="USD",
        is_active=True,
        features={
            "agreements": 2,
            "ai_analyses": None,
            "seats": 3,
            "webhooks": False,
        },
    )
    db_session.add(plan)
    await db_session.commit()
    await db_session.refresh(plan)
    return plan


async def _subscribe(db_session, test_org, test_user):
    await subscribe(
        db_session,
        tenant_id=test_org.id,
        plan_code="starter",
        created_by=test_user.id,
    )
    await db_session.commit()


class TestEntitlementService:
    async def test_require_raises_403_without_subscription(self, db_session, test_org):
        with pytest.raises(HTTPException) as exc:
            await EntitlementService.require(db_session, test_org.id, "ai_analyses")
        assert exc.value.status_code == 403

    async def test_require_raises_403_for_disabled_feature(
        self, db_session, test_org, test_user, test_plan
    ):
        await _subscribe(db_session, test_org, test_user)
        with pytest.raises(HTTPException) as exc:
            await EntitlementService.require(db_session, test_org.id, "webhooks")
        assert exc.value.status_code == 403

    async def test_require_returns_entitlement_for_plan_feature(
        self, db_session, test_org, test_user, test_plan
    ):
        await _subscribe(db_session, test_org, test_user)
        result = await EntitlementService.require(db_session, test_org.id, "ai_analyses")
        assert result["enabled"] is True
        assert result["limit"] is None

    async def test_check_usage_raises_402_when_exhausted(
        self, db_session, test_org, test_user, test_plan
    ):
        from app.services.billing_service import record_usage

        await _subscribe(db_session, test_org, test_user)
        await record_usage(
            db_session, tenant_id=test_org.id, feature_key="agreements", quantity=2
        )
        await db_session.commit()

        with pytest.raises(HTTPException) as exc:
            await EntitlementService.check_usage(db_session, test_org.id, "agreements")
        assert exc.value.status_code == 402


class TestEntitlementMiddleware:
    async def test_analyze_contract_blocked_without_plan(
        self, client, auth_headers, test_agreement
    ):
        resp = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/analyze",
            headers=auth_headers,
        )
        assert resp.status_code == 403, resp.text

    async def test_ai_endpoint_403_then_allowed_after_subscription(
        self, client, auth_headers, db_session, test_agreement, test_user, test_plan
    ):
        blocked = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/analyze",
            headers=auth_headers,
        )
        assert blocked.status_code == 403

        from app.services.billing_service import subscribe as s

        await s(
            db_session,
            tenant_id=test_agreement.organization_id,
            plan_code="starter",
            created_by=test_user.id,
        )
        await db_session.commit()

        allowed = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/analyze",
            headers=auth_headers,
        )
        # The plan grants ai_analyses (unlimited) so the gate passes; the
        # endpoint itself then reports no rendered content for the draft.
        assert allowed.status_code == 400, allowed.text