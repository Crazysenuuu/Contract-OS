"""Integration connector API (spec 24.6)."""
import uuid
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.dependencies.rbac import require_permission
from app.models.integration import (
    PROVIDER_LABELS,
    SUPPORTED_EVENTS,
    IntegrationConnector,
)
from app.models.user import User
from app.services.integration_service import (
    IntegrationError,
    build_payload,
    dispatch_event,
    sign_payload,
)

router = APIRouter(prefix="/integrations", tags=["Integrations"])

_perm_integration_manage = Depends(require_permission("integration.manage"))


class ConnectorCreateRequest(BaseModel):
    provider: str = Field(..., pattern="^(salesforce|hubspot|netsuite|sap|stripe)$")
    name: Optional[str] = None
    credentials_ref: Optional[str] = None
    settings: Optional[dict] = None
    events: list[str] = []


class ConnectorUpdateRequest(BaseModel):
    name: Optional[str] = None
    credentials_ref: Optional[str] = None
    settings: Optional[dict] = None
    events: Optional[list[str]] = None
    enabled: Optional[bool] = None


class DispatchRequest(BaseModel):
    event: str
    agreement_id: UUID


@router.get("/providers")
async def list_providers():
    """List supported providers and the events they can sync."""
    return {
        "providers": [
            {"key": key, "label": label}
            for key, label in PROVIDER_LABELS.items()
        ],
        "supported_events": SUPPORTED_EVENTS,
    }


@router.get("")
async def list_connectors(
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(IntegrationConnector)
        .where(IntegrationConnector.organization_id == org_id)
        .order_by(IntegrationConnector.created_at.desc())
    )
    return [
        {
            "id": str(c.id),
            "provider": c.provider,
            "provider_label": PROVIDER_LABELS.get(c.provider, c.provider),
            "name": c.name,
            "enabled": c.enabled,
            "events": c.events or [],
            "settings": c.settings or {},
            "last_sync_at": c.last_sync_at,
            "last_sync_status": c.last_sync_status,
            "last_error": c.last_error,
        }
        for c in result.scalars().all()
    ]


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[_perm_integration_manage])
async def create_connector(
    data: ConnectorCreateRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    invalid = [e for e in data.events if e not in SUPPORTED_EVENTS]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported events: {invalid}",
        )
    connector = IntegrationConnector(
        organization_id=org_id,
        provider=data.provider,
        name=data.name or PROVIDER_LABELS.get(data.provider, data.provider),
        credentials_ref=data.credentials_ref,
        settings=data.settings,
        events=data.events or ["agreement.signed", "agreement.executed"],
        enabled=True,
    )
    db.add(connector)
    await db.flush()
    await db.refresh(connector)
    return {
        "id": str(connector.id),
        "provider": connector.provider,
        "name": connector.name,
        "enabled": connector.enabled,
    }


@router.patch("/{connector_id}", dependencies=[_perm_integration_manage])
async def update_connector(
    connector_id: UUID,
    data: ConnectorUpdateRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(IntegrationConnector).where(
            IntegrationConnector.id == connector_id,
            IntegrationConnector.organization_id == org_id,
        )
    )
    connector = result.scalar_one_or_none()
    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connector not found",
        )
    if data.events is not None:
        invalid = [e for e in data.events if e not in SUPPORTED_EVENTS]
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported events: {invalid}",
            )
        connector.events = data.events
    for field in ("name", "credentials_ref", "settings", "enabled"):
        value = getattr(data, field)
        if value is not None:
            setattr(connector, field, value)
    await db.flush()
    await db.refresh(connector)
    return {"id": str(connector.id), "status": "updated"}


@router.post("/{connector_id}/test")
async def test_connector(
    connector_id: UUID,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Validate the connector by building a sample payload."""
    result = await db.execute(
        select(IntegrationConnector).where(
            IntegrationConnector.id == connector_id,
            IntegrationConnector.organization_id == org_id,
        )
    )
    connector = result.scalar_one_or_none()
    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connector not found",
        )
    sample = build_payload(connector.provider, "agreement.signed", _SampleAgreement())
    return {
        "ok": True,
        "provider": connector.provider,
        "sample_payload": sample,
        "signature": sign_payload(sample, "test-secret"),
    }


class _SampleAgreement:
    id = uuid.uuid4()
    title = "Sample Agreement"
    status = "executed"
    governing_law = "New York"
    effective_date = None
    expiry_date = None
    value = 5000.0
    currency = "USD"


@router.post("/dispatch")
async def dispatch(
    data: DispatchRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dispatch a domain event to all enabled connectors."""
    if data.event not in SUPPORTED_EVENTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported event: {data.event}",
        )
    from app.models.agreement import Agreement

    result = await db.execute(
        select(Agreement).where(
            Agreement.id == data.agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()
    if agreement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement not found",
        )
    try:
        outcomes = await dispatch_event(
            db, organization_id=org_id, event=data.event, agreement=agreement
        )
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return {"outcomes": outcomes}