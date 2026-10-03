from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.organization import Organization
from app.models.rbac import OrganizationMember
from app.models.user import User
from app.schemas.organization import (
    MembershipOptionResponse,
    OrganizationCreate,
    OrganizationResponse,
)

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("/me", response_model=OrganizationResponse)
async def get_my_organization(
    org_id=Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()

    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    return org


@router.get(
    "/me/memberships",
    response_model=list[MembershipOptionResponse],
    status_code=status.HTTP_200_OK,
)
async def list_my_memberships(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Organizations this user may act in.

    Deliberately does not depend on ``get_current_organization_id``: a user
    with several memberships is exactly the case where that dependency
    refuses without ``X-Organization-Id``, so it cannot be the way to
    discover the options. This endpoint is what a client calls to find out
    what to send.

    Only active memberships are returned. Suspended or revoked rows must not
    appear as switch targets even though the row itself may still exist.

    ``role_id`` is returned but not the role's name: ``roles`` is under RLS
    keyed on the tenant being resolved, so the name is not readable until a
    tenant context exists. Clients get the display name after switching.
    """
    rows = (
        await db.execute(
            select(OrganizationMember.organization_id, OrganizationMember.role_id, Organization)
            .join(Organization, Organization.id == OrganizationMember.organization_id)
            .where(
                OrganizationMember.user_id == current_user.id,
                OrganizationMember.status == "active",
            )
            .order_by(Organization.name)
        )
    ).all()

    return [
        MembershipOptionResponse(
            organization_id=organization_id,
            name=org.name,
            slug=org.slug,
            role_id=role_id,
        )
        for organization_id, role_id, org in rows
    ]


@router.get(
    "/me/members",
    status_code=status.HTTP_200_OK,
)
async def get_members(
    org_id=Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.status == "active",
        )
    )
    members = result.scalars().all()

    return [
        {
            "id": str(m.id),
            "user_id": str(m.user_id),
            "role_id": str(m.role_id),
            "status": m.status,
        }
        for m in members
    ]
