"""Authorization API endpoints.

Check permissions and manage agreement participant permissions.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.services.authorization_service import AuthorizationService

router = APIRouter(
    prefix="/agreements",
    tags=["authorization"],
)

auth_service = AuthorizationService()


# --- Schemas ---


class PermissionCheckRequest(BaseModel):
    permission_key: str


class PermissionCheckResponse(BaseModel):
    allowed: bool
    reason: str
    participant_id: str | None = None
    party_id: str | None = None


class UserPermissionsResponse(BaseModel):
    permissions: list[str]


class GrantPermissionRequest(BaseModel):
    participant_id: UUID
    permission_key: str
    granted: bool = True


# --- Endpoints ---


@router.post(
    "/{agreement_id}/check-permission",
    response_model=PermissionCheckResponse,
)
async def check_permission(
    agreement_id: UUID,
    data: PermissionCheckRequest,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Check if the current user has a specific permission on an agreement.

    Returns the authorization decision with reason.
    """
    decision = await auth_service.authorize_agreement_action(
        db,
        user_id=current_user.id,
        organization_id=org_id,
        agreement_id=agreement_id,
        permission_key=data.permission_key,
    )

    return PermissionCheckResponse(
        allowed=decision.allowed,
        reason=decision.reason,
        participant_id=str(decision.participant_id) if decision.participant_id else None,
        party_id=str(decision.party_id) if decision.party_id else None,
    )


@router.get(
    "/{agreement_id}/my-permissions",
    response_model=UserPermissionsResponse,
)
async def get_my_permissions(
    agreement_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all permissions for the current user on an agreement."""
    permissions = await auth_service.get_user_permissions(
        db,
        user_id=current_user.id,
        agreement_id=agreement_id,
    )

    return UserPermissionsResponse(permissions=permissions)


@router.post(
    "/{agreement_id}/grant-permission",
    status_code=status.HTTP_201_CREATED,
)
async def grant_permission(
    agreement_id: UUID,
    data: GrantPermissionRequest,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Grant a permission to an agreement participant.

    Requires manage_participants permission.
    """
    # Verify the granter has manage_participants permission
    decision = await auth_service.authorize_agreement_action(
        db,
        user_id=current_user.id,
        organization_id=org_id,
        agreement_id=agreement_id,
        permission_key="agreement.manage_participants",
    )

    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cannot manage participants: {decision.reason}",
        )

    # Grant the permission
    from app.models.authorization import AgreementParticipantPermission
    from app.models.agreement_access import AgreementParticipant

    # Verify participant exists
    participant_result = await db.execute(
        select(AgreementParticipant).where(
            AgreementParticipant.id == data.participant_id,
            AgreementParticipant.agreement_id == agreement_id,
        )
    )
    participant = participant_result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Participant not found",
        )

    # Check if permission already exists
    existing = await db.execute(
        select(AgreementParticipantPermission).where(
            AgreementParticipantPermission.agreement_participant_id
            == data.participant_id,
            AgreementParticipantPermission.permission_key == data.permission_key,
        )
    )
    perm = existing.scalar_one_or_none()

    if perm:
        perm.granted = data.granted
    else:
        perm = AgreementParticipantPermission(
            agreement_participant_id=data.participant_id,
            permission_key=data.permission_key,
            granted=data.granted,
        )
        db.add(perm)

    await db.flush()

    return {
        "status": "granted" if data.granted else "revoked",
        "permission_key": data.permission_key,
        "participant_id": str(data.participant_id),
    }


@router.get(
    "/{agreement_id}/participants/{participant_id}/permissions",
    response_model=UserPermissionsResponse,
)
async def get_participant_permissions(
    agreement_id: UUID,
    participant_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get all permissions for a specific participant."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    from app.models.authorization import AgreementParticipantPermission

    result = await db.execute(
        select(AgreementParticipantPermission.permission_key).where(
            AgreementParticipantPermission.agreement_participant_id
            == participant_id,
            AgreementParticipantPermission.granted == True,
        )
    )
    permissions = [row[0] for row in result.all()]

    return UserPermissionsResponse(permissions=permissions)
