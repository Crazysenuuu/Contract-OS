from uuid import UUID
from typing import Set

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user_id
from app.models.rbac import OrganizationMember
from app.models.user import User


async def get_current_user(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> User:
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if user.status not in ("active", "pending_verification"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is not active",
        )

    return user


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    # Admin MFA enforcement (spec §2.17): when an admin has enrolled in MFA
    # they must have completed verification; admins who never enrolled are
    # flagged so deployment can require enrollment before going live.
    if current_user.mfa_enabled and not current_user.mfa_secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin MFA configuration is incomplete",
        )
    return current_user


async def get_user_org_ids(
    db: AsyncSession,
    user_id: UUID,
) -> Set[UUID]:
    """IDs of the organizations the user is an active member of."""
    result = await db.execute(
        select(OrganizationMember.organization_id).where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.status == "active",
        )
    )
    return {row[0] for row in result.all()}
