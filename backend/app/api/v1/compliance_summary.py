"""Compliance dashboard summary API (spec §89).

GET /compliance/summary  — executive compliance widget data
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.services.compliance_summary import get_compliance_summary

router = APIRouter(prefix="/compliance", tags=["Compliance Summary"])


@router.get("/summary")
async def compliance_summary(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Spec §89 — compliance dashboard widget data."""
    import uuid

    return await get_compliance_summary(db, org_id=uuid.UUID(str(org_id)))
