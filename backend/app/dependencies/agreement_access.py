"""Agreement access dependency.

Enforces that:
1. User is authenticated
2. User is a member of the organization
3. User is explicitly a participant on the agreement (or has an access grant)

Being a member of a company does NOT automatically grant access to an agreement.
"""

import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement
from app.models.agreement_access import (
    AgreementAccessGrant,
    AgreementParticipant,
)
from app.models.user import User


async def verify_agreement_access(
    agreement_id: uuid.UUID,
    permission: str | None = None,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
) -> Agreement:
    """Verify the current user has access to the agreement.

    Args:
        agreement_id: The agreement to check access for.
        permission: Optional specific permission key to verify.
            If None, just checks basic view access.
        current_user: Authenticated user (injected).
        org_id: Organization ID from JWT (injected).
        db: Database session (injected).

    Returns:
        The Agreement object if access is granted.

    Raises:
        HTTPException 404: If agreement not found in this org.
        HTTPException 403: If user has no access.
    """
    # 1. Verify agreement exists and belongs to this org
    result = await db.execute(
        select(Agreement).where(
            Agreement.id == agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()

    if agreement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement not found",
        )

    # 2. Agreement creator always has full access
    if agreement.created_by == current_user.id:
        return agreement

    # 3. Check if user is a participant on this agreement
    participant_result = await db.execute(
        select(AgreementParticipant).where(
            AgreementParticipant.agreement_id == agreement_id,
            AgreementParticipant.user_id == current_user.id,
            AgreementParticipant.status == "active",
        )
    )
    participant = participant_result.scalar_one_or_none()

    if participant is not None:
        # Check specific permission if requested
        if permission is not None:
            if not _has_permission(participant, permission):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing permission: {permission}",
                )
        return agreement

    # 4. Check access grants
    grant_result = await db.execute(
        select(AgreementAccessGrant).where(
            AgreementAccessGrant.agreement_id == agreement_id,
            AgreementAccessGrant.user_id == current_user.id,
            AgreementAccessGrant.permission_key == permission
            if permission
            else True,
            AgreementAccessGrant.status == "active",
        )
    )
    grant = grant_result.scalar_one_or_none()

    if grant is not None:
        # Check expiration
        if grant.expires_at is not None:
            from datetime import datetime, timezone

            if grant.expires_at < datetime.now(timezone.utc):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access grant has expired",
                )
        return agreement

    # 5. No access found
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have access to this agreement",
    )


def _has_permission(
    participant: AgreementParticipant,
    permission: str,
) -> bool:
    """Check if a participant has a specific permission.

    Permission mapping:
        agreement.view -> can_view
        agreement.comment -> can_comment
        agreement.propose_change -> can_propose_changes
        agreement.approve -> can_approve
        agreement.sign -> can_sign
    """
    permission_map = {
        "agreement.view": participant.can_view,
        "agreement.comment": participant.can_comment,
        "agreement.propose_change": participant.can_propose_changes,
        "agreement.approve": participant.can_approve,
        "agreement.sign": participant.can_sign,
        "agreement.request_signature": participant.can_sign,
        "agreement.approve_termination": participant.can_approve,
        "agreement.manage_participants": participant.can_approve,
    }

    return permission_map.get(permission, False)
