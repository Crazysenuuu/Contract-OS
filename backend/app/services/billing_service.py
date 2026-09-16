"""Billing & entitlements service (spec 1.24).

Pricing is data (plans as rows), feature access is centralized here, usage
is metered with source traceability, and billing state never touches legal
state. The payment-provider boundary is a thin abstraction so Stripe etc.
can be plugged in without changing callers.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.tenant import get_current_organization_id
from app.models.billing import (
    BillingPlan,
    Entitlement,
    Invoice,
    InvoiceLine,
    Subscription,
    UsageRecord,
)
from app.services.audit_service import record_event


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


_SUBSCRIPTION_STATUS_MAP = {
    "trialing": "trialing",
    "active": "active",
    "past_due": "past_due",
    "canceled": "cancelled",
    "unpaid": "cancelled",
    "incomplete": "active",
    "incomplete_expired": "cancelled",
    "paused": "past_due",
    "cancelled": "cancelled",
    "expired": "expired",
}

_INVOICE_STATUS_MAP = {
    "paid": "paid",
    "open": "issued",
    "uncollectible": "failed",
    "void": "void",
    "draft": "draft",
}


async def process_billing_webhook(
    db: AsyncSession,
    provider: str,
    event_id: str,
    event_type: str,
    payload: dict,
) -> dict:
    """Apply a verified provider billing event (idempotent).

    Returns an update summary. Referenced plans/invoices must already exist;
    unknown external ids are reported in ``skipped`` so operators can see why
    an event had no effect without failing the delivery.
    """
    from app.models.incoming_webhook import IncomingWebhookEvent

    receipt = IncomingWebhookEvent(
        provider=provider,
        event_id=event_id,
        event_type=event_type,
        status="processed",
        received_at=now_utc(),
        payload=str(payload)[:4000],
    )
    db.add(receipt)
    await db.flush()

    updates: dict[str, Any] = {"subscriptions": 0, "invoices": 0}
    skipped: list[str] = []

    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    obj = data.get("object", {}) if isinstance(data, dict) else {}

    if event_type.startswith("customer.subscription."):
        external_id = obj.get("id")
        if external_id:
            result = await db.execute(
                select(Subscription).where(
                    Subscription.external_subscription_id == external_id
                )
            )
            subscription = result.scalar_one_or_none()
            if subscription is None:
                skipped.append(f"unknown subscription {external_id}")
            else:
                raw_status = obj.get("status", subscription.status)
                subscription.status = _SUBSCRIPTION_STATUS_MAP.get(
                    raw_status, subscription.status
                )
                period = obj.get("current_period")
                if isinstance(period, dict):
                    subscription.current_period_start = _date_from_unix(
                        period.get("start")
                    )
                    subscription.current_period_end = _date_from_unix(
                        period.get("end")
                    )
                if subscription.status == "cancelled":
                    subscription.canceled_at = now_utc()
                updates["subscriptions"] += 1
        else:
            skipped.append("subscription event without object id")

    elif event_type.startswith("invoice.") or event_type == "charge.refunded":
        external_id = obj.get("id")
        status_event = obj.get("status") or _invoice_status_from_event_type(event_type)
        if external_id and status_event:
            result = await db.execute(
                select(Invoice).where(Invoice.external_id == external_id)
            )
            invoice = result.scalar_one_or_none()
            if invoice is None:
                skipped.append(f"unknown invoice {external_id}")
            else:
                invoice.status = _INVOICE_STATUS_MAP.get(status_event, status_event)
                if invoice.status == "paid":
                    invoice.paid_at = now_utc()
                if obj.get("amount_due") is not None and obj.get("amount_paid"):
                    invoice.amount_cents = obj.get("amount_paid", invoice.amount_cents)
                    invoice.currency = obj.get("currency", invoice.currency)
                updates["invoices"] += 1
        else:
            skipped.append(f"invoice event without id/status ({event_type})")

    elif event_type == "usage.recorded":
        external_id = obj.get("subscription_item")
        quantity = obj.get("quantity", 1)
        feature_key = obj.get("metadata", {}).get("feature_key") if isinstance(
            obj.get("metadata"), dict
        ) else None
        if external_id and feature_key:
            result = await db.execute(
                select(Subscription).where(
                    Subscription.external_subscription_id == external_id
                )
            )
            subscription = result.scalar_one_or_none()
            if subscription is not None:
                db.add(
                    UsageRecord(
                        tenant_id=subscription.tenant_id,
                        feature_key=feature_key,
                        quantity=int(quantity or 1),
                        recorded_at=_event_ts(payload),
                        source="webhook",
                        source_ref=event_id,
                        metadata_json=obj.get("metadata") or {},
                    )
                )
                updates["usage"] = updates.get("usage", 0) + 1
            else:
                skipped.append(f"unknown subscription_item {external_id}")
        else:
            skipped.append("usage event without subscription_item/feature_key")

    return {"status": "processed", "updates": updates, "skipped": skipped}


def _date_from_unix(ts: Any) -> date | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def _event_ts(payload: dict) -> datetime:
    try:
        return datetime.fromtimestamp(
            int(payload.get("created", 0)), tz=timezone.utc
        )
    except (TypeError, ValueError):
        return now_utc()


def _invoice_status_from_event_type(event_type: str) -> str | None:
    if event_type.endswith("payment_failed"):
        return "failed"
    if event_type.endswith("payment_succeeded") or event_type.endswith("finalized"):
        return "issued"
    if event_type.endswith("voided"):
        return "void"
    return None


# --------------------------------------------------------------------------
# Plans & subscriptions
# --------------------------------------------------------------------------

async def list_plans(
    db: AsyncSession,
    tenant_id: uuid.UUID | None = None,
    include_inactive: bool = False,
) -> list[BillingPlan]:
    query = select(BillingPlan)
    if tenant_id is not None:
        query = query.where(
            (BillingPlan.tenant_id == tenant_id)
            | (BillingPlan.tenant_id.is_(None))
        )
    if not include_inactive:
        query = query.where(BillingPlan.is_active.is_(True))
    result = await db.execute(query.order_by(BillingPlan.monthly_price_cents.asc()))
    return list(result.scalars().all())


async def get_plan_by_code(
    db: AsyncSession,
    code: str,
    tenant_id: uuid.UUID | None = None,
) -> BillingPlan | None:
    query = select(BillingPlan).where(BillingPlan.code == code)
    if tenant_id is not None:
        query = query.where(
            (BillingPlan.tenant_id == tenant_id)
            | (BillingPlan.tenant_id.is_(None))
        )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_subscription(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> Subscription | None:
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.plan))
        .where(Subscription.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def subscribe(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    plan_code: str,
    created_by: uuid.UUID,
    external_provider: str | None = None,
    external_subscription_id: str | None = None,
) -> Subscription:
    """Create or replace the tenant's subscription."""
    plan = await get_plan_by_code(db, plan_code, tenant_id=tenant_id)
    if plan is None:
        raise ValueError(f"Plan '{plan_code}' not found or inactive")

    existing = await get_subscription(db, tenant_id)
    if existing is not None:
        existing.plan_id = plan.id
        existing.status = "active"
        existing.external_provider = external_provider
        existing.external_subscription_id = external_subscription_id
        existing.canceled_at = None
        existing.plan = plan
        subscription = existing
    else:
        subscription = Subscription(
            tenant_id=tenant_id,
            plan_id=plan.id,
            plan=plan,
            status="active",
            current_period_start=date.today(),
            current_period_end=None,
            seat_limit=int(_plan_feature(plan, "seats", default=1)),
            external_provider=external_provider,
            external_subscription_id=external_subscription_id,
        )
        db.add(subscription)
    await db.flush()

    await record_event(
        db,
        tenant_id=tenant_id,
        actor_id=created_by,
        actor_type="user",
        action="SUBSCRIPTION_CHANGED",
        resource_type="subscription",
        resource_id=subscription.id,
        metadata_json={"plan_code": plan.code, "plan_id": str(plan.id)},
    )
    await db.flush()
    return subscription


