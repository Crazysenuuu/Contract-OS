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
from app.models.termination import (
    AgreementTermination,
    PostTerminationObligation,
    TerminationSettlement,
    TerminationSettlementItem,
)
from app.models.user import User
from app.dependencies.rbac import require_permission
from app.services.termination_service import (
    TerminationError,
    cancel_termination,
    complete_termination,
    generate_settlement,
    initiate_termination,
    issue_notice,
    record_cure,
    settle_termination,
    update_settlement_item,
)

router = APIRouter(
    prefix="/agreements/{agreement_id}/terminations",
    tags=["terminations"],
)

_perm_agreement_terminate = Depends(require_permission("agreement.terminate"))


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


class SettlementItemUpdateRequest(BaseModel):
    status: str  # 'open' | 'resolved' | 'waived'
    resolution_note: str | None = None


class SettleRequest(BaseModel):
    notes: str | None = None


def _money_out(settlement) -> dict:
    """Serialize the financial position without floating-point."""
    minor = settlement.outstanding_amount_minor
    return {
        "outstanding_amount_minor": minor,
        "currency": settlement.currency,
        "outstanding_amount_display": (
            f"{(minor // 100)}.{minor % 100:02d}" if minor is not None else None
        ),
    }


def _settlement_out(settlement) -> dict:
    return {
        "id": str(settlement.id),
        "termination_id": str(settlement.termination_id),
        **_money_out(settlement),
        "obligations_remaining": settlement.obligations_remaining,
        "status": settlement.status,
        "notes": settlement.notes,
        "settled_by": (
            str(settlement.settled_by) if settlement.settled_by else None
        ),
        "settled_at": (
            settlement.settled_at.isoformat() if settlement.settled_at else None
        ),
    }


def _settlement_item_out(item: TerminationSettlementItem) -> dict:
    return {
        "id": str(item.id),
        "settlement_id": str(item.settlement_id),
        "item_key": item.item_key,
        "description": item.description,
        "category": item.category,
        # Spec 1.17 §15: required-by-agreement vs recommended operational.
        "kind": item.kind,
        "blocks_completion": item.blocks_completion,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "status": item.status,
        "resolved_by": str(item.resolved_by) if item.resolved_by else None,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "resolution_note": item.resolution_note,
    }


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


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[_perm_agreement_terminate])
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


@router.post("/{termination_id}/complete", dependencies=[_perm_agreement_terminate])
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


# ===========================================================================
# Settlement endpoints (spec 1.17 §15-16)
# ===========================================================================


@router.get("/{termination_id}/settlement")
async def get_settlement(
    agreement_id: uuid.UUID,
    termination_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """The settlement with its full checklist for a termination."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    term = await _get_term_or_404(db, agreement_id, termination_id)
    settlement_result = await db.execute(
        select(TerminationSettlement).where(
            TerminationSettlement.termination_id == termination_id
        )
    )
    settlement = settlement_result.scalar_one_or_none()
    if settlement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Settlement not found",
        )
    items_result = await db.execute(
        select(TerminationSettlementItem)
        .where(TerminationSettlementItem.settlement_id == settlement.id)
        .order_by(TerminationSettlementItem.position)
    )
    items = items_result.scalars().all()
    return {
        **_settlement_out(settlement),
        "items": [_settlement_item_out(i) for i in items],
    }


@router.post("/{termination_id}/settlement/regenerate")
async def regenerate_settlement(
    agreement_id: uuid.UUID,
    termination_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Rebuild the checklist from current obligations (keeps item states)."""
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    term = await _get_term_or_404(db, agreement_id, termination_id)
    if term.status == "effective":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot regenerate the settlement of an effective termination",
        )
    try:
        settlement = await generate_settlement(
            db,
            termination=term,
            agreement=agreement,
            replace_existing=True,
        )
    except TerminationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _settlement_out(settlement)


@router.patch(
    "/{termination_id}/settlement/items/{item_id}",
    dependencies=[_perm_agreement_terminate],
)
async def update_settlement_item_endpoint(
    agreement_id: uuid.UUID,
    termination_id: uuid.UUID,
    item_id: uuid.UUID,
    data: SettlementItemUpdateRequest,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Resolve, waive or re-open a checklist item."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    term = await _get_term_or_404(db, agreement_id, termination_id)
    settlement_result = await db.execute(
        select(TerminationSettlement).where(
            TerminationSettlement.termination_id == termination_id
        )
    )
    settlement = settlement_result.scalar_one_or_none()
    if settlement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Settlement not found",
        )
    result = await db.execute(
        select(TerminationSettlementItem).where(
            TerminationSettlementItem.id == item_id,
            TerminationSettlementItem.settlement_id == settlement.id,
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Settlement item not found",
        )
    try:
        item = await update_settlement_item(
            db,
            item=item,
            actor_id=current_user.id,
            status=data.status,
            resolution_note=data.resolution_note,
        )
    except TerminationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _settlement_item_out(item)


@router.post(
    "/{termination_id}/settlement/settle",
    dependencies=[_perm_agreement_terminate],
)
async def settle_termination_endpoint(
    agreement_id: uuid.UUID,
    termination_id: uuid.UUID,
    data: SettleRequest,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Mark the settlement settled once no required items remain open."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    term = await _get_term_or_404(db, agreement_id, termination_id)
    try:
        settlement = await settle_termination(
            db,
            termination=term,
            actor_id=current_user.id,
            org_id=org_id,
            notes=data.notes,
        )
    except TerminationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _settlement_out(settlement)