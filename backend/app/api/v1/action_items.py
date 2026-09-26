"""Action Center API (spec §3.10.50, §3.17.49).

GET    /action-items          — the caller's queue
POST   /action-items/{id}/complete — idempotent completion
POST   /action-items/{id}/dismiss  — hide without completing
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.services import action_item_service

router = APIRouter(prefix="/action-items", tags=["Action Center"])


class CompleteRequest(BaseModel):
    pass


@router.get("")
async def list_items(
    include_completed: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    items = await action_item_service.list_action_items(
        db,
        org_id=uuid.UUID(str(org_id)),
        user_id=user.id,
        include_completed=include_completed,
    )
    return [action_item_service.serialize_item(i) for i in items]


@router.post("/{item_id}/complete")
async def complete_item(
    item_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    item = await action_item_service.complete_action_item(
        db, item_id=uuid.UUID(item_id), completed_by=user.id
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Action item not found")
    if item.organization_id != uuid.UUID(str(org_id)):
        raise HTTPException(status_code=404, detail="Action item not found")
    return action_item_service.serialize_item(item)


@router.post("/{item_id}/dismiss")
async def dismiss_item(
    item_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    item = await action_item_service.dismiss_action_item(
        db, item_id=uuid.UUID(item_id)
    )
    if item is None or item.organization_id != uuid.UUID(str(org_id)):
        raise HTTPException(status_code=404, detail="Action item not found")
    return action_item_service.serialize_item(item)
