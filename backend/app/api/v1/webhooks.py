"""
Webhook & Cron Job API Endpoints.

Manage webhook endpoints and trigger scheduled tasks.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.dependencies.rbac import require_permission
from app.domain.agreement_states import PRE_EXECUTION_STATES
from app.models.user import User
from app.services.webhook_guard import WebhookUrlError
from app.services.webhook_service import WebhookService, WEBHOOK_EVENTS

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

_perm_webhook_manage = Depends(require_permission("webhook.manage"))
cron_router = APIRouter(prefix="/cron", tags=["Cron Jobs"])


# --- Webhook Schemas ---

class WebhookCreate(BaseModel):
    url: str
    description: Optional[str] = None
    events: Optional[list[str]] = None
    headers: Optional[dict] = None
    secret: Optional[str] = None
    retry_count: int = 3
    timeout_seconds: int = 10


class WebhookUpdate(BaseModel):
    url: Optional[str] = None
    description: Optional[str] = None
    events: Optional[list[str]] = None
    headers: Optional[dict] = None
    is_active: Optional[bool] = None
    retry_count: Optional[int] = None
    timeout_seconds: Optional[int] = None


class WebhookResponse(BaseModel):
    id: uuid.UUID
    url: str
    description: Optional[str]
    is_active: bool
    events: Optional[list]
    headers: Optional[dict]
    retry_count: int
    timeout_seconds: int
    created_at: str

    model_config = {"from_attributes": True}


class DeliveryResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    status: str
    response_status_code: Optional[int]
    error_message: Optional[str]
    attempt: int
    created_at: str

    model_config = {"from_attributes": True}


# --- Webhook Endpoints ---

@router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List all webhook endpoints."""
    service = WebhookService(db)
    endpoints = await service.list_endpoints(org_id)
    return [
        WebhookResponse(
            id=e.id,
            url=e.url,
            description=e.description,
            is_active=e.is_active,
            events=e.events,
            headers=e.headers,
            retry_count=e.retry_count,
            timeout_seconds=e.timeout_seconds,
            created_at=e.created_at.isoformat() if e.created_at else "",
        )
        for e in endpoints
    ]


