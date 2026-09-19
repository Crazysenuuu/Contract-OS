"""Route-level RBAC enforcement (spec §51 / 2.51).

Usage::

    @router.post("", dependencies=[Depends(require_permission("legal_entity.manage"))])

or, when the handler also needs the caller/org::

    member: OrganizationMember = Depends(require_permission("template.manage"))

Resolution order for the caller's *active* membership in the current
organisation:

1. system roles ``owner`` / ``admin`` hold every permission;
2. otherwise the role must carry a ``RolePermission`` row for the key.

Missing membership → 403 (the tenant dependency already guarantees one).
Permission keys are catalogued in ``seed_data.PERMISSION_CATALOG``; an
unknown key is a programming error and is rejected at import time.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.rbac import OrganizationMember, Permission, Role, RolePermission
from app.models.user import User

# Roles that implicitly hold every permission in their organisation.
SYSTEM_ROLES: frozenset[str] = frozenset({"owner", "admin"})

# Canonical permission catalogue. Keep in sync with seed_data.PERMISSION_CATALOG.
PERMISSION_KEYS: frozenset[str] = frozenset(
    {
        "agreement.view",
        "agreement.send",
        "agreement.submit_approval",
        "agreement.approve",
        "agreement.sign",
        "agreement.request_signature",
        "agreement.terminate",
        "agreement.amend",
        "agreement.export",
        "agreement.manage_participants",
        "agreement.propose_change",
        "agreement.comment",
        "template.manage",
        "clause.create",
        "policy.manage",
        "retention.manage",
        "org.manage_roles",
        "org.manage_members",
        "org.manage_sso",
        "integration.manage",
        "webhook.manage",
        "legal_entity.manage",
    }
)


async def has_permission(
    db: AsyncSession,
    *,
    user_id: UUID,
    org_id: UUID,
    permission: str,
) -> bool:
    """True when the user's active role in ``org_id`` grants ``permission``."""
    result = await db.execute(
        select(Role.name, Role.id)
        .join(OrganizationMember, OrganizationMember.role_id == Role.id)
        .where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.organization_id == org_id,
            OrganizationMember.status == "active",
        )
        .limit(1)
    )
    row = result.first()
    if row is None:
        return False
    role_name, role_id = row
    if role_name in SYSTEM_ROLES:
        return True
    granted = await db.execute(
        select(RolePermission.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(RolePermission.role_id == role_id, Permission.key == permission)
        .limit(1)
    )
    return granted.first() is not None


def require_permission(permission: str):
    """Dependency factory: 403 unless the caller holds ``permission``."""
    if permission not in PERMISSION_KEYS:
        raise ValueError(f"Unknown permission key: {permission}")

    async def _dependency(
        current_user: User = Depends(get_current_user),
        org_id: UUID = Depends(get_current_organization_id),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if current_user.is_admin:  # platform administrators
            return current_user
        if not await has_permission(
            db, user_id=current_user.id, org_id=org_id, permission=permission
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "insufficient_permission",
                    "required_permission": permission,
                },
            )
        return current_user

    _dependency.__name__ = f"require_{permission.replace('.', '_')}"
    return _dependency