async def cancel_subscription(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> Subscription | None:
    subscription = await get_subscription(db, tenant_id)
    if subscription is None:
        return None
    subscription.status = "cancelled"
    subscription.canceled_at = now_utc()

    await record_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_type="user",
        action="SUBSCRIPTION_CANCELLED",
        resource_type="subscription",
        resource_id=subscription.id,
        metadata_json={"plan_id": str(subscription.plan_id)},
    )
    await db.flush()
    return subscription


# --------------------------------------------------------------------------
# Entitlements
# --------------------------------------------------------------------------

def _plan_feature(
    plan: BillingPlan,
    feature_key: str,
    default: Any = None,
) -> Any:
    features = plan.features or {}
    return features.get(feature_key, default)


async def _tenant_override(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    feature_key: str,
) -> Entitlement | None:
    result = await db.execute(
        select(Entitlement).where(
            Entitlement.tenant_id == tenant_id,
            Entitlement.feature_key == feature_key,
            Entitlement.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def resolve_entitlement(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    feature_key: str,
) -> dict:
    """Resolve the effective entitlement for a feature.

    Resolution order: tenant override > plan feature > default (None =
    not entitled).
    """
    override = await _tenant_override(db, tenant_id, feature_key)
    if override is not None and override.value is not None:
        return {
            "feature_key": feature_key,
            "source": "override",
            **override.value,
        }

    subscription = await get_subscription(db, tenant_id)
    if subscription is None:
        return {
            "feature_key": feature_key,
            "source": "none",
            "enabled": False,
            "limit": 0,
        }
    plan = await db.get(BillingPlan, subscription.plan_id)
    features = (plan.features or {}) if plan else {}
    # Distinguish "not in plan" (not entitled) from an explicit null limit
    # (unlimited) — a missing key must NOT grant unlimited access.
    if feature_key not in features:
        return {
            "feature_key": feature_key,
            "source": "plan",
            "enabled": False,
            "limit": 0,
        }
    plan_value = features[feature_key]
    if plan_value is None:
        return {
            "feature_key": feature_key,
            "source": "plan",
            "enabled": True,
            "limit": None,
        }
    if isinstance(plan_value, bool):
        return {
            "feature_key": feature_key,
            "source": "plan",
            "enabled": plan_value,
            "limit": 1 if plan_value else 0,
        }
    if isinstance(plan_value, dict):
        return {"feature_key": feature_key, "source": "plan", **plan_value}
    # Numeric limit.
    return {
        "feature_key": feature_key,
        "source": "plan",
        "enabled": plan_value > 0,
        "limit": plan_value,
    }


async def get_usage(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    feature_key: str,
    period_start: datetime | None = None,
) -> int:
    query = select(func.coalesce(func.sum(UsageRecord.quantity), 0)).where(
        UsageRecord.tenant_id == tenant_id,
        UsageRecord.feature_key == feature_key,
    )
    if period_start is not None:
        query = query.where(UsageRecord.recorded_at >= period_start)
    result = await db.execute(query)
    return int(result.scalar_one() or 0)


async def check_entitlement(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    feature_key: str,
    quantity: int = 1,
) -> dict:
    """Check whether a feature may be used (optionally with quantity)."""
    entitlement = await resolve_entitlement(db, tenant_id, feature_key)
    enabled = entitlement.get("enabled", True)
    limit = entitlement.get("limit")
    if limit is None:
        allowed = enabled
        remaining = None
    else:
        usage = await get_usage(db, tenant_id, feature_key)
        remaining = max(int(limit) - usage, 0)
        allowed = enabled and remaining >= quantity
    return {
        "feature_key": feature_key,
        "allowed": allowed,
        "enabled": enabled,
        "limit": limit,
        "remaining": remaining,
        "requested": quantity,
    }


async def record_usage(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    feature_key: str,
    quantity: int = 1,
    source: str = "api",
    source_ref: str | None = None,
    metadata_json: dict | None = None,
) -> UsageRecord:
    record = UsageRecord(
        tenant_id=tenant_id,
        feature_key=feature_key,
        quantity=quantity,
        recorded_at=now_utc(),
        source=source,
        source_ref=source_ref,
        metadata_json=metadata_json or {},
    )
    db.add(record)
    await db.flush()
    return record


def require_entitlement(feature_key: str, quantity: int = 1):
    """FastAPI dependency factory: gate an endpoint on an entitlement.

    Usage:
        @router.get("/x")
        async def x(_: None = Depends(require_entitlement("ai_analyses"))):
            ...
    """

    async def _dependency(
        org_id: uuid.UUID = Depends(get_current_organization_id),
        db: AsyncSession = Depends(get_db),
    ):
        result = await check_entitlement(db, org_id, feature_key, quantity)
        if not result["allowed"]:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "message": (
                        f"Feature '{feature_key}' is not available on your plan"
                        if result["limit"] == 0
                        else f"Usage limit reached for '{feature_key}'"
                    ),
                    **result,
                },
            )
        return result

    return _dependency


