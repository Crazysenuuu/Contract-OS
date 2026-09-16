"""Legal workspace API endpoints.

Manage private notes and comments that are only visible to the owning party.
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
from app.models.agreement_access import AgreementParty, AgreementParticipant
from app.models.user import User
from app.services.legal_workspace_service import (
    create_private_comment,
    create_private_note,
    delete_private_comment,
    delete_private_note,
    get_private_comments,
    get_private_notes,
)

router = APIRouter(
    prefix="/agreements",
    tags=["legal-workspace"],
)


# --- Schemas ---


class PrivateNoteCreate(BaseModel):
    agreement_party_id: UUID
    content: str
    note_type: str = "legal"


class PrivateNoteResponse(BaseModel):
    id: UUID
    agreement_id: UUID
    agreement_party_id: UUID
    author_id: UUID
    note_type: str
    content: str
    created_at: str

    model_config = {"from_attributes": True}


class PrivateCommentCreate(BaseModel):
    agreement_party_id: UUID
    content: str
    clause_identifier: str | None = None


class PrivateCommentResponse(BaseModel):
    id: UUID
    agreement_id: UUID
    agreement_party_id: UUID
    author_id: UUID
    clause_identifier: str | None
    content: str
    created_at: str

    model_config = {"from_attributes": True}


# --- Helper ---


async def verify_party_access(
    agreement_id: UUID,
    agreement_party_id: UUID,
    current_user: User,
    org_id: UUID,
    db: AsyncSession,
) -> AgreementParty:
    """Verify user has access to the specified party's workspace."""
    # First verify agreement access
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    # Verify the party exists and user is a participant for this party
    party_result = await db.execute(
        select(AgreementParty).where(
            AgreementParty.id == agreement_party_id,
            AgreementParty.agreement_id == agreement_id,
        )
    )
    party = party_result.scalar_one_or_none()

    if party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement party not found",
        )

    # Check if user is a participant for this party
    participant_result = await db.execute(
        select(AgreementParticipant).where(
            AgreementParticipant.agreement_id == agreement_id,
            AgreementParticipant.agreement_party_id == agreement_party_id,
            AgreementParticipant.user_id == current_user.id,
            AgreementParticipant.status == "active",
        )
    )
    participant = participant_result.scalar_one_or_none()

    if participant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a participant for this party",
        )

    return party


# --- Private Notes ---


@router.post(
    "/{agreement_id}/workspace/notes",
    response_model=PrivateNoteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_note(
    agreement_id: UUID,
    data: PrivateNoteCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a private note visible only to your party."""
    await verify_party_access(
        agreement_id=agreement_id,
        agreement_party_id=data.agreement_party_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    note = await create_private_note(
        db=db,
        agreement_id=agreement_id,
        agreement_party_id=data.agreement_party_id,
        author_id=current_user.id,
        content=data.content,
        note_type=data.note_type,
    )

    return note


@router.get(
    "/{agreement_id}/workspace/notes",
    response_model=list[PrivateNoteResponse],
)
async def list_notes(
    agreement_id: UUID,
    agreement_party_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List private notes for your party.

    CRITICAL: Only returns notes belonging to the authenticated user's party.
    """
    await verify_party_access(
        agreement_id=agreement_id,
        agreement_party_id=agreement_party_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    return await get_private_notes(db, agreement_id, agreement_party_id)


@router.delete(
    "/{agreement_id}/workspace/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_note(
    agreement_id: UUID,
    note_id: UUID,
    agreement_party_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Delete a private note (only if it belongs to your party)."""
    await verify_party_access(
        agreement_id=agreement_id,
        agreement_party_id=agreement_party_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    deleted = await delete_private_note(db, note_id, agreement_party_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found or access denied",
        )


# --- Private Comments ---


@router.post(
    "/{agreement_id}/workspace/comments",
    response_model=PrivateCommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_comment(
    agreement_id: UUID,
    data: PrivateCommentCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a clause-specific internal comment visible only to your party."""
    await verify_party_access(
        agreement_id=agreement_id,
        agreement_party_id=data.agreement_party_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    comment = await create_private_comment(
        db=db,
        agreement_id=agreement_id,
        agreement_party_id=data.agreement_party_id,
        author_id=current_user.id,
        content=data.content,
        clause_identifier=data.clause_identifier,
    )

    return comment


@router.get(
    "/{agreement_id}/workspace/comments",
    response_model=list[PrivateCommentResponse],
)
async def list_comments(
    agreement_id: UUID,
    agreement_party_id: UUID,
    clause_identifier: str | None = None,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List private comments for your party, optionally filtered by clause.

    CRITICAL: Only returns comments belonging to the authenticated user's party.
    """
    await verify_party_access(
        agreement_id=agreement_id,
        agreement_party_id=agreement_party_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    return await get_private_comments(
        db, agreement_id, agreement_party_id, clause_identifier
    )


@router.delete(
    "/{agreement_id}/workspace/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_comment(
    agreement_id: UUID,
    comment_id: UUID,
    agreement_party_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Delete a private comment (only if it belongs to your party)."""
    await verify_party_access(
        agreement_id=agreement_id,
        agreement_party_id=agreement_party_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    deleted = await delete_private_comment(db, comment_id, agreement_party_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found or access denied",
        )
