"""
Notification Delivery Status API Endpoints.

Track and query notification delivery status.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.notification import Notification, NotificationPreference
from app.models.user import User

router = APIRouter(prefix="/notifications", tags=["Notification Status"])


class DeliveryStatusResponse(BaseModel):
    notification_id: uuid.UUID
    status: str
    message_id: Optional[str]
    error_message: Optional[str]
    sent_at: Optional[str]


class DeliveryStatsResponse(BaseModel):
    total_sent: int
    total_failed: int
    total_pending: int
    delivery_rate: float
    by_type: dict[str, dict[str, int]]


class DigestStatsResponse(BaseModel):
    pending_notifications: int
    digest_subscribers: int


@router.get("/delivery/{notification_id}", response_model=DeliveryStatusResponse)
async def get_delivery_status(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get delivery status of a specific notification."""
    result = await db.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    notification = result.scalar_one_or_none()

    if not notification:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Notification not found")

    return DeliveryStatusResponse(
        notification_id=notification.id,
        status=notification.status,
        message_id=notification.message_id,
        error_message=notification.error_message,
        sent_at=notification.sent_at.isoformat() if notification.sent_at else None,
    )


@router.get("/delivery-stats", response_model=DeliveryStatsResponse)
async def get_delivery_stats(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get notification delivery statistics."""
    # Total counts by status
    sent_result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.organization_id == org_id,
            Notification.status == "sent",
        )
    )
    total_sent = sent_result.scalar() or 0

    failed_result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.organization_id == org_id,
            Notification.status == "failed",
        )
    )
    total_failed = failed_result.scalar() or 0

    pending_result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.organization_id == org_id,
            Notification.status == "pending",
        )
    )
    total_pending = pending_result.scalar() or 0

    total = total_sent + total_failed + total_pending
    delivery_rate = round(total_sent / max(total, 1) * 100, 1)

    # By type
    type_result = await db.execute(
        select(
            Notification.notification_type,
            Notification.status,
            func.count(Notification.id),
        )
        .where(Notification.organization_id == org_id)
        .group_by(Notification.notification_type, Notification.status)
    )

    by_type: dict[str, dict[str, int]] = {}
    for row in type_result.all():
        ntype, status, count = row
        if ntype not in by_type:
            by_type[ntype] = {"sent": 0, "failed": 0, "pending": 0}
        by_type[ntype][status] = count

    return DeliveryStatsResponse(
        total_sent=total_sent,
        total_failed=total_failed,
        total_pending=total_pending,
        delivery_rate=delivery_rate,
        by_type=by_type,
    )


@router.post("/retry/{notification_id}", response_model=DeliveryStatusResponse)
async def retry_notification(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retry a failed notification."""
    from app.services.email_service import email_service

    result = await db.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    notification = result.scalar_one_or_none()

    if not notification:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Notification not found")

    if notification.status != "failed":
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Only failed notifications can be retried")

    # Retry sending
    email_result = email_service._send(
        to_email=notification.to_email,
        subject=notification.subject,
        html_content=f"<p>Retry of: {notification.subject}</p>",
    )

    notification.status = "sent" if email_result.success else "failed"
    notification.message_id = email_result.message_id
    notification.error_message = None if email_result.success else email_result.message

    await db.commit()
    await db.refresh(notification)

    return DeliveryStatusResponse(
        notification_id=notification.id,
        status=notification.status,
        message_id=notification.message_id,
        error_message=notification.error_message,
        sent_at=notification.sent_at.isoformat() if notification.sent_at else None,
    )


@router.post("/send-digest")
async def trigger_digest(
    frequency: Optional[str] = Query(None, regex="^(daily|weekly)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger digest email sending."""
    from app.services.email_digest import EmailDigestService

    digest_service = EmailDigestService(db)
    sent_count = await digest_service.send_pending_digests(frequency=frequency)
    await db.commit()

    return {"digests_sent": sent_count}
