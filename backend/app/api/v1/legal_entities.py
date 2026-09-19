from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.rbac import require_permission
from app.dependencies.tenant import get_current_organization_id
from app.models.legal_entity import LegalEntity
from app.models.user import User
from app.schemas.legal_entity import (
    LegalEntityCreate,
    LegalEntityResponse,
)

router = APIRouter(
    prefix="/legal-entities",
    tags=["legal-entities"],
)

_perm_legal_entity_manage = Depends(require_permission("legal_entity.manage"))


@router.post(
    "",
    response_model=LegalEntityResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_perm_legal_entity_manage],
)
async def create_legal_entity(
    data: LegalEntityCreate,
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    entity = LegalEntity(
        organization_id=org_id,
        legal_name=data.legal_name,
        registration_number=data.registration_number,
        entity_type=data.entity_type,
        country=data.country,
        registered_address=data.registered_address,
        tax_identifier=data.tax_identifier,
    )
    db.add(entity)
    await db.flush()
    await db.refresh(entity)

    return entity


@router.get("", response_model=list[LegalEntityResponse])
async def list_legal_entities(
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LegalEntity)
        .where(
            LegalEntity.organization_id == org_id,
            LegalEntity.status == "active",
        )
    )
    return result.scalars().all()


@router.get(
    "/{entity_id}",
    response_model=LegalEntityResponse,
)
async def get_legal_entity(
    entity_id: UUID,
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LegalEntity)
        .where(
            LegalEntity.id == entity_id,
            LegalEntity.organization_id == org_id,
        )
    )
    entity = result.scalar_one_or_none()

    if entity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Legal entity not found",
        )

    return entity
