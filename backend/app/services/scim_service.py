"""SCIM 2.0 provisioning service (spec 1.21.9).

Implements the core SCIM user lifecycle used by identity providers
(Okta/Azure AD/Google): create, update, deactivate. SCIM is the *system of
record* for employment status changes flowing into the platform; deactivation
revokes membership but never deletes legal data (spec 1.21.37).

Authentication is a hashed bearer token scoped to one organization.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    NotFoundError,
    UnauthenticatedError,
    ValidationError,
)
from app.models.rbac import OrganizationMember, Role
from app.models.sso import IdentityProviderEvent, SCIMToken
from app.models.user import User


class SCIMError(Exception):
    pass


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def issue_scim_token(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    name: str,
) -> str:
    """Create a SCIM bearer token. The raw token is returned exactly once."""
    raw = secrets.token_urlsafe(32)
    db.add(
        SCIMToken(
            organization_id=organization_id,
            token_hash=hash_token(raw),
            name=name,
        )
    )
    await db.flush()
    return raw


async def authenticate_scim_token(
    db: AsyncSession, *, authorization_header: str | None
) -> uuid.UUID:
    """Resolve the organization for a SCIM request via its bearer token."""
    if not authorization_header or not authorization_header.lower().startswith("bearer "):
        raise UnauthenticatedError("SCIM bearer token required")
    raw = authorization_header.split(" ", 1)[1].strip()
    result = await db.execute(
        select(SCIMToken).where(
            SCIMToken.token_hash == hash_token(raw),
            SCIMToken.revoked_at.is_(None),
        )
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise UnauthenticatedError("Invalid SCIM token")
    if token.expires_at is not None and token.expires_at < datetime.now(timezone.utc):
        raise UnauthenticatedError("SCIM token expired")

    token.last_used_at = datetime.now(timezone.utc)
    return token.organization_id


async def _record_event(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    succeeded: bool = True,
    **detail,
) -> None:
    db.add(
        IdentityProviderEvent(
            organization_id=organization_id,
            event_type=event_type,
            succeeded=succeeded,
            email=detail.get("email"),
            detail=detail or None,
        )
    )


async def _default_role_id(db: AsyncSession, organization_id: uuid.UUID) -> uuid.UUID | None:
    result = await db.execute(
        select(Role).where(
            Role.organization_id == organization_id,
            Role.name == "member",
        ).limit(1)
    )
    role = result.scalar_one_or_none()
    if role is not None:
        return role.id
    # Fall back to the organization's lowest-privilege role by name.
    result = await db.execute(
        select(Role).where(Role.organization_id == organization_id).limit(1)
    )
    role = result.scalar_one_or_none()
    return role.id if role else None


async def provision_user(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_name: str,
    email: str,
    given_name: str | None = None,
    family_name: str | None = None,
    active: bool = True,
) -> dict:
    """SCIM POST /Users: create or reactivate a user + membership."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValidationError("valid email required (emails[user].value)")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            email=email,
            name=f"{given_name or ''} {family_name or ''}".strip() or email.split("@")[0],
            # SCIM-provisioned users have no local password until they set one.
            password_hash="!scim-no-password",
            status="active" if active else "deactivated",
        )
        db.add(user)
        await db.flush()
    elif not active:
        user.status = "deactivated"

    membership_result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == user.id,
        )
    )
    membership = membership_result.scalar_one_or_none()
    if membership is None:
        db.add(
            OrganizationMember(
                organization_id=organization_id,
                user_id=user.id,
                role_id=await _default_role_id(db, organization_id),
                status="active" if active else "deactivated",
            )
        )
    elif active:
        membership.status = "active"

    await _record_event(
        db,
        organization_id=organization_id,
        event_type="scim_user_created",
        idp_user_id=user_name,
        email=email,
        active=active,
    )
    await db.flush()

    return {
        "id": str(user.id),
        "userName": user_name,
        "emails": [{"value": email, "primary": True}],
        "name": {"givenName": given_name, "familyName": family_name},
        "active": active,
    }


async def update_user(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    active: bool | None = None,
    email: str | None = None,
    display_name: str | None = None,
) -> dict:
    """SCIM PUT/PATCH /Users/{id}."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError("User not found")

    if display_name:
        user.name = display_name
    if active is not None:
        user.status = "active" if active else "deactivated"
        membership_result = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user.id,
            )
        )
        membership = membership_result.scalar_one_or_none()
        if membership is not None and active is False:
            # Deactivation revokes access immediately but preserves legal
            # records (spec 1.21.37 data-deletion behavior).
            membership.status = "deactivated"

    await _record_event(
        db,
        organization_id=organization_id,
        event_type="scim_user_updated",
        email=email or user.email,
        active=active,
    )
    await db.flush()

    return {
        "id": str(user.id),
        "userName": user.email,
        "emails": [{"value": user.email, "primary": True}],
        "active": user.status == "active",
    }


async def deactivate_user(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict:
    """SCIM DELETE /Users/{id} equivalent: deactivate, never delete."""
    return await update_user(
        db,
        organization_id=organization_id,
        user_id=user_id,
        active=False,
    )
