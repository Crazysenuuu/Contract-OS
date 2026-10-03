from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.rbac import OrganizationMember
from app.models.user import User
from app.services.tenant_context import set_tenant_context

#: Header a client sets to pick which of its organizations a request acts in.
#: Only meaningful for users holding more than one active membership.
ORG_HEADER = "X-Organization-Id"


async def get_current_organization_id(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """
    Returns the organization_id this request acts in.

    This is the tenant boundary. Every query must filter on this.

    A user may hold several active memberships, so the tenant cannot be
    inferred from the account alone. Previously this took whichever row the
    database happened to return first — a query with ``LIMIT 1`` and no
    ORDER BY, so the same account could read and write against different
    organizations across replicas or plan changes. Resolution is therefore
    explicit:

    - no active membership: 403
    - exactly one: used, so single-org clients need no header
    - several: ``X-Organization-Id`` is required, and must be one of them
    """
    memberships = (
        await db.execute(
            select(OrganizationMember.organization_id).where(
                OrganizationMember.user_id == current_user.id,
                OrganizationMember.status == "active",
            )
        )
    ).scalars().all()

    if not memberships:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of any organization",
        )

    requested = request.headers.get(ORG_HEADER)

    if requested is None:
        if len(memberships) == 1:
            org_id = memberships[0]
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "User belongs to multiple organizations; "
                    f"specify one with the {ORG_HEADER} header"
                ),
            )
    else:
        try:
            org_id = UUID(requested)
        except ValueError:
            # A malformed header is a client bug, not an auth failure.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{ORG_HEADER} must be a UUID",
            ) from None
        if org_id not in memberships:
            # Deliberately the same response as "not a member at all": a
            # caller must not be able to probe which organizations exist.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not a member of this organization",
            )

    # Set the RLS tenant context for the rest of this transaction
    # (no-op on non-PostgreSQL dialects).
    await set_tenant_context(db, org_id)

    return org_id