# --------------------------------------------------------------------------
# Invoices
# --------------------------------------------------------------------------

async def issue_invoice(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    lines: list[dict],
    currency: str = "USD",
    created_by: uuid.UUID,
    metadata_json: dict | None = None,
) -> Invoice:
    """Issue an invoice from line items (description, quantity, unit_price_cents)."""
    subscription = await get_subscription(db, tenant_id)
    count_result = await db.execute(
        select(func.count()).select_from(Invoice).where(
            Invoice.tenant_id == tenant_id
        )
    )
    count = count_result.scalar_one() or 0
    invoice = Invoice(
        tenant_id=tenant_id,
        subscription_id=subscription.id if subscription else None,
        number=f"INV-{tenant_id.hex[:8].upper()}-{count + 1:04d}",
        status="issued",
        amount_cents=0,
        currency=currency,
        metadata_json=metadata_json or {},
    )
    db.add(invoice)
    await db.flush()

    total = 0
    for line in lines:
        amount = int(line.get("quantity", 1)) * int(line.get("unit_price_cents", 0))
        total += amount
        db.add(
            InvoiceLine(
                invoice_id=invoice.id,
                description=line["description"],
                quantity=int(line.get("quantity", 1)),
                unit_price_cents=int(line.get("unit_price_cents", 0)),
                amount_cents=amount,
            )
        )
    invoice.amount_cents = total
    await db.flush()
    await db.refresh(invoice, ["lines"])

    await record_event(
        db,
        tenant_id=tenant_id,
        actor_id=created_by,
        actor_type="user",
        action="INVOICE_ISSUED",
        resource_type="invoice",
        resource_id=invoice.id,
        metadata_json={"number": invoice.number, "amount_cents": total},
    )
    await db.flush()
    return invoice


