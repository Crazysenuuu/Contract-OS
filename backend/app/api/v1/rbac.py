"""RBAC governance API (spec 24.8 / governance).

Org-scoped role, permission, and membership management. All operations are
bounded by the caller's organization via ``get_current_organization_id``.
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.rbac import (
    OrganizationMember,
    Permission,
    Role,
    RolePermission,
)
from app.models.user import User

router = APIRouter(prefix="/rbac", tags=["RBAC"])


class RoleCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    permission_keys: List[str] = []


class RoleNameUpdateRequest(BaseModel):
    name: str


class PermissionsUpdateRequest(BaseModel):
    permission_keys: List[str]


class MemberAddRequest(BaseModel):
    user_id: str
    role_id: str


class MemberRoleUpdateRequest(BaseModel):
    role_id: str


async def _get_role(db: AsyncSession, org_id: UUID, role_id: str) -> Role:
    try:
        rid = UUID(str(role_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Role not found")
    result = await db.execute(
        select(Role).where(Role.id == rid, Role.organization_id == org_id)
    )
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


async def _permission_map(db: AsyncSession) -> dict[str, UUID]:
    result = await db.execute(select(Permission))
    return {p.key: p.id for p in result.scalars().all()}


def _role_response(role: Role, permissions: List[dict]) -> dict:
    return {
        "id": str(role.id),
        "name": role.name,
        "description": getattr(role, "description", None),
        "organization_id": str(role.organization_id),
        "permissions": permissions,
    }


@router.get("/permissions")
async def list_permissions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List available permission keys."""
    result = await db.execute(select(Permission).order_by(Permission.key))
    return [
        {"key": p.key, "description": p.description}
        for p in result.scalars().all()
    ]


@router.get("/roles")
async def list_roles(
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List roles for the current organization with their permissions."""
    roles_result = await db.execute(
        select(Role).where(Role.organization_id == org_id)
    )
    roles = roles_result.scalars().all()

    rp_result = await db.execute(
        select(RolePermission, Permission.key, Permission.description)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(RolePermission.role_id.in_([r.id for r in roles]))
    )
    permissions_by_role: dict[UUID, list] = {}
    for rp, key, description in rp_result.all():
        permissions_by_role.setdefault(rp.role_id, []).append(
            {"key": key, "description": description}
        )

    return [
        _role_response(role, permissions_by_role.get(role.id, []))
        for role in roles
    ]


@router.get("/roles/{role_id}")
async def get_role(
    role_id: str,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a role with its permissions."""
    role = await _get_role(db, org_id, role_id)

    perms_result = await db.execute(
        select(Permission.key, Permission.description)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role.id)
    )
    perms = [
        {"key": key, "description": description}
        for key, description in perms_result.all()
    ]
    return _role_response(role, perms)


@router.post("/roles", status_code=201)
async def create_role(
    request: RoleCreateRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a role and attach permissions."""
    existing = await db.execute(
        select(Role.id).where(
            Role.organization_id == org_id,
            Role.name == request.name,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Role name already exists")

    role = Role(
        organization_id=org_id,
        name=request.name,
    )
    db.add(role)
    await db.flush()

    if request.permission_keys:
        perm_map = await _permission_map(db)
        for key in request.permission_keys:
            pid = perm_map.get(key)
            if pid is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown permission key: {key}",
                )
            db.add(RolePermission(role_id=role.id, permission_id=pid))

    await db.commit()
    await db.refresh(role)
    return _role_response(role, [{"key": k} for k in request.permission_keys])


@router.put("/roles/{role_id}/permissions")
async def update_role_permissions(
    role_id: str,
    request: PermissionsUpdateRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace the permission set on a role."""
    role = await _get_role(db, org_id, role_id)
    if role.name == "owner":
        raise HTTPException(status_code=403, detail="Owner role permissions are fixed")

    await db.execute(
        RolePermission.__table__.delete().where(
            RolePermission.role_id == role.id
        )
    )

    perm_map = await _permission_map(db)
    for key in request.permission_keys:
        pid = perm_map.get(key)
        if pid is None:
            raise HTTPException(status_code=400, detail=f"Unknown permission key: {key}")
        db.add(RolePermission(role_id=role.id, permission_id=pid))

    await db.commit()
    return {"updated": True, "role_id": str(role.id), "permissions": request.permission_keys}


@router.delete("/roles/{role_id}", status_code=200)
async def delete_role(
    role_id: str,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a role (unless system role or in use)."""
    role = await _get_role(db, org_id, role_id)
    if role.name in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="System roles cannot be deleted")

    members_assigned = await db.execute(
        select(OrganizationMember.id).where(OrganizationMember.role_id == role.id).limit(1)
    )
    if members_assigned.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Role is assigned to members")

    await db.delete(role)
    await db.commit()
    return {"deleted": True, "role_id": role_id}


@router.post("/members", status_code=201)
async def add_member(
    request: MemberAddRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a user to the organization with a role."""
    try:
        uid = UUID(str(request.user_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="User not found")
    role = await _get_role(db, org_id, request.role_id)

    existing = await db.execute(
        select(OrganizationMember.id).where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == uid,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="User is already a member")

    member = OrganizationMember(
        organization_id=org_id,
        user_id=uid,
        role_id=role.id,
        status="active",
    )
    db.add(member)
    await db.commit()

    return {
        "id": str(member.id),
        "user_id": str(member.user_id),
        "role_id": str(member.role_id),
        "status": member.status,
    }


@router.put("/members/{member_id}/role")
async def update_member_role(
    member_id: str,
    request: MemberRoleUpdateRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change a member's role."""
    try:
        mid = UUID(str(member_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Member not found")
    role = await _get_role(db, org_id, request.role_id)

    result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.id == mid,
            OrganizationMember.organization_id == org_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")

    member.role_id = role.id
    await db.commit()

    return {
        "id": str(member.id),
        "user_id": str(member.user_id),
        "role_id": str(member.role_id),
        "status": member.status,
    }


@router.delete("/members/{member_id}", status_code=200)
async def remove_member(
    member_id: str,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a member from the organization."""
    try:
        mid = UUID(str(member_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Member not found")

    result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.id == mid,
            OrganizationMember.organization_id == org_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if member.user_id == current_user.id:
        raise HTTPException(status_code=403, detail="Cannot remove yourself")

    await db.delete(member)
    await db.commit()
    return {"deleted": True, "member_id": member_id}