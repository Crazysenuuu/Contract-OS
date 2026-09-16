"""Termination API endpoints."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement
from app.models.termination import AgreementTermination, PostTerminationObligation
from app.models.user import User
from app.services.termination_service import (
    TerminationError,
    cancel_termination,
    complete_termination,
    initiate_termination,
    issue_notice,
    record_cure,
)

router = APIRouter(
    prefix="/agreements/{agreement_id}/terminations",
    tags=["terminations"],
)


class InitiateRequest(BaseModel):
    reason_code: str
    reason_detail: str | None = None
    notice_period_days: int | None = None
    cure_required: bool = False
    cure_period_days: int | None = None


class NoticeRequest(BaseModel):
    notice_date: str | None = None
    evidence: dict | None = None


class CureRequest(BaseModel):
    cured: bool = True


class CompleteRequest(BaseModel):
    effective_date: str | None = None
    force: bool = False


def _termination_out(term: AgreementTermination) -> dict:
    return {
        "id": str(term.id),
        "agreement_id": str(term.agreement_id),
        "initiated_by": str(term.initiated_by),
        "initiated_at": term.initiated_at.isoformat(),
        "reason_code": term.reason_code,
        "reason_detail": term.reason_detail,
        "notice_date": term.notice_date.isoformat() if term.notice_date else None,
        "notice_period_days": term.notice_period_days,
        "notice_served": term.notice_served,
        "notice_evidence": term.notice_evidence,
        "cure_required": term.cure_required,
        "cure_period_days": term.cure_period_days,
        "cure_deadline": term.cure_deadline.isoformat() if term.cure_deadline else None,
        "cured": term.cured,
        "effective_date": term.effective_date.isoformat() if term.effective_date else None,
        "status": term.status,
        "obligations_check": term.obligations_check,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_termination(
    agreement_id: uuid.UUID,
    data: InitiateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    try:
        term = await initiate_termination(
            db,
            agreement=agreement,
            initiated_by=current_user.id,
            reason_code=data.reason_code,
            reason_detail=data.reason_detail,
            notice_period_days=data.notice_period_days,
            cure_required=data.cure_required,
            cure_period_days=data.cure_period_days,
            org_id=org_id,
        )
    except TerminationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _termination_out(term)


@router.get("")
async def list_terminations(
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
        select(AgreementTermination)
        .where(AgreementTermination.agreement_id == agreement_id)
        .order_by(AgreementTermination.initiated_at)
    )
    return [_termination_out(t) for t in result.scalars().all()]


@router.get("/{termination_id}")
async def get_termination(
    agreement_id: uuid.UUID,
    termination_id: uuid.UUID,
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
        select(AgreementTermination).where(
            AgreementTermination.id == termination_id,
            AgreementTermination.agreement_id == agreement_id,
        )
    )
    term = result.scalar_one_or_none()
    if term is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Termination not found")
    return _termination_out(term)


@router.get("/{termination_id}/obligations")
async def get_termination_obligations(
    agreement_id: uuid.UUID,
    termination_id: uuid.UUID,
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
        select(PostTerminationObligation).where(
            PostTerminationObligation.termination_id == termination_id
        )
    )
    return [
        {
            "id": str(o.id),
            "owner_party": o.owner_party,
            "description": o.description,
            "obligation_type": o.obligation_type,
            "due_date": o.due_date.isoformat() if o.due_date else None,
            "status": o.status,
        }
        for o in result.scalars().all()
    ]


@router.post("/{termination_id}/notice")
async def issue_termination_notice(
    agreement_id: uuid.UUID,
    termination_id: uuid.UUID,
    data: NoticeRequest,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    term = await _get_term_or_404(db, agreement_id, termination_id)
    try:
        term = await issue_notice(
            db,
            termination=term,
            notice_date=date.fromisoformat(data.notice_date) if data.notice_date else None,
            evidence=data.evidence,
            actor_id=current_user.id,
            org_id=org_id,
        )
    except TerminationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _termination_out(term)


@router.post("/{termination_id}/cure")
async def record_termination_cure(
    agreement_id: uuid.UUID,
    termination_id: uuid.UUID,
    data: CureRequest,
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
    term = await _get_term_or_404(db, agreement_id, termination_id)
    try:
        term = await record_cure(
            db,
            termination=term,
            actor_id=current_user.id,
            org_id=org_id,
            cured=data.cured,
        )
    except TerminationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _termination_out(term)


@router.post("/{termination_id}/complete")
async def complete_termination_endpoint(
    agreement_id: uuid.UUID,
    termination_id: uuid.UUID,
    data: CompleteRequest,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    term = await _get_term_or_404(db, agreement_id, termination_id)
    try:
        term = await complete_termination(
            db,
            termination=term,
            agreement=agreement,
            completed_by=current_user.id,
            org_id=org_id,
            effective_date=date.fromisoformat(data.effective_date) if data.effective_date else None,
            force=data.force,
        )
    except TerminationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _termination_out(term)


@router.post("/{termination_id}/cancel")
async def cancel_termination_endpoint(
    agreement_id: uuid.UUID,
    termination_id: uuid.UUID,
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
    term = await _get_term_or_404(db, agreement_id, termination_id)
    try:
        term = await cancel_termination(
            db,
            termination=term,
            actor_id=current_user.id,
            org_id=org_id,
        )
    except TerminationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _termination_out(term)


async def _get_term_or_404(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    termination_id: uuid.UUID,
) -> AgreementTermination:
    result = await db.execute(
        select(AgreementTermination).where(
            AgreementTermination.id == termination_id,
            AgreementTermination.agreement_id == agreement_id,
        )
    )
    term = result.scalar_one_or_none()
    if term is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Termination not found")
    return term