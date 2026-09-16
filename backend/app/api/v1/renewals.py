"""Renewal API endpoints.

Wires ContractRenewal/RenewalReminder into the lifecycle: configure terms,
evaluate/process an upcoming renewal, serve non-renewal notice, schedule
reminders.
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
from app.models.renewal import ContractRenewal, RenewalReminder
from app.models.user import User
from app.services.renewal_service import (
    RenewalError,
    get_or_create_renewal,
    mark_notice_given,
    process_renewal,
    schedule_reminders,
    update_renewal_config,
)

router = APIRouter(
    prefix="/agreements/{agreement_id}/renewal",
    tags=["renewals"],
)


class ConfigRequest(BaseModel):
    is_renewable: bool | None = None
    auto_renew: bool | None = None
    renewal_term_months: int | None = None
    max_renewals: int | None = None
    notice_period_days: int | None = None
    price_increase_percentage: float | None = None
    price_fixed_amount: float | None = None


class ProcessRequest(BaseModel):
    force: bool = False


def _renewal_out(renewal: ContractRenewal) -> dict:
    return {
        "id": str(renewal.id),
        "agreement_id": str(renewal.agreement_id),
        "is_renewable": renewal.is_renewable,
        "auto_renew": renewal.auto_renew,
        "renewal_term_months": renewal.renewal_term_months,
        "max_renewals": renewal.max_renewals,
        "current_renewal_count": renewal.current_renewal_count,
        "original_expiry_date": renewal.original_expiry_date.isoformat() if renewal.original_expiry_date else None,
        "current_expiry_date": renewal.current_expiry_date.isoformat() if renewal.current_expiry_date else None,
        "next_renewal_date": renewal.next_renewal_date.isoformat() if renewal.next_renewal_date else None,
        "last_renewal_date": renewal.last_renewal_date.isoformat() if renewal.last_renewal_date else None,
        "notice_period_days": renewal.notice_period_days,
        "notice_given": renewal.notice_given,
        "notice_given_date": renewal.notice_given_date.isoformat() if renewal.notice_given_date else None,
        "price_increase_percentage": renewal.price_increase_percentage,
        "price_fixed_amount": renewal.price_fixed_amount,
        "status": renewal.status,
        "renewal_history": renewal.renewal_history or [],
    }


@router.get("")
async def get_renewal(
    agreement_id: uuid.UUID,
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
    renewal = await get_or_create_renewal(db, agreement)
    return _renewal_out(renewal)


@router.get("/reminders")
async def list_reminders(
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
        select(RenewalReminder)
        .join(ContractRenewal)
        .where(ContractRenewal.agreement_id == agreement_id)
        .order_by(RenewalReminder.reminder_date)
    )
    return [
        {
            "id": str(r.id),
            "renewal_id": str(r.renewal_id),
            "reminder_date": r.reminder_date.isoformat() if r.reminder_date else None,
            "reminder_type": r.reminder_type,
            "status": r.status,
            "sent_at": r.sent_at.isoformat() if r.sent_at else None,
        }
        for r in result.scalars().all()
    ]


@router.post("/reminders/schedule")
async def schedule_renewal_reminders(
    agreement_id: uuid.UUID,
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
    renewal = await get_or_create_renewal(db, agreement)
    reminders = await schedule_reminders(db, renewal)
    return {
        "created": len(reminders),
        "next_renewal_date": renewal.next_renewal_date.isoformat() if renewal.next_renewal_date else None,
    }


@router.put("")
async def configure_renewal(
    agreement_id: uuid.UUID,
    data: ConfigRequest,
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
    renewal = await get_or_create_renewal(db, agreement)
    renewal = await update_renewal_config(
        db, renewal, **data.model_dump(exclude_unset=True)
    )
    return _renewal_out(renewal)


@router.post("/process")
async def process_renewal_endpoint(
    agreement_id: uuid.UUID,
    data: ProcessRequest | None = None,
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
    renewal = await get_or_create_renewal(db, agreement)
    try:
        result = await process_renewal(
            db,
            renewal=renewal,
            agreement=agreement,
            org_id=org_id,
            actor_id=current_user.id,
            force=data.force if data else False,
        )
    except RenewalError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    result["renewal"] = _renewal_out(renewal)
    return result


@router.post("/notice")
async def give_non_renewal_notice(
    agreement_id: uuid.UUID,
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
    renewal = await get_or_create_renewal(db, agreement)
    renewal = await mark_notice_given(db, renewal)
    return _renewal_out(renewal)