async def list_invoices(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[Invoice]:
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.lines))
        .where(Invoice.tenant_id == tenant_id)
        .order_by(Invoice.created_at.desc())
    )
    return list(result.scalars().all())


async def mark_invoice_paid(
    db: AsyncSession,
    *,
    invoice: Invoice,
    paid_at: datetime | None = None,
    external_id: str | None = None,
) -> Invoice:
    invoice.status = "paid"
    invoice.paid_at = paid_at or now_utc()
    if external_id:
        invoice.external_id = external_id
    await db.flush()
    return invoice


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------

def serialize_plan(p: BillingPlan) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "code": p.code,
        "description": p.description,
        "monthly_price_cents": p.monthly_price_cents,
        "currency": p.currency,
        "is_active": p.is_active,
        "features": p.features or {},
    }


def serialize_subscription(s: Subscription) -> dict:
    return {
        "id": str(s.id),
        "tenant_id": str(s.tenant_id),
        "plan_id": str(s.plan_id),
        "plan_code": s.plan.code if s.plan else None,
        "status": s.status,
        "current_period_start": (
            s.current_period_start.isoformat() if s.current_period_start else None
        ),
        "current_period_end": (
            s.current_period_end.isoformat() if s.current_period_end else None
        ),
        "seat_limit": s.seat_limit,
        "external_provider": s.external_provider,
        "external_subscription_id": s.external_subscription_id,
        "canceled_at": s.canceled_at.isoformat() if s.canceled_at else None,
    }


def serialize_invoice(i: Invoice) -> dict:
    return {
        "id": str(i.id),
        "tenant_id": str(i.tenant_id),
        "subscription_id": str(i.subscription_id) if i.subscription_id else None,
        "number": i.number,
        "status": i.status,
        "amount_cents": i.amount_cents,
        "currency": i.currency,
        "due_date": i.due_date.isoformat() if i.due_date else None,
        "paid_at": i.paid_at.isoformat() if i.paid_at else None,
        "external_id": i.external_id,
        "created_at": i.created_at.isoformat(),
        "lines": [
            {
                "id": str(l.id),
                "description": l.description,
                "quantity": l.quantity,
                "unit_price_cents": l.unit_price_cents,
                "amount_cents": l.amount_cents,
            }
            for l in i.lines
        ],
    }