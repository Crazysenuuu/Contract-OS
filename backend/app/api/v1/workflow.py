import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement
from app.models.user import User
from app.services.notification_tracking import NotificationTracking
from app.services.workflow_engine import WorkflowEngine

router = APIRouter(prefix="/agreements", tags=["workflow"])

workflow_engine = WorkflowEngine()


class TransitionRequest(BaseModel):
    action_key: str
    metadata: dict | None = None


class WorkflowStateResponse(BaseModel):
    key: str
    name: str
    is_initial: bool
    is_terminal: bool


class AvailableActionResponse(BaseModel):
    action_key: str
    name: str
    description: str | None
    requires_confirmation: bool
    requires_permission: str | None


class TransitionResponse(BaseModel):
    previous_state: str
    current_state: str
    transition_id: uuid.UUID
    workflow_instance_id: uuid.UUID


@router.get(
    "/{agreement_id}/workflow/state",
    response_model=WorkflowStateResponse,
    status_code=status.HTTP_200_OK,
)
async def get_workflow_state(
    agreement_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get the current workflow state of an agreement."""
    try:
        state = await workflow_engine.get_current_state(
            db, agreement_id=agreement_id
        )
    except HTTPException:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No workflow instance found for this agreement",
        )

    return state


@router.get(
    "/{agreement_id}/workflow/actions",
    response_model=list[AvailableActionResponse],
    status_code=status.HTTP_200_OK,
)
async def get_available_actions(
    agreement_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get available actions from the current state."""
    actions = await workflow_engine.get_available_actions(
        db, agreement_id=agreement_id
    )
    return actions


@router.post(
    "/{agreement_id}/workflow/transition",
    response_model=TransitionResponse,
    status_code=status.HTTP_200_OK,
)
async def transition_workflow(
    agreement_id: uuid.UUID,
    data: TransitionRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute a state transition on an agreement."""
    result = await workflow_engine.transition(
        db,
        agreement_id=agreement_id,
        action_key=data.action_key,
        actor_id=current_user.id,
        actor_type="user",
        tenant_id=org_id,
        metadata=data.metadata,
    )

    # Send notification on workflow transition (best-effort)
    try:
        agr_result = await db.execute(
            select(Agreement).where(Agreement.id == agreement_id)
        )
        agreement = agr_result.scalar_one_or_none()

        if agreement:
            tracking = NotificationTracking(db)
            await tracking.send_workflow_transition_notification(
                to_email=current_user.email,
                agreement_title=agreement.title,
                previous_state=result.previous_state,
                current_state=result.current_state,
                actor_name=current_user.name,
                organization_id=org_id,
                agreement_id=agreement_id,
            )
            await db.flush()
    except Exception:
        # Notifications are best-effort
        pass

    # Fire webhook events (best-effort)
    try:
        from app.services.webhook_service import WebhookService
        webhook_service = WebhookService(db)
        await webhook_service.fire_event(
            event_type=f"agreement.{result.current_state}",
            payload={
                "agreement_id": str(agreement_id),
                "previous_state": result.previous_state,
                "current_state": result.current_state,
                "action_key": data.action_key,
                "actor_id": str(current_user.id),
            },
            organization_id=org_id,
        )
        await db.flush()
    except Exception:
        # Webhooks are best-effort
        pass

    return result
