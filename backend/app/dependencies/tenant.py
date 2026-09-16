from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.rbac import OrganizationMember
from app.models.user import User
from app.services.tenant_context import set_tenant_context


async def get_current_organization_id(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """
    Returns the organization_id for the current user's active membership.

    This is the tenant boundary. Every query must filter on this.
    """
    result = await db.execute(
        select(OrganizationMember.organization_id)
        .where(
            OrganizationMember.user_id == current_user.id,
            OrganizationMember.status == "active",
        )
        .limit(1)
    )

    org_id = result.scalar_one_or_none()

    if org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of any organization",
        )

    # Set the RLS tenant context for the rest of this transaction
    # (no-op on non-PostgreSQL dialects).
    await set_tenant_context(db, org_id)

    return org_id
