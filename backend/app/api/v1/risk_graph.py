"""Contract Risk Graph + AI Copilot API (spec 35/36)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement
from app.models.user import User
from app.services.risk_graph_service import (
    agreements_expiring_within,
    agreements_with_open_obligations,
    build_agreement_graph,
    graph_stats,
    high_risk_agreements,
    impact_traversal,
)

router = APIRouter(prefix="/risk-graph", tags=["Contract Risk Graph"])
copilot_router = APIRouter(prefix="/copilot", tags=["AI Copilot"])


class CopilotAsk(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    agreement_id: uuid.UUID | None = None


async def _get_agreement_or_404(
    db: AsyncSession, agreement_id: uuid.UUID, org_id: uuid.UUID
) -> Agreement:
    result = await db.execute(
        select(Agreement).where(
            Agreement.id == agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()
    if agreement is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    return agreement


@router.post("/agreements/{agreement_id}/build", status_code=status.HTTP_200_OK)
async def build_graph(
    agreement_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Build/refresh the risk graph subgraph for an agreement."""
    agreement = await _get_agreement_or_404(db, agreement_id, org_id)
    result = await build_agreement_graph(
        db, organization_id=org_id, agreement=agreement
    )
    await db.commit()
    return result


@router.get("/stats")
async def stats(
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await graph_stats(db, organization_id=org_id)


@copilot_router.post("/ask")
async def copilot_ask(
    data: CopilotAsk,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """AI Copilot: grounded answer over the contract corpus (spec 36)."""
    from app.services.copilot_service import CopilotError, copilot_answer

    if data.agreement_id is not None:
        await _get_agreement_or_404(db, data.agreement_id, org_id)

    try:
        result = await copilot_answer(
            db,
            organization_id=org_id,
            user_id=current_user.id,
            question=data.question,
            agreement_id=data.agreement_id,
        )
    except CopilotError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.get("/open-obligations")
async def open_obligations(
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Which counterparties have open obligations right now."""
    return await agreements_with_open_obligations(db, organization_id=org_id)


@router.get("/expiring")
async def expiring(
    days: int = Query(90, ge=1, le=730),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Which contracts expire within the window."""
    return await agreements_expiring_within(db, organization_id=org_id, days=days)


@router.get("/high-risk")
async def high_risk(
    min_edges: int = Query(1, ge=0),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Agreements ranked by accumulated open-risk edge weight."""
    return await high_risk_agreements(db, organization_id=org_id, min_edges=min_edges)


@router.get("/impact/{agreement_id}")
async def impact(
    agreement_id: uuid.UUID,
    min_risk: float = Query(0.4, ge=0.0, le=1.0),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Traverse from an agreement to related high-risk exposure.

    Surfaces the source agreement's material-risk clauses plus the other
    agreements that share a counterparty with it (impact radius).
    """
    await _get_agreement_or_404(db, agreement_id, org_id)
    return await impact_traversal(
        db, organization_id=org_id, agreement_id=agreement_id, min_risk=min_risk
    )


