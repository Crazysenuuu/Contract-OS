"""Agreement-type registry for the dynamic authoring studio (spec 2.03.3-4).

GET /api/v1/agreement-types returns only published/active types, filterable
by jurisdiction, category, and language. Clients build the intake form from
the returned schema — never from a frontend switch on the type key.
"""

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.agreement_type import AgreementType
from app.models.user import User

router = APIRouter(prefix="/agreement-types", tags=["Agreement Types"])


class AgreementTypeSummary(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str | None = None
    category: str
    schema_version: int
    status: str


class AgreementTypeDetail(AgreementTypeSummary):
    template_key: str | None = None
    schema: dict | None = None


def _schema_version(atype: AgreementType) -> int:
    configured = atype.schema.get("schema_version") if atype.schema else None
    if isinstance(configured, int):
        return configured
    return atype.version or 1


@router.get("", response_model=list[AgreementTypeSummary])
async def list_agreement_types(
    jurisdiction: Optional[str] = None,
    category: Optional[str] = None,
    language: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(AgreementType).where(AgreementType.status == "active")
    if category:
        query = query.where(AgreementType.category == category)
    result = await db.execute(query.order_by(AgreementType.name))
    types = result.scalars().all()

    if jurisdiction:
        types = [
            t
            for t in types
            if (t.schema or {}).get("jurisdiction") in (None, jurisdiction)
        ]

    # Language filter is applied on the schema locale list when present.
    if language:
        types = [
            t
            for t in types
            if not (t.schema and "languages" in t.schema)
            or language in (t.schema.get("languages") or [])
        ]

    return [
        AgreementTypeSummary(
            id=atype.id,
            key=atype.key,
            name=atype.name,
            description=atype.description,
            category=atype.category,
            schema_version=_schema_version(atype),
            status=atype.status,
        )
        for atype in types
    ]


@router.get("/{type_id:uuid}", response_model=AgreementTypeDetail)
async def get_agreement_type(
    type_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AgreementType).where(
            AgreementType.id == type_id,
            AgreementType.status == "active",
        )
    )
    atype = result.scalar_one_or_none()
    if atype is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement type not found",
        )
    return AgreementTypeDetail(
        id=atype.id,
        key=atype.key,
        name=atype.name,
        description=atype.description,
        category=atype.category,
        schema_version=_schema_version(atype),
        status=atype.status,
        template_key=atype.template_key,
        schema=atype.schema,
    )