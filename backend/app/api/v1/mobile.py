"""Mobile backend endpoints (spec M2.02 / sections 25-26, 55-56).

Device registration / token rotation (spec 26):
  app start/resume
    → retrieve current provider token
    → POST /api/v1/mobile/devices (authenticated)
    → upsert device record (no duplicates)

GET /api/v1/mobile/config is unauthenticated so the Flutter app can
enforce a minimum client version before login (spec 55-56).
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.services.user_device_service import (
    register_device,
    revoke_device,
    list_devices,
)

router = APIRouter(prefix="/mobile", tags=["Mobile"])

# ---------------------------------------------------------------------------
# Deployment configuration — overridable via env vars (spec 55)
# ---------------------------------------------------------------------------

import os

_MIN_VERSION = os.getenv("MOBILE_MINIMUM_VERSION", "1.0.0")
_RECOMMENDED_VERSION = os.getenv("MOBILE_RECOMMENDED_VERSION", "1.2.0")
_MAINTENANCE = os.getenv("MOBILE_MAINTENANCE", "false").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class RegisterDeviceRequest(BaseModel):
    device_id: str
    platform: str
    push_token: str | None = None
    app_version: str | None = None

    @field_validator("platform")
    @classmethod
    def valid_platform(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in ("ios", "android", "web"):
            raise ValueError("platform must be 'ios', 'android', or 'web'")
        return v


class DeviceResponse(BaseModel):
    id: uuid.UUID
    device_id: str
    platform: str
    push_token: str | None
    app_version: str | None
    last_seen_at: datetime
    revoked_at: datetime | None
    revoked_reason: str | None

    model_config = {"from_attributes": True}


class MobileConfigResponse(BaseModel):
    minimum_supported_version: str
    recommended_version: str
    maintenance: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/devices", response_model=DeviceResponse, status_code=201)
async def upsert_device(
    body: RegisterDeviceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    device = await register_device(
        db,
        user_id=current_user.id,
        device_id=body.device_id,
        platform=body.platform,
        push_token=body.push_token,
        app_version=body.app_version,
    )
    return DeviceResponse.model_validate(device)


@router.get("/devices", response_model=list[DeviceResponse])
async def list_user_devices(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    devices = await list_devices(db, user_id=current_user.id)
    return [DeviceResponse.model_validate(d) for d in devices]


@router.delete("/devices/{device_id}", status_code=204)
async def remove_device(
    device_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await revoke_device(db, user_id=current_user.id, device_id=device_id)


@router.get("/config", response_model=MobileConfigResponse)
async def mobile_config():
    """Public — no auth required (spec 55-56: checked before login)."""
    return MobileConfigResponse(
        minimum_supported_version=_MIN_VERSION,
        recommended_version=_RECOMMENDED_VERSION,
        maintenance=_MAINTENANCE,
    )