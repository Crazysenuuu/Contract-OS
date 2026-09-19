"""Dynamic Delegation of Authority (DOA) matrix API (spec 24.2).

Organizations manage approval matrices in the database instead of relying
on hardcoded thresholds. The resolve endpoint drives the same decision the
signature authority engine uses, so value bands and parallel/sequential
fan-out are configurable per organization.
"""
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.currency_service import resolve_currency
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.services.doa_service import (
    DoaMatrixError,
    create_matrix,
    list_matrices,
    resolve_doa_matrix,
)

router = APIRouter(prefix="/doa", tags=["Delegation of Authority"])


class DoaStageRequest(BaseModel):
    name: Optional[str] = None
    required_role: Optional[str] = None
    execution_mode: str = Field(default="sequential", pattern="^(sequential|parallel)$")
    require_all_approvers: bool = True


class DoaMatrixCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    stages: list[DoaStageRequest] = []


@router.get("/resolve")
async def resolve(
    agreement_value: float = Query(..., gt=0),
    currency: str | None = Query(default=None),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Resolve the approval matrix that applies to an agreement value."""
    result = await resolve_doa_matrix(
        db,
        organization_id=org_id,
        agreement_value=agreement_value,
        currency=resolve_currency(currency),
    )
    return result


@router.get("/matrices")
async def list_doa_matrices(
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List the organization's configured DOA matrices."""
    return await list_matrices(db, org_id)


@router.post("/matrices", status_code=status.HTTP_201_CREATED)
async def create_doa_matrix(
    data: DoaMatrixCreateRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a DOA matrix with its approval stages."""
    try:
        definition = await create_matrix(
            db,
            org_id,
            name=data.name,
            description=data.description,
            min_value=data.min_value,
            max_value=data.max_value,
            stages=[s.model_dump() for s in data.stages],
        )
    except DoaMatrixError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return {
        "id": str(definition.id),
        "name": definition.name,
        "min_value": definition.min_value,
        "max_value": definition.max_value,
    }