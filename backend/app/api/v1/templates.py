"""Template library API.

Tenant boundary: every handler resolves the caller's organisation through
``get_current_organization_id`` (which also sets the RLS tenant context), and
mutations require the ``template.manage`` permission (spec §51).
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.rbac import require_permission
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.schemas.template import (
    TemplateCreate,
    TemplateOut,
    TemplateUpdate,
    TemplateVersionCreate,
    TemplateVersionOut,
)
from app.services import template_service_v2 as template_service

router = APIRouter(prefix="/templates", tags=["templates"])

_manage = Depends(require_permission("template.manage"))


@router.get("", response_model=list[TemplateOut])
async def list_templates(
    status: str | None = None,
    agreement_type_id: uuid.UUID | None = None,
    include_system: bool = True,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_organization_id),
) -> Any:
    return await template_service.get_templates(
        db=db,
        org_id=org_id,
        include_system=include_system,
        status=status,
        agreement_type_id=agreement_type_id,
    )


@router.post(
    "",
    response_model=TemplateOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_manage],
)
async def create_template(
    template_in: TemplateCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
) -> Any:
    # Only platform administrators may publish system (cross-tenant) templates.
    if template_in.is_system and not current_user.is_admin:
        template_in.is_system = False

    return await template_service.create_template(
        db=db,
        template_in=template_in,
        org_id=org_id,
        user_id=current_user.id,
    )


@router.get("/{template_id}", response_model=TemplateOut)
async def get_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_organization_id),
) -> Any:
    try:
        return await template_service.get_template(db, template_id, org_id)
    except template_service.TemplateNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{template_id}", response_model=TemplateOut, dependencies=[_manage])
async def update_template(
    template_id: uuid.UUID,
    template_in: TemplateUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_organization_id),
) -> Any:
    try:
        return await template_service.update_template(
            db=db,
            template_id=template_id,
            template_in=template_in,
            org_id=org_id,
        )
    except template_service.TemplateNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_manage],
)
async def delete_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_organization_id),
) -> None:
    try:
        await template_service.delete_template(db, template_id, org_id)
    except template_service.TemplateNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{template_id}/versions", response_model=list[TemplateVersionOut])
async def list_template_versions(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_organization_id),
) -> Any:
    try:
        return await template_service.get_template_versions(db, template_id, org_id)
    except template_service.TemplateNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/{template_id}/versions",
    response_model=TemplateVersionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_manage],
)
async def create_template_version(
    template_id: uuid.UUID,
    version_in: TemplateVersionCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
) -> Any:
    try:
        return await template_service.create_template_version(
            db=db,
            template_id=template_id,
            version_in=version_in,
            org_id=org_id,
            user_id=current_user.id,
        )
    except template_service.TemplateNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
