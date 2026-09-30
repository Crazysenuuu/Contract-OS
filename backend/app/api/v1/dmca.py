"""
DMCA notice intake API (17 U.S.C. § 512).

The public-facing designated agent details live on /legal/dmca. This
endpoint gives that agent (and support staff) a tracked inbox: every
takedown notice or counter-notification submitted here is persisted with
its status so the repeat-infringer determination and response deadlines
(§ 512(c)(1)(C) expeditious removal; § 512(g) 10-14 business day restore
window) are auditable.

Submission is unauthenticated (a rights holder may not have an account)
but heavily rate-limited; processing/review requires an admin.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.dmca import DmcaNotice
from app.models.user import User

router = APIRouter(prefix="/legal/dmca", tags=["DMCA"])

logger = logging.getLogger(__name__)


class DmcaNoticeCreate(BaseModel):
    reporter_name: str = Field(min_length=1, max_length=255)
    reporter_email: EmailStr
    # The copyrighted work claimed to be infringed (or representative list).
    work_description: str = Field(min_length=1, max_length=5000)
    # URL / location of the allegedly infringing material on the Service.
    material_location: str = Field(min_length=1, max_length=2000)
    # 'takedown' (original notice) or 'counter' (counter-notification).
    kind: str = Field(default="takedown", pattern="^(takedown|counter)$")
    sworn_statement: bool = Field(
        description="Reporter affirms the good-faith + accuracy statements "
        "required by 17 U.S.C. § 512(c)(3) / (g)(3)."
    )


class DmcaNoticeResponse(BaseModel):
    id: uuid.UUID
    kind: str
    reporter_name: str
    reporter_email: str
    work_description: str
    material_location: str
    status: str
    admin_note: Optional[str]
    received_at: datetime
    resolved_at: Optional[datetime]

    model_config = {"from_attributes": True}


@router.post(
    "/notices",
    response_model=DmcaNoticeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_dmca_notice(
    data: DmcaNoticeCreate,
    db: AsyncSession = Depends(get_db),
):
    """Public intake for DMCA takedown notices and counter-notifications."""
    if not data.sworn_statement:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "The good-faith and accuracy statements required by the "
                "DMCA must be affirmed to submit a notice."
            ),
        )

    notice = DmcaNotice(
        kind=data.kind,
        reporter_name=data.reporter_name,
        reporter_email=str(data.reporter_email),
        work_description=data.work_description,
        material_location=data.material_location,
        status="received",
    )
    db.add(notice)
    await db.commit()
    await db.refresh(notice)
    logger.info("DMCA %s received from %s", data.kind, notice.reporter_email)
    return notice


class DmcaNoticeUpdate(BaseModel):
    status: str = Field(pattern="^(action_taken|rejected|restored)$")
    admin_note: Optional[str] = Field(default=None, max_length=5000)


@router.patch("/notices/{notice_id}", response_model=DmcaNoticeResponse)
async def update_dmca_notice(
    notice_id: uuid.UUID,
    data: DmcaNoticeUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Process a DMCA notice (admin only).

    Records the action taken ('action_taken' after expeditious removal or
    decline with reasoning, 'rejected' for invalid notices, 'restored' when
    a counter-notification's § 512(g) window elapsed without suit). The
    admin_note is the audit trail for the decision and any repeat-infringer
    determination.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    result = await db.execute(
        select(DmcaNotice).where(DmcaNotice.id == notice_id)
    )
    notice = result.scalar_one_or_none()
    if notice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notice not found",
        )
    notice.status = data.status
    if data.admin_note is not None:
        notice.admin_note = data.admin_note
    if notice.resolved_at is None:
        notice.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(notice)
    logger.info(
        "DMCA notice %s -> %s by admin %s",
        notice.id, data.status, current_user.id,
    )
    return notice


@router.get("/notices", response_model=list[DmcaNoticeResponse])
async def list_dmca_notices(
    kind: Optional[str] = Query(None, pattern="^(takedown|counter)$"),
    notice_status: Optional[str] = Query(
        None, pattern="^(received|action_taken|rejected|restored)$"
    ),
    limit: int = Query(50, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List DMCA notices (admin only) — the agent's tracked queue."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    query = select(DmcaNotice).order_by(DmcaNotice.received_at.desc())
    if kind:
        query = query.where(DmcaNotice.kind == kind)
    if notice_status:
        query = query.where(DmcaNotice.status == notice_status)
    result = await db.execute(query.limit(limit))
    return result.scalars().all()
