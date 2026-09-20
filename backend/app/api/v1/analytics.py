"""Analytics API (spec §84-85).

GET /analytics/executive  — full executive analytics
GET /analytics/financial   — financial contract analytics
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.services.analytics_service import (
    get_executive_analytics,
    get_financial_analytics,
    get_supplier_performance,
)
from app.services.risk_scoring import compute_deterministic_risk

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/executive")
async def executive_analytics(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Spec §84 — executive-level analytics across all agreements."""
    import uuid

    return await get_executive_analytics(db, org_id=uuid.UUID(str(org_id)))


@router.get("/financial")
async def financial_analytics(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Spec §85 — financial contract analytics (committed spend, obligations)."""
    import uuid

    return await get_financial_analytics(db, org_id=uuid.UUID(str(org_id)))


@router.get("/supplier-performance")
async def supplier_performance(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Spec §84/86 — per-supplier performance metrics."""
    import uuid

    return await get_supplier_performance(db, org_id=uuid.UUID(str(org_id)))


@router.get("/risk-score/{agreement_id}")
async def deterministic_risk_score(
    agreement_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Spec §39 — deterministic, explainable risk score for one agreement."""
    import uuid

    score = await compute_deterministic_risk(db, agreement_id=uuid.UUID(agreement_id))
    return {
        "overall": score.overall,
        "level": score.level,
        "factors_count": score.factors_count,
        "explanation": score.explanation,
        "components": [
            {
                "category": c.category,
                "score": c.score,
                "weight": c.weight,
                "weighted_score": c.weighted_score,
                "contributing_factors": c.contributing_factors,
            }
            for c in score.components
        ],
    }
