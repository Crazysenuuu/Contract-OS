"""Amendment API endpoints.

CRUD + activation for amendments, plus the consolidated current-terms
view (2.07.26) for an agreement.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement
from app.models.amendment import AgreementAmendment
from app.models.user import User
from app.dependencies.rbac import require_permission
from app.schemas.amendment import AmendmentResponse
from app.services.amendment_service import (
    AmendmentError,
    activate_amendment,
    create_amendment,
)
from app.services.lifecycle_service import get_current_terms

router = APIRouter(
    prefix="/agreements/{agreement_id}/amendments",
    tags=["amendments"],
)

_perm_agreement_amend = Depends(require_permission("agreement.amend"))


class AmendmentChangeRequest(BaseModel):
    section_key: str
    change_type: str = "replace"
    old_text: str | None = None
    new_text: str
    structured_delta: dict | None = None


class AmendmentCreateRequest(BaseModel):
    title: str
    description: str | None = None
    reason: str | None = None
    changes: list[AmendmentChangeRequest]


class ActivateRequest(BaseModel):
    effective_date: str | None = None


@router.post(
    "",
    response_model=AmendmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_amendment_endpoint(
    agreement_id: uuid.UUID,
    data: AmendmentCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a new amendment proposal for an agreement."""
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    try:
        amendment = await create_amendment(
            db,
            agreement=agreement,
            title=data.title,
            description=data.description,
            reason=data.reason,
            changes=[c.model_dump() for c in data.changes],
            proposed_by=current_user.id,
        )
    except AmendmentError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return amendment


@router.get(
    "",
    response_model=list[AmendmentResponse],
)
async def list_amendments(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    result = await db.execute(
        select(AgreementAmendment)
        .where(AgreementAmendment.agreement_id == agreement_id)
        .order_by(AgreementAmendment.amendment_number)
    )
    return result.scalars().all()


@router.get(
    "/{amendment_id}",
    response_model=AmendmentResponse,
)
async def get_amendment(
    agreement_id: uuid.UUID,
    amendment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    result = await db.execute(
        select(AgreementAmendment).where(
            AgreementAmendment.id == amendment_id,
            AgreementAmendment.agreement_id == agreement_id,
        )
    )
    amendment = result.scalar_one_or_none()
    if amendment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Amendment not found")
    return amendment


@router.post("/{amendment_id}/activate", response_model=AmendmentResponse, dependencies=[_perm_agreement_amend])
async def activate_amendment_endpoint(
    agreement_id: uuid.UUID,
    amendment_id: uuid.UUID,
    data: ActivateRequest | None = None,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    result = await db.execute(
        select(AgreementAmendment).where(
            AgreementAmendment.id == amendment_id,
            AgreementAmendment.agreement_id == agreement_id,
        )
    )
    amendment = result.scalar_one_or_none()
    if amendment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Amendment not found")

    effective_date = None
    if data and data.effective_date:
        from datetime import date
        effective_date = date.fromisoformat(data.effective_date)

    try:
        return await activate_amendment(
            db,
            amendment=amendment,
            activated_by=current_user.id,
            effective_date=effective_date,
            org_id=org_id,
        )
    except AmendmentError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{amendment_id}/current-terms")
async def amendment_current_terms(
    agreement_id: uuid.UUID,
    amendment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Consolidated current terms after this amendment (and prior ones)."""
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    return await get_current_terms(db, agreement)