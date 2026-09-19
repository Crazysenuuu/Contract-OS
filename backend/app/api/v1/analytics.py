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
)

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
