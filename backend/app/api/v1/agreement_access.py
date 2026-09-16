"""Agreement access API endpoints.

Manage parties, participants, and legal representatives for agreements.
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement
from app.models.agreement_access import (
    AgreementAccessGrant,
    AgreementParticipant,
    AgreementParty,
    LegalRepresentative,
    LegalRepresentativeAssignment,
)
from app.models.user import User

router = APIRouter(
    prefix="/agreements",
    tags=["agreement-access"],
)


# --- Schemas ---


class PartyCreate(BaseModel):
    legal_entity_id: UUID
    party_role: str  # 'disclosing', 'receiving', 'disclosing_and_receiving'
    display_name: str | None = None
    signatory_required: bool = True


class PartyResponse(BaseModel):
    id: UUID
    agreement_id: UUID
    legal_entity_id: UUID
    party_role: str
    display_name: str | None
    signatory_required: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ParticipantCreate(BaseModel):
    user_id: UUID
    agreement_party_id: UUID
    participant_role: str  # 'signatory', 'lawyer', 'legal_reviewer', etc.
    can_view: bool = True
    can_comment: bool = False
    can_propose_changes: bool = False
    can_approve: bool = False
    can_sign: bool = False


class ParticipantResponse(BaseModel):
    id: UUID
    agreement_id: UUID
    agreement_party_id: UUID
    user_id: UUID
    participant_role: str
    status: str
    can_view: bool
    can_comment: bool
    can_propose_changes: bool
    can_approve: bool
    can_sign: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AccessGrantCreate(BaseModel):
    user_id: UUID
    permission_key: str
    expires_at: datetime | None = None


class AccessGrantResponse(BaseModel):
    id: UUID
    agreement_id: UUID
    user_id: UUID
    permission_key: str
    granted_by: UUID
    status: str
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Party Endpoints ---


@router.post(
    "/{agreement_id}/parties",
    response_model=PartyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_party(
    agreement_id: UUID,
    data: PartyCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Add a legal entity as a party to the agreement."""
    # Verify access
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    # Check for duplicate party
    existing = await db.execute(
        select(AgreementParty).where(
            AgreementParty.agreement_id == agreement_id,
            AgreementParty.legal_entity_id == data.legal_entity_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This legal entity is already a party to this agreement",
        )

    party = AgreementParty(
        agreement_id=agreement_id,
        legal_entity_id=data.legal_entity_id,
        party_role=data.party_role,
        display_name=data.display_name,
        signatory_required=data.signatory_required,
    )
    db.add(party)
    await db.flush()
    await db.refresh(party)

    return party


@router.get(
    "/{agreement_id}/parties",
    response_model=list[PartyResponse],
)
async def list_parties(
    agreement_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List all parties for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    result = await db.execute(
        select(AgreementParty)
        .where(AgreementParty.agreement_id == agreement_id)
        .order_by(AgreementParty.created_at)
    )
    return result.scalars().all()


@router.delete(
    "/{agreement_id}/parties/{party_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_party(
    agreement_id: UUID,
    party_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Remove a party from an agreement (only if agreement is in draft)."""
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    if agreement.status not in ("draft",):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove parties after agreement has been sent",
        )

    result = await db.execute(
        select(AgreementParty).where(
            AgreementParty.id == party_id,
            AgreementParty.agreement_id == agreement_id,
        )
    )
    party = result.scalar_one_or_none()

    if party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Party not found",
        )

    await db.delete(party)
    await db.flush()


# --- Participant Endpoints ---


@router.post(
    "/{agreement_id}/participants",
    response_model=ParticipantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_participant(
    agreement_id: UUID,
    data: ParticipantCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Add a user as a participant on the agreement.

    This explicitly grants access — being a member of the org is not enough.
    """
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    # Verify party exists
    party_result = await db.execute(
        select(AgreementParty).where(
            AgreementParty.id == data.agreement_party_id,
            AgreementParty.agreement_id == agreement_id,
        )
    )
    if party_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement party not found",
        )

    # Check for duplicate
    existing = await db.execute(
        select(AgreementParticipant).where(
            AgreementParticipant.agreement_id == agreement_id,
            AgreementParticipant.user_id == data.user_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a participant on this agreement",
        )

    participant = AgreementParticipant(
        agreement_id=agreement_id,
        agreement_party_id=data.agreement_party_id,
        user_id=data.user_id,
        participant_role=data.participant_role,
        can_view=data.can_view,
        can_comment=data.can_comment,
        can_propose_changes=data.can_propose_changes,
        can_approve=data.can_approve,
        can_sign=data.can_sign,
    )
    db.add(participant)
    await db.flush()
    await db.refresh(participant)

    return participant


@router.get(
    "/{agreement_id}/participants",
    response_model=list[ParticipantResponse],
)
async def list_participants(
    agreement_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List all participants for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    result = await db.execute(
        select(AgreementParticipant)
        .where(AgreementParticipant.agreement_id == agreement_id)
        .order_by(AgreementParticipant.created_at)
    )
    return result.scalars().all()


@router.patch(
    "/{agreement_id}/participants/{participant_id}",
    response_model=ParticipantResponse,
)
async def update_participant(
    agreement_id: UUID,
    participant_id: UUID,
    data: ParticipantCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Update a participant's role and permissions."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    result = await db.execute(
        select(AgreementParticipant).where(
            AgreementParticipant.id == participant_id,
            AgreementParticipant.agreement_id == agreement_id,
        )
    )
    participant = result.scalar_one_or_none()

    if participant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Participant not found",
        )

    participant.participant_role = data.participant_role
    participant.can_view = data.can_view
    participant.can_comment = data.can_comment
    participant.can_propose_changes = data.can_propose_changes
    participant.can_approve = data.can_approve
    participant.can_sign = data.can_sign

    await db.flush()
    await db.refresh(participant)

    return participant


@router.delete(
    "/{agreement_id}/participants/{participant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_participant(
    agreement_id: UUID,
    participant_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Remove a participant from an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    result = await db.execute(
        select(AgreementParticipant).where(
            AgreementParticipant.id == participant_id,
            AgreementParticipant.agreement_id == agreement_id,
        )
    )
    participant = result.scalar_one_or_none()

    if participant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Participant not found",
        )

    await db.delete(participant)
    await db.flush()


# --- Access Grant Endpoints ---


@router.post(
    "/{agreement_id}/access-grants",
    response_model=AccessGrantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_access_grant(
    agreement_id: UUID,
    data: AccessGrantCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Grant explicit access to a user for a specific permission.

    Supports temporary access with expiration.
    """
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    grant = AgreementAccessGrant(
        agreement_id=agreement_id,
        user_id=data.user_id,
        permission_key=data.permission_key,
        granted_by=current_user.id,
        expires_at=data.expires_at,
    )
    db.add(grant)
    await db.flush()
    await db.refresh(grant)

    return grant


@router.get(
    "/{agreement_id}/access-grants",
    response_model=list[AccessGrantResponse],
)
async def list_access_grants(
    agreement_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List all access grants for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    result = await db.execute(
        select(AgreementAccessGrant)
        .where(AgreementAccessGrant.agreement_id == agreement_id)
        .order_by(AgreementAccessGrant.created_at)
    )
    return result.scalars().all()


@router.delete(
    "/{agreement_id}/access-grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_access_grant(
    agreement_id: UUID,
    grant_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Revoke an access grant."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    result = await db.execute(
        select(AgreementAccessGrant).where(
            AgreementAccessGrant.id == grant_id,
            AgreementAccessGrant.agreement_id == agreement_id,
        )
    )
    grant = result.scalar_one_or_none()

    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Access grant not found",
        )

    grant.status = "revoked"
    await db.flush()
