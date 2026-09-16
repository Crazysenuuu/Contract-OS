"""Billing & entitlements API endpoints (spec 1.24).

Plans, subscriptions, entitlement checks, usage metering and invoices.
Billing state is kept strictly separate from legal state.
"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.dependencies.auth import get_current_admin, get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.billing import Invoice
from app.models.user import User
from app.services.billing_service import (
    cancel_subscription,
    check_entitlement,
    get_subscription,
    issue_invoice,
    list_invoices,
    list_plans,
    mark_invoice_paid,
    process_billing_webhook,
    record_usage,
    resolve_entitlement,
    serialize_invoice,
    serialize_plan,
    serialize_subscription,
    subscribe,
)

router = APIRouter(prefix="/billing", tags=["billing"])


def _unix_now() -> int:
    import time

    return int(time.time())


@router.post("/webhooks")
async def billing_webhook_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive + apply verified provider billing events.

    Enforces the spec 22 incoming-webhook security chain: HTTPS transport
    signature verification, timestamp freshness, event-id idempotency, then
    authorized state change.
    """
    import hashlib
    import hmac
    import json

    from sqlalchemy import select as sa_select

    from app.models.incoming_webhook import IncomingWebhookEvent

    settings = get_settings()
    secret = settings.billing_webhook_secret
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing webhook is not configured",
        )

    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed webhook payload",
        )

    # 1. HMAC signature verification.
    signature = request.headers.get("x-webhook-signature", "")
    expected = hmac.new(
        secret.get_secret_value().encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    # 2. Timestamp freshness.
    try:
        event_ts = int(payload.get("created", 0))
        skew = abs(int(request.headers.get("x-webhook-timestamp", event_ts)) - event_ts)
        if event_ts and abs(_unix_now() - event_ts) > settings.billing_webhook_timestamp_skew_seconds:
            skew = max(skew, abs(_unix_now() - event_ts))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook timestamp",
        )
    if skew > settings.billing_webhook_timestamp_skew_seconds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook timestamp is stale",
        )

    # 3. Event-id idempotency.
    event_id = str(payload.get("id") or "")
    if not event_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload has no event id",
        )

    result = await db.execute(
        sa_select(IncomingWebhookEvent).where(
            IncomingWebhookEvent.provider == "billing",
            IncomingWebhookEvent.event_id == event_id,
        )
    )
    if result.scalar_one_or_none() is not None:
        # Duplicate delivery — acknowledge without re-applying.
        return {"received": True, "duplicate": True}

    # 4. Authorized state change.
    event_type = payload.get("type") or ""
    summary = await process_billing_webhook(
        db=db,
        provider="billing",
        event_id=event_id,
        event_type=event_type,
        payload=payload,
    )
    await db.commit()
    return {"received": True, "summary": summary}


class SubscribeRequest(BaseModel):
    plan_code: str
    external_provider: str | None = None
    external_subscription_id: str | None = None


class UsageRequest(BaseModel):
    feature_key: str
    quantity: int = 1
    source: str = "api"
    source_ref: str | None = None
    metadata_json: dict | None = None


class InvoiceLineIn(BaseModel):
    description: str
    quantity: int = 1
    unit_price_cents: int = 0


class InvoiceIssueRequest(BaseModel):
    lines: list[InvoiceLineIn]
    currency: str = "USD"
    metadata_json: dict | None = None


@router.get("/plans")
async def billing_plans_endpoint(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    plans = await list_plans(db, tenant_id=org_id)
    return [serialize_plan(p) for p in plans]


@router.post("/subscribe")
async def subscribe_endpoint(
    data: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        subscription = await subscribe(
            db,
            tenant_id=org_id,
            plan_code=data.plan_code,
            created_by=current_user.id,
            external_provider=data.external_provider,
            external_subscription_id=data.external_subscription_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return serialize_subscription(subscription)


@router.get("/subscription")
async def subscription_endpoint(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    subscription = await get_subscription(db, org_id)
    if subscription is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription for this organization",
        )
    return serialize_subscription(subscription)


@router.patch("/subscription")
async def change_subscription_endpoint(
    data: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        subscription = await subscribe(
            db,
            tenant_id=org_id,
            plan_code=data.plan_code,
            created_by=current_user.id,
            external_provider=data.external_provider,
            external_subscription_id=data.external_subscription_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return serialize_subscription(subscription)


@router.post("/subscription/cancel")
async def cancel_subscription_endpoint(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    subscription = await cancel_subscription(
        db, tenant_id=org_id, actor_id=current_user.id
    )
    if subscription is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription for this organization",
        )
    return serialize_subscription(subscription)


@router.get("/entitlements")
async def entitlements_endpoint(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Resolve the full set of entitlements from the current plan."""
    subscription = await get_subscription(db, org_id)
    if subscription is None:
        return {"subscription": None, "entitlements": []}
    plan = subscription.plan
    features = plan.features or {}
    resolved = []
    for feature_key in sorted(features.keys()):
        resolved.append(
            await resolve_entitlement(db, org_id, feature_key)
        )
    return {"subscription": serialize_subscription(subscription), "entitlements": resolved}


@router.post("/usage")
async def record_usage_endpoint(
    data: UsageRequest,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Record a metered usage event and return the updated entitlement state."""
    record = await record_usage(
        db,
        tenant_id=org_id,
        feature_key=data.feature_key,
        quantity=data.quantity,
        source=data.source,
        source_ref=data.source_ref,
        metadata_json=data.metadata_json,
    )
    # Check after recording so remaining reflects the updated usage.
    check = await check_entitlement(db, org_id, data.feature_key, data.quantity)
    return {
        "usage_record_id": str(record.id),
        "feature_key": data.feature_key,
        "quantity": data.quantity,
        "entitlement": check,
    }


@router.get("/usage/{feature_key}")
async def usage_check_endpoint(
    feature_key: str,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Check entitlement + current usage for a feature."""
    return await check_entitlement(db, org_id, feature_key)


@router.get("/invoices")
async def invoices_endpoint(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    invoices = await list_invoices(db, org_id)
    return [serialize_invoice(i) for i in invoices]


@router.post("/invoices/issue", status_code=status.HTTP_201_CREATED)
async def issue_invoice_endpoint(
    data: InvoiceIssueRequest,
    admin: User = Depends(get_current_admin),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    invoice = await issue_invoice(
        db,
        tenant_id=org_id,
        lines=[line.model_dump() for line in data.lines],
        currency=data.currency,
        created_by=admin.id,
        metadata_json=data.metadata_json,
    )
    return serialize_invoice(invoice)


@router.post("/invoices/{invoice_id}/pay")
async def pay_invoice_endpoint(
    invoice_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.lines))
        .where(
            Invoice.id == invoice_id,
            Invoice.tenant_id == org_id,
        )
    )
    invoice = result.scalar_one_or_none()
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )
    invoice = await mark_invoice_paid(db, invoice=invoice)
    return serialize_invoice(invoice)