@router.post("", response_model=WebhookResponse, status_code=201, dependencies=[_perm_webhook_manage])
async def create_webhook(
    data: WebhookCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a new webhook endpoint."""
    service = WebhookService(db)
    try:
        endpoint = await service.register_endpoint(
            organization_id=org_id,
            url=data.url,
            description=data.description,
            events=data.events,
            headers=data.headers,
            secret=data.secret,
            retry_count=data.retry_count,
            timeout_seconds=data.timeout_seconds,
        )
    except WebhookUrlError as exc:
        # SSRF/transport validation rejection is a client error, not a
        # server fault — return 400 with the reason, never a 500.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()

    return WebhookResponse(
        id=endpoint.id,
        url=endpoint.url,
        description=endpoint.description,
        is_active=endpoint.is_active,
        events=endpoint.events,
        headers=endpoint.headers,
        retry_count=endpoint.retry_count,
        timeout_seconds=endpoint.timeout_seconds,
        created_at=endpoint.created_at.isoformat() if endpoint.created_at else "",
    )


@router.get("/events")
async def list_available_events():
    """List all available webhook event types."""
    return {"events": WEBHOOK_EVENTS}


@router.get("/{endpoint_id}", response_model=WebhookResponse)
async def get_webhook(
    endpoint_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific webhook endpoint."""
    service = WebhookService(db)
    endpoints = await service.list_endpoints(org_id)
    endpoint = next((e for e in endpoints if e.id == endpoint_id), None)

    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook not found")

    return WebhookResponse(
        id=endpoint.id,
        url=endpoint.url,
        description=endpoint.description,
        is_active=endpoint.is_active,
        events=endpoint.events,
        headers=endpoint.headers,
        retry_count=endpoint.retry_count,
        timeout_seconds=endpoint.timeout_seconds,
        created_at=endpoint.created_at.isoformat() if endpoint.created_at else "",
    )


@router.patch("/{endpoint_id}", response_model=WebhookResponse, dependencies=[_perm_webhook_manage])
async def update_webhook(
    endpoint_id: uuid.UUID,
    data: WebhookUpdate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Update a webhook endpoint."""
    service = WebhookService(db)
    endpoint = await service.update_endpoint(
        endpoint_id=endpoint_id,
        organization_id=org_id,
        **data.model_dump(exclude_unset=True),
    )

    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook not found")

    await db.commit()

    return WebhookResponse(
        id=endpoint.id,
        url=endpoint.url,
        description=endpoint.description,
        is_active=endpoint.is_active,
        events=endpoint.events,
        headers=endpoint.headers,
        retry_count=endpoint.retry_count,
        timeout_seconds=endpoint.timeout_seconds,
        created_at=endpoint.created_at.isoformat() if endpoint.created_at else "",
    )


@router.delete("/{endpoint_id}", dependencies=[_perm_webhook_manage])
async def delete_webhook(
    endpoint_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Delete a webhook endpoint."""
    service = WebhookService(db)
    deleted = await service.delete_endpoint(endpoint_id, org_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Webhook not found")

    await db.commit()
    return {"deleted": True}


@router.get("/{endpoint_id}/deliveries", response_model=list[DeliveryResponse])
async def list_deliveries(
    endpoint_id: uuid.UUID,
    limit: int = Query(50, le=200),
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get delivery history for a webhook endpoint."""
    service = WebhookService(db)
    deliveries = await service.get_deliveries(endpoint_id, limit)

    return [
        DeliveryResponse(
            id=d.id,
            event_type=d.event_type,
            status=d.status,
            response_status_code=d.response_status_code,
            error_message=d.error_message,
            attempt=d.attempt,
            created_at=d.created_at.isoformat() if d.created_at else "",
        )
        for d in deliveries
    ]


@router.get("/{endpoint_id}/stats")
async def get_delivery_stats(
    endpoint_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get delivery statistics for a webhook endpoint."""
    service = WebhookService(db)
    return await service.get_delivery_stats(endpoint_id)


@router.post("/{endpoint_id}/test")
async def test_webhook(
    endpoint_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Send a test webhook delivery."""
    service = WebhookService(db)

    count = await service.fire_event(
        event_type="webhook.test",
        payload={
            "message": "This is a test webhook delivery",
            "timestamp": "2024-01-01T00:00:00Z",
        },
        organization_id=org_id,
    )

    await db.commit()
    return {"deliveries_queued": count}


@router.post("/{endpoint_id}/retry/{delivery_id}")
async def retry_delivery(
    endpoint_id: uuid.UUID,
    delivery_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Retry a failed webhook delivery."""
    service = WebhookService(db)
    delivery = await service.retry_delivery(delivery_id)

    if not delivery:
        raise HTTPException(
            status_code=404,
            detail="Delivery not found or not retryable",
        )

    await db.commit()

    return DeliveryResponse(
        id=delivery.id,
        event_type=delivery.event_type,
        status=delivery.status,
        response_status_code=delivery.response_status_code,
        error_message=delivery.error_message,
        attempt=delivery.attempt,
        created_at=delivery.created_at.isoformat() if delivery.created_at else "",
    )


# --- Cron Job Endpoints ---

@cron_router.post("/digest/send")
async def send_digest_cron(
    frequency: Optional[str] = Query(None, regex="^(daily|weekly)$"),
    api_key: str = Query(..., description="Cron API key for authentication"),
    db: AsyncSession = Depends(get_db),
):
    """Send pending digest emails. Run via cron job."""
    # Verify API key
    from app.core.config import get_settings_lazy
    settings = get_settings_lazy()

    cron_api_key = getattr(settings, "cron_api_key", None)
    if cron_api_key and api_key != cron_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    from app.services.email_digest import EmailDigestService
    digest_service = EmailDigestService(db)
    sent_count = await digest_service.send_pending_digests(frequency=frequency)
    await db.commit()

    return {
        "digests_sent": sent_count,
        "frequency": frequency or "all",
    }


@cron_router.post("/webhooks/retry")
async def retry_pending_webhooks(
    api_key: str = Query(..., description="Cron API key for authentication"),
    db: AsyncSession = Depends(get_db),
):
    """Retry pending webhook deliveries. Run via cron job."""
    # Verify API key
    from app.core.config import get_settings_lazy
    settings = get_settings_lazy()

    cron_api_key = getattr(settings, "cron_api_key", None)
    if cron_api_key and api_key != cron_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    from sqlalchemy import select
    from app.models.webhook import WebhookDelivery

    # Find retrying deliveries
    result = await db.execute(
        select(WebhookDelivery).where(
            WebhookDelivery.status == "retrying",
        )
    )
    deliveries = result.scalars().all()

    service = WebhookService(db)

    retried = 0
    for delivery in deliveries:
        retry_result = await service.retry_delivery(delivery.id)
        if retry_result:
            retried += 1

    await db.commit()

    return {"retried": retried}


@cron_router.post("/obligations/reminder")
async def send_obligation_reminders(
    api_key: str = Query(..., description="Cron API key for authentication"),
    db: AsyncSession = Depends(get_db),
):
    """Send obligation reminder emails. Run via cron job."""
    # Verify API key
    from app.core.config import get_settings_lazy
    settings = get_settings_lazy()

    cron_api_key = getattr(settings, "cron_api_key", None)
    if cron_api_key and api_key != cron_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    from app.services.obligation_reminder_service import dispatch_obligation_reminders

    result = await dispatch_obligation_reminders(db)
    await db.commit()

    return {"reminders_sent": result["sent"], "details": result}


@cron_router.post("/compliance/check")
async def run_compliance_check_cron(
    api_key: str = Query(..., description="Cron API key for authentication"),
    db: AsyncSession = Depends(get_db),
):
    """Run compliance checks on draft/in-flight agreements. Run via cron job.

    Idempotent: checks only agreements that have no completed compliance
    record for their current version, records Violation rows, and raises
    critical-severity alerts through the alerting pipeline.
    """
    # Verify API key
    from app.core.config import get_settings_lazy
    settings = get_settings_lazy()

    cron_api_key = getattr(settings, "cron_api_key", None)
    if cron_api_key and api_key != cron_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    from sqlalchemy import select

    from app.models.agreement import Agreement, AgreementVersion
    from app.models.company_policy import ComplianceReport
    from app.services.compliance_service import ComplianceService

    # Candidate agreements (any lifecycle stage with renderable content).
    agreements_result = await db.execute(
        select(Agreement.id).where(
            Agreement.status.in_(sorted(PRE_EXECUTION_STATES))
        )
    )
    agreement_ids = [row[0] for row in agreements_result.all()]

    checked = 0
    violations_found = 0
    for agreement_id in agreement_ids:
        # Latest rendered version with content.
        version_result = await db.execute(
            select(AgreementVersion)
            .where(AgreementVersion.agreement_id == agreement_id)
            .order_by(AgreementVersion.version_number.desc())
            .limit(1)
        )
        version = version_result.scalar_one_or_none()
        if version is None or not version.content:
            continue

        org_result = await db.execute(
            select(Agreement.organization_id).where(Agreement.id == agreement_id)
        )
        organization_id = org_result.scalar_one()

        # Skip already-checked current versions (idempotency). check_compliance
        # persists the ComplianceReport + PolicyViolation rows itself.
        done_result = await db.execute(
            select(ComplianceReport.id).where(
                ComplianceReport.agreement_id == agreement_id,
                ComplianceReport.version_id == version.id,
            ).limit(1)
        )
        if done_result.scalar_one_or_none() is not None:
            continue

        svc = ComplianceService(db)
        try:
            report = await svc.check_compliance(
                agreement_id=agreement_id,
                organization_id=organization_id,
                version_id=version.id,
                send_alerts=True,
            )
        except Exception:
            continue

        checked += 1
        violations_found += report.get("violations_found", 0)

    await db.commit()
    return {"agreements_checked": checked, "violations_found": violations_found}


# ---------------------------------------------------------------------------
# Inbound e-signature provider webhooks (spec 24.5)
# ---------------------------------------------------------------------------

from fastapi import Request  # noqa: E402

from app.core.config import get_settings  # noqa: E402


@router.post("/esignature/{provider}", tags=["Webhooks"])
async def esignature_provider_webhook(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive DocuSign Connect / Adobe Sign events for an envelope.

    Security mirrors the billing webhook: HMAC-SHA256 over the raw body with
    a per-provider shared secret (``DOCUSIGN_WEBHOOK_SECRET`` or
    ``ADOBE_WEBHOOK_SECRET``), refused when unconfigured. Processing is
    idempotent by provider event id, so retries are safe.
    """
    import hashlib
    import hmac as hmac_mod
    import json

    from app.services.esign_webhook_service import (
        EsignWebhookError,
        process_esign_webhook,
    )

    provider_key = provider.lower()
    settings = get_settings()
    secret_map = {
        "docusign": getattr(settings, "docusign_webhook_secret", None),
        "adobe_sign": getattr(settings, "adobe_webhook_secret", None),
        "adobesign": getattr(settings, "adobe_webhook_secret", None),
    }
    secret = secret_map.get(provider_key)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Webhook for '{provider}' is not configured",
        )

    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed webhook payload",
        )

    signature = request.headers.get("x-esignature-signature", "")
    expected = hmac_mod.new(
        secret.get_secret_value().encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac_mod.compare_digest(signature, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    try:
        summary = await process_esign_webhook(db, payload=payload)
        await db.commit()
    except EsignWebhookError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    return summary
