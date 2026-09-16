"""Tests for billing & entitlements (spec 1.24)."""
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import BillingPlan
from app.services.billing_service import (
    check_entitlement,
    get_subscription,
    issue_invoice,
    list_invoices,
    mark_invoice_paid,
    record_usage,
    resolve_entitlement,
    subscribe,
)


@pytest_asyncio.fixture
async def test_plan(db_session: AsyncSession, test_org):
    plan = BillingPlan(
        tenant_id=None,  # system plan template
        name="Starter",
        code="starter",
        description="Starter plan",
        monthly_price_cents=2900,
        currency="USD",
        is_active=True,
        features={
            "agreements": 2,
            "ai_analyses": None,  # unlimited
            "seats": 3,
            "webhooks": False,
        },
    )
    db_session.add(plan)
    await db_session.commit()
    await db_session.refresh(plan)
    return plan


class TestSubscriptions:
    async def test_subscribe_creates_subscription(self, db_session, test_org, test_user, test_plan):
        subscription = await subscribe(
            db_session,
            tenant_id=test_org.id,
            plan_code="starter",
            created_by=test_user.id,
        )
        await db_session.commit()

        assert subscription.plan_id == test_plan.id
        assert subscription.status == "active"
        assert subscription.seat_limit == 3

        loaded = await get_subscription(db_session, test_org.id)
        assert loaded is not None
        assert loaded.plan.code == "starter"

    async def test_subscribe_unknown_plan(self, db_session, test_org, test_user):
        import pytest

        with pytest.raises(ValueError):
            await subscribe(
                db_session,
                tenant_id=test_org.id,
                plan_code="nonexistent",
                created_by=test_user.id,
            )


class TestEntitlements:
    async def test_entitlement_from_plan(self, db_session, test_org, test_user, test_plan):
        await subscribe(
            db_session,
            tenant_id=test_org.id,
            plan_code="starter",
            created_by=test_user.id,
        )
        await db_session.commit()

        entitlement = await resolve_entitlement(db_session, test_org.id, "agreements")
        assert entitlement["limit"] == 2
        assert entitlement["enabled"] is True

    async def test_unlimited_feature(self, db_session, test_org, test_user, test_plan):
        await subscribe(
            db_session,
            tenant_id=test_org.id,
            plan_code="starter",
            created_by=test_user.id,
        )
        await db_session.commit()

        entitlement = await resolve_entitlement(db_session, test_org.id, "ai_analyses")
        assert entitlement["limit"] is None

    async def test_disabled_feature(self, db_session, test_org, test_user, test_plan):
        await subscribe(
            db_session,
            tenant_id=test_org.id,
            plan_code="starter",
            created_by=test_user.id,
        )
        await db_session.commit()

        check = await check_entitlement(db_session, test_org.id, "webhooks")
        assert check["allowed"] is False

    async def test_usage_limit_reached(self, db_session, test_org, test_user, test_plan):
        await subscribe(
            db_session,
            tenant_id=test_org.id,
            plan_code="starter",
            created_by=test_user.id,
        )
        await db_session.commit()

        # Use both agreement slots.
        await record_usage(
            db_session, tenant_id=test_org.id, feature_key="agreements", quantity=1
        )
        await record_usage(
            db_session, tenant_id=test_org.id, feature_key="agreements", quantity=1
        )
        await db_session.commit()

        check = await check_entitlement(db_session, test_org.id, "agreements", quantity=1)
        assert check["allowed"] is False
        assert check["remaining"] == 0

    async def test_unsubscribed_org_not_entitled(self, db_session, test_org):
        check = await check_entitlement(db_session, test_org.id, "agreements")
        assert check["allowed"] is False


class TestInvoices:
    async def test_issue_and_pay_invoice(self, db_session, test_org, test_user, test_plan):
        await subscribe(
            db_session,
            tenant_id=test_org.id,
            plan_code="starter",
            created_by=test_user.id,
        )
        await db_session.commit()

        invoice = await issue_invoice(
            db_session,
            tenant_id=test_org.id,
            lines=[
                {"description": "Starter plan (monthly)", "quantity": 1, "unit_price_cents": 2900},
                {"description": "Extra seat", "quantity": 2, "unit_price_cents": 500},
            ],
            created_by=test_user.id,
        )
        await db_session.commit()

        assert invoice.status == "issued"
        assert invoice.amount_cents == 3900
        assert len(invoice.lines) == 2

        await mark_invoice_paid(db_session, invoice=invoice)
        await db_session.commit()

        invoices = await list_invoices(db_session, test_org.id)
        assert len(invoices) == 1
        assert invoices[0].status == "paid"


class TestBillingApi:
    async def test_subscribe_and_entitlements_api(
        self, client, test_org, test_user, auth_headers, db_session, test_plan
    ):
        response = await client.post(
            "/api/v1/billing/subscribe",
            headers=auth_headers,
            json={"plan_code": "starter"},
        )
        assert response.status_code == 200
        assert response.json()["plan_code"] == "starter"

        entitlements = await client.get(
            "/api/v1/billing/entitlements",
            headers=auth_headers,
        )
        assert entitlements.status_code == 200
        body = entitlements.json()
        assert body["subscription"]["plan_code"] == "starter"
        keys = {e["feature_key"] for e in body["entitlements"]}
        assert "agreements" in keys
        assert "webhooks" in keys

    async def test_usage_api(self, client, test_org, test_user, auth_headers, db_session, test_plan):
        from app.services.billing_service import subscribe

        await subscribe(
            db_session,
            tenant_id=test_org.id,
            plan_code="starter",
            created_by=test_user.id,
        )
        await db_session.commit()

        response = await client.post(
            "/api/v1/billing/usage",
            headers=auth_headers,
            json={"feature_key": "agreements", "quantity": 1},
        )
        assert response.status_code == 200
        assert response.json()["entitlement"]["remaining"] == 1

        check = await client.get(
            "/api/v1/billing/usage/agreements",
            headers=auth_headers,
        )
        assert check.status_code == 200
        assert check.json()["allowed"] is True

    async def test_invoice_api(self, client, test_org, test_user, auth_headers, db_session, test_plan):
        test_user.is_admin = True
        await db_session.commit()

        response = await client.post(
            "/api/v1/billing/invoices/issue",
            headers=auth_headers,
            json={
                "lines": [
                    {"description": "Plan", "quantity": 1, "unit_price_cents": 1000}
                ]
            },
        )
        assert response.status_code == 201
        invoice = response.json()
        assert invoice["amount_cents"] == 1000

        paid = await client.post(
            f"/api/v1/billing/invoices/{invoice['id']}/pay",
            headers=auth_headers,
        )
        assert paid.status_code == 200
        assert paid.json()["status"] == "paid"