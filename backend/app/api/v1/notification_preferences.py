"""
Notification Preferences API Endpoints.

Manage user notification preferences.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.notification import NotificationPreference
from app.models.user import User

router = APIRouter(prefix="/notification-preferences", tags=["Notification Preferences"])


class PreferenceUpdate(BaseModel):
    email_review_invitation: Optional[bool] = None
    email_agreement_viewed: Optional[bool] = None
    email_change_requested: Optional[bool] = None
    email_agreement_accepted: Optional[bool] = None
    email_signature_completed: Optional[bool] = None
    email_approval_request: Optional[bool] = None
    email_workflow_transition: Optional[bool] = None
    email_compliance_violation: Optional[bool] = None
    email_obligation_reminder: Optional[bool] = None
    digest_enabled: Optional[bool] = None
    digest_frequency: Optional[str] = None


class PreferenceResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email_review_invitation: bool
    email_agreement_viewed: bool
    email_change_requested: bool
    email_agreement_accepted: bool
    email_signature_completed: bool
    email_approval_request: bool
    email_workflow_transition: bool
    email_compliance_violation: bool
    email_obligation_reminder: bool
    digest_enabled: bool
    digest_frequency: str

    model_config = {"from_attributes": True}


async def _get_or_create_preferences(
    db: AsyncSession,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
) -> NotificationPreference:
    """Get or create notification preferences for a user."""
    result = await db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.organization_id == org_id,
        )
    )
    prefs = result.scalar_one_or_none()

    if prefs is None:
        prefs = NotificationPreference(
            user_id=user_id,
            organization_id=org_id,
        )
        db.add(prefs)
        await db.flush()

    return prefs


@router.get("", response_model=PreferenceResponse)
async def get_preferences(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get current user's notification preferences."""
    prefs = await _get_or_create_preferences(db, current_user.id, org_id)
    await db.commit()
    return prefs


@router.patch("", response_model=PreferenceResponse)
async def update_preferences(
    data: PreferenceUpdate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Update current user's notification preferences."""
    prefs = await _get_or_create_preferences(db, current_user.id, org_id)

    update_data = data.model_dump(exclude_unset=True)

    # Validate digest_frequency
    if "digest_frequency" in update_data:
        if update_data["digest_frequency"] not in ("daily", "weekly"):
            raise HTTPException(
                status_code=400,
                detail="digest_frequency must be 'daily' or 'weekly'",
            )

    for field, value in update_data.items():
        setattr(prefs, field, value)

    await db.commit()
    await db.refresh(prefs)
    return prefs


@router.post("/reset", response_model=PreferenceResponse)
async def reset_preferences(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Reset notification preferences to defaults."""
    prefs = await _get_or_create_preferences(db, current_user.id, org_id)

    prefs.email_review_invitation = True
    prefs.email_agreement_viewed = True
    prefs.email_change_requested = True
    prefs.email_agreement_accepted = True
    prefs.email_signature_completed = True
    prefs.email_approval_request = True
    prefs.email_workflow_transition = True
    prefs.email_compliance_violation = True
    prefs.email_obligation_reminder = True
    prefs.digest_enabled = False
    prefs.digest_frequency = "daily"

    await db.commit()
    await db.refresh(prefs)
    return prefs
