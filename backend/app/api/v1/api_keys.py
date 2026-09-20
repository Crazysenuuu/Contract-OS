"""API key management (spec §7).

POST   /api-keys         create a new API key (returns raw key once)
GET    /api-keys         list current user's API keys
DELETE /api-keys/{id}    revoke an API key
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.api_key import APIKey
from app.models.user import User
from app.dependencies.api_key_auth import require_api_key

router = APIRouter(prefix="/api-keys", tags=["API Keys"])


# --- Schemas ----------------------------------------------------------------

class APIKeyCreate(BaseModel):
    name: str
    scopes: str | None = None  # comma-separated permission keys
    expires_days: int | None = None


class APIKeyResponse(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    scopes: str | None
    is_active: bool
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class APIKeyCreatedResponse(APIKeyResponse):
    raw_key: str  # shown only once


# --- Endpoints --------------------------------------------------------------

@router.post("", response_model=APIKeyCreatedResponse, status_code=201)
async def create_api_key(
    data: APIKeyCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Create a new API key. The raw key is returned only in this response."""
    raw_key = f"cb_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:12]

    expires_at = None
    if data.expires_days:
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(days=data.expires_days)

    api_key = APIKey(
        organization_id=uuid.UUID(str(org_id)),
        user_id=user.id,
        name=data.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        scopes=data.scopes,
        is_active=True,
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)

    return APIKeyCreatedResponse(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        scopes=api_key.scopes,
        is_active=api_key.is_active,
        last_used_at=api_key.last_used_at,
        expires_at=api_key.expires_at,
        created_at=api_key.created_at,
        raw_key=raw_key,
    )


@router.get("", response_model=list[APIKeyResponse])
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """List the current user's API keys for this organisation (JWT auth)."""
    """List the current user's API keys for this organisation."""
    keys = (
        await db.scalars(
            select(APIKey)
            .where(
                APIKey.organization_id == uuid.UUID(str(org_id)),
                APIKey.user_id == user.id,
            )
            .order_by(APIKey.created_at.desc())
        )
    ).all()

    return [
        APIKeyResponse(
            id=k.id,
            name=k.name,
            key_prefix=k.key_prefix,
            scopes=k.scopes,
            is_active=k.is_active,
            last_used_at=k.last_used_at,
            expires_at=k.expires_at,
            created_at=k.created_at,
        )
        for k in keys
    ]


@router.get("/with-key", response_model=list[APIKeyResponse])
async def list_api_keys_with_key(
    db: AsyncSession = Depends(get_db),
    auth: tuple = Depends(require_api_key),
):
    """List API keys using API-key auth (demonstrates X-API-Key flow)."""
    key, user_id, org_uuid = auth
    keys = (
        await db.scalars(
            select(APIKey)
            .where(
                APIKey.organization_id == org_uuid,
                APIKey.user_id == user_id,
            )
            .order_by(APIKey.created_at.desc())
        )
    ).all()
    return [
        APIKeyResponse(
            id=k.id,
            name=k.name,
            key_prefix=k.key_prefix,
            scopes=k.scopes,
            is_active=k.is_active,
            last_used_at=k.last_used_at,
            expires_at=k.expires_at,
            created_at=k.created_at,
        )
        for k in keys
    ]


@router.delete("/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Revoke (deactivate) an API key."""
    key = await db.get(APIKey, key_id)
    if key is None or key.organization_id != uuid.UUID(str(org_id)):
        raise HTTPException(status_code=404, detail="API key not found")
    if key.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your API key")

    key.is_active = False
    await db.flush()
