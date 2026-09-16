"""
Obligation Management API Endpoints.

Track post-execution obligations extracted from signed agreements.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.obligation import Obligation
from app.models.user import User
from app.services.obligation_service import ObligationService

router = APIRouter(
    prefix="/agreements",
    tags=["Obligations"],
)


# --- Schemas ---

class ObligationCreate(BaseModel):
    owner_party: str
    description: str
    obligation_type: str  # 'payment', 'delivery', 'reporting', etc.
    amount: Optional[str] = None
    frequency: Optional[str] = None  # 'once', 'daily', 'weekly', etc.
    due_date: Optional[date] = None
    clause_identifier: Optional[str] = None


class ObligationUpdateStatus(BaseModel):
    status: str  # 'upcoming', 'due', 'overdue', 'completed', 'waived', 'disputed'


class ObligationResponse(BaseModel):
    id: uuid.UUID
    agreement_id: uuid.UUID
    owner_party: str
    description: str
    obligation_type: str
    amount: Optional[str]
    frequency: Optional[str]
    due_date: Optional[date]
    status: str
    criticality: Optional[str] = None
    clause_identifier: Optional[str]
    # Includes sla_metrics (uptime_target / response_time_hours /
    # measurement_period) for obligations of type 'sla'.
    metadata_json: Optional[dict] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ObligationStats(BaseModel):
    total: int
    upcoming: int
    due: int
    completed: int
    overdue: int
    due_within_30_days: int


# --- Endpoints ---

@router.get(
    "/{agreement_id}/obligations",
    response_model=list[ObligationResponse],
)
async def list_obligations(
    agreement_id: uuid.UUID,
    obligation_status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List obligations for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    service = ObligationService(db)
    obligations = await service.list_agreement_obligations(
        agreement_id, status=obligation_status
    )
    return obligations


@router.get(
    "/{agreement_id}/obligations/stats",
    response_model=ObligationStats,
)
async def get_obligation_stats(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get obligation statistics for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    service = ObligationService(db)
    return await service.get_stats(agreement_id)


@router.post(
    "/{agreement_id}/obligations",
    response_model=ObligationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_obligation(
    agreement_id: uuid.UUID,
    data: ObligationCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a new obligation for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    service = ObligationService(db)
    obligation = await service.create_obligation(
        agreement_id=agreement_id,
        owner_party=data.owner_party,
        description=data.description,
        obligation_type=data.obligation_type,
        amount=data.amount,
        frequency=data.frequency,
        due_date=data.due_date,
        clause_identifier=data.clause_identifier,
    )
    await db.commit()
    await db.refresh(obligation)
    return obligation


@router.patch(
    "/{agreement_id}/obligations/{obligation_id}/status",
    response_model=ObligationResponse,
)
async def update_obligation_status(
    agreement_id: uuid.UUID,
    obligation_id: uuid.UUID,
    data: ObligationUpdateStatus,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Update obligation status (e.g., mark as completed)."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    service = ObligationService(db)
    obligation = await service.update_status(obligation_id, data.status)
    if not obligation:
        raise HTTPException(status_code=404, detail="Obligation not found")
    await db.commit()
    await db.refresh(obligation)
    return obligation


class ExtractionRunResponse(BaseModel):
    run_id: uuid.UUID
    candidate_count: int
    candidates: list[dict]


@router.post(
    "/{agreement_id}/obligations/extract",
    response_model=ExtractionRunResponse,
)
async def extract_obligations(
    agreement_id: uuid.UUID,
    method: str = "hybrid",
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Run obligation extraction over the current agreement version.

    Creates CANDIDATE obligations only - a human must confirm each one
    through /obligations/{id}/confirm before it becomes live (spec 1.16.23).
    """
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    if method not in ("ai", "deterministic", "hybrid"):
        raise HTTPException(status_code=422, detail="method must be ai|deterministic|hybrid")

    service = ObligationService(db)
    try:
        result = await service.extract_obligations(agreement_id, method=method)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await db.commit()
    return result


@router.get(
    "/{agreement_id}/obligations/extraction-runs",
    response_model=list[dict],
)
async def list_extraction_runs(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Provenance for AI extraction runs on this agreement (spec 1.16.21)."""
    from app.models.obligation import ExtractionRun

    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    result = await db.execute(
        select(ExtractionRun)
        .where(ExtractionRun.agreement_id == agreement_id)
        .order_by(ExtractionRun.created_at.desc())
        .limit(50)
    )
    runs = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "method": r.method,
            "model_id": r.model_id,
            "prompt_version": r.prompt_version,
            "source_version_id": str(r.source_version_id) if r.source_version_id else None,
            "candidate_count": r.candidate_count,
            "confirmed_count": r.confirmed_count,
            "status": r.status,
            "error_message": r.error_message,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in runs
    ]


@router.post(
    "/{agreement_id}/obligations/mark-overdue",
)
async def mark_overdue_obligations(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Mark past-due obligations as overdue."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    service = ObligationService(db)
    count = await service.mark_overdue()
    await db.commit()
    return {"marked_overdue": count}
