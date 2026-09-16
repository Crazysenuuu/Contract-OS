"""Event outbox API endpoints (spec 1.14).

Manage and process outbox events (publish + deliver notifications and
real-time pushes).
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_admin, get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.event_outbox import OutboxEvent
from app.models.user import User
from app.services.event_outbox_service import (
    enqueue_event,
    get_outbox_stats,
    process_pending_events,
)

router = APIRouter(prefix="/outbox", tags=["outbox"])


class OutboxEventCreate(BaseModel):
    event_type: str
    aggregate_type: str
    aggregate_id: uuid.UUID | None = None
    payload: dict | None = None


class OutboxEventResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    event_type: str
    aggregate_type: str
    aggregate_id: uuid.UUID | None
    payload: dict | None
    status: str
    attempts: int
    next_attempt_at: datetime | None
    published_at: datetime | None
    last_error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


def _serialize(e: OutboxEvent) -> dict:
    return {
        "id": str(e.id),
        "tenant_id": str(e.tenant_id) if e.tenant_id else None,
        "event_type": e.event_type,
        "aggregate_type": e.aggregate_type,
        "aggregate_id": str(e.aggregate_id) if e.aggregate_id else None,
        "payload": e.payload or {},
        "status": e.status,
        "attempts": e.attempts,
        "next_attempt_at": (
            e.next_attempt_at.isoformat() if e.next_attempt_at else None
        ),
        "published_at": (
            e.published_at.isoformat() if e.published_at else None
        ),
        "last_error": e.last_error,
        "created_at": e.created_at.isoformat(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def enqueue_outbox_event_endpoint(
    data: OutboxEventCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Manually enqueue an outbox event (e.g. for testing or tooling)."""
    event = await enqueue_event(
        db,
        tenant_id=org_id,
        event_type=data.event_type,
        aggregate_type=data.aggregate_type,
        aggregate_id=data.aggregate_id,
        payload=data.payload,
    )
    return _serialize(event)


@router.get("/events")
async def list_outbox_events_endpoint(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = 100,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(OutboxEvent)
    if status_filter:
        query = query.where(OutboxEvent.status == status_filter)
    result = await db.execute(
        query.order_by(OutboxEvent.created_at.desc()).limit(limit)
    )
    return [_serialize(e) for e in result.scalars().all()]


@router.get("/stats")
async def outbox_stats_endpoint(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    return await get_outbox_stats(db)


@router.post("/process")
async def process_outbox_endpoint(
    limit: int = 50,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Process pending outbox events: publish + deliver (idempotent).

    Intended as a manual trigger; a scheduler/celery task should call the
    same ``process_pending_events`` on an interval in production.
    """
    return await process_pending_events(db, limit=limit)


@router.post("/dead-letter/requeue")
async def requeue_dead_letter_endpoint(
    limit: int = 100,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Re-drive dead-lettered events (spec 1.23.22).

    Resets attempts and returns them to 'pending' so the next processing
    pass picks them up. Requires an admin.
    """
    from app.core.resilience import requeue_dead_events

    result = await db.execute(
        select(OutboxEvent)
        .where(OutboxEvent.status == "dead")
        .order_by(OutboxEvent.created_at.asc())
        .limit(limit)
    )
    requeued = requeue_dead_events(list(result.scalars().all()))
    await db.commit()
    return {"requeued": requeued}