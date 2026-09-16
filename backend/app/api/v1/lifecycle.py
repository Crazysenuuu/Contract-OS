"""Lifecycle API endpoints.

Expose the data-driven status machine: the registered states, the actions
available for an agreement from its current status, and the endpoint to
apply a transition (authorized + rule-checked via the lifecycle service).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement
from app.models.user import User
from app.services.lifecycle_service import (
    TransitionNotAllowed,
    apply_transition,
    get_available_actions,
    get_state_keys,
)

router = APIRouter(prefix="/agreements", tags=["lifecycle"])


class TransitionRequest(BaseModel):
    action_key: str


@router.get("/states", status_code=status.HTTP_200_OK)
async def list_states(
    db: AsyncSession = Depends(get_db),
):
    """List all registered lifecycle states."""
    return await get_state_keys(db)


@router.get(
    "/{agreement_id}/transitions",
    status_code=status.HTTP_200_OK,
)
async def list_available_transitions(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List the state transitions available from an agreement's current status."""
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    return await get_available_actions(db, agreement, org_id)


@router.post(
    "/{agreement_id}/transitions",
    status_code=status.HTTP_200_OK,
)
async def apply_transition_endpoint(
    agreement_id: uuid.UUID,
    data: TransitionRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Apply a lifecycle action to the agreement (rule-checked)."""
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    # A permission on the transition rule (like agreement.sign) is checked
    # through the access dependency if it exists; otherwise we allow members.
    try:
        await apply_transition(
            db,
            agreement=agreement,
            action_key=data.action_key,
            actor_id=current_user.id,
            org_id=org_id,
            actor_type="user",
            ip_address=request.client.host if request.client else None,
        )
    except TransitionNotAllowed as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return {
        "agreement_id": str(agreement.id),
        "status": agreement.status,
        "action": data.action_key,
    }