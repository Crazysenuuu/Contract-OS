"""API key authentication (spec §7).

Clients may authenticate with either:
- ``Authorization: Bearer <jwt>`` (normal flow), or
- ``X-API-Key: cb_...`` (programmatic access)

The API key path resolves to the owning user + organisation and is
scoped by the key's ``scopes`` string (comma-separated permission keys;
``*`` = all).  Expired or revoked keys are rejected with 401.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import fastapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.api_key import APIKey
from app.models.rbac import OrganizationMember, Role


async def resolve_api_key(
    db: AsyncSession,
    raw_key: str,
) -> tuple[APIKey, uuid.UUID, uuid.UUID] | None:
    """Resolve a raw API key to (key, user_id, org_id), or None if invalid."""
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key = (
        await db.scalar(
            select(APIKey).where(
                APIKey.key_hash == key_hash,
                APIKey.is_active.is_(True),
            )
        )
    )
    if key is None:
        return None

    if key.expires_at is not None and key.expires_at < datetime.now(timezone.utc):
        return None

    # The owner's active membership defines the active organisation.
    membership = (
        await db.scalar(
            select(OrganizationMember).where(
                OrganizationMember.user_id == key.user_id,
                OrganizationMember.organization_id == key.organization_id,
                OrganizationMember.status == "active",
            )
        )
    )
    if membership is None:
        return None

    return key, key.user_id, key.organization_id


async def api_key_has_permission(
    db: AsyncSession,
    key: APIKey,
    permission: str,
) -> bool:
    """True if the key's scopes grant *permission*.

    Scope grammar: comma-separated permission keys, ``*`` grants all.
    An empty/null scopes field grants read-only platform access.
    """
    if key.scopes is None or key.scopes.strip() == "":
        return permission in ("agreement.view",)  # implicit read-only

    scopes = {s.strip() for s in key.scopes.split(",") if s.strip()}
    return "*" in scopes or permission in scopes


async def get_api_key_context(
    request: fastapi.Request,
    db: AsyncSession = fastapi.Depends(get_db),
) -> tuple[APIKey, uuid.UUID, uuid.UUID] | None:
    """FastAPI dependency: resolve X-API-Key if present, else None.

    Routes that support both JWT and API-key auth use this to accept
    either credential.  Routes requiring a full user session should keep
    using ``get_current_user``.
    """
    raw = request.headers.get("X-API-Key")
    if not raw:
        return None
    return await resolve_api_key(db, raw)


async def require_api_key(
    request: fastapi.Request,
    db: AsyncSession = fastapi.Depends(get_db),
) -> tuple[APIKey, uuid.UUID, uuid.UUID]:
    """Dependency that *requires* a valid API key (not JWT)."""
    raw = request.headers.get("X-API-Key")
    if not raw:
        raise fastapi.HTTPException(
            status_code=401,
            detail="X-API-Key header required",
        )
    resolved = await resolve_api_key(db, raw)
    if resolved is None:
        raise fastapi.HTTPException(
            status_code=401,
            detail="Invalid, expired, or revoked API key",
        )
    key, user_id, org_id = resolved

    # Touch last_used_at (best-effort; never blocks the request).
    key.last_used_at = datetime.now(timezone.utc)

    return key, user_id, org_id
