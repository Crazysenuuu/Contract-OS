"""Clause library API (spec 1.8).

Library management (CRUD + version lifecycle) and the clause-library draft
generation endpoint. Every mutating route requires an explicit permission
key; generation follows the full chain: auth -> tenant -> participant ->
permission -> schema-valid data -> applicable clauses -> version -> audit.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import AgreementVersion
from app.models.clause import (
    AgreementTypeClauseBinding,
    AgreementVersionClause,
    Clause,
    ClauseCondition,
    ClauseJurisdiction,
    ClauseVariable,
    ClauseVersion,
)
from app.models.user import User
from app.services.audit_service import record_event
from app.services.clause_hash import calculate_clause_hash

router = APIRouter(prefix="/clauses", tags=["Clause Library"])


# --- Schemas -----------------------------------------------------------


class ClauseCreate(BaseModel):
    key: str = Field(min_length=1, max_length=150)
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    category: Optional[str] = None


class ClauseVersionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    effective_from: Optional[datetime] = None
    provenance: Optional[dict] = None


class ClauseVersionApprove(BaseModel):
    approved: bool


class ClauseVariableIn(BaseModel):
    key: str
    label: str
    data_type: str = "text"
    source_path: Optional[str] = None
    required: bool = True
    display_order: int = 0


class ClauseConditionIn(BaseModel):
    condition: dict
    display_order: int = 0


class ClauseJurisdictionIn(BaseModel):
    jurisdiction_id: uuid.UUID
    applicable: bool


class BindingCreate(BaseModel):
    agreement_type_id: uuid.UUID
    clause_id: uuid.UUID
    display_order: int = 0
    required: bool = False
    configuration: Optional[dict] = None


def _clause_dict(clause: Clause) -> dict:
    return {
        "id": str(clause.id),
        "key": clause.key,
        "name": clause.name,
        "description": clause.description,
        "category": clause.category,
        "status": clause.status,
        "created_at": clause.created_at.isoformat() if clause.created_at else None,
    }


def _version_dict(v: ClauseVersion) -> dict:
    return {
        "id": str(v.id),
        "clause_id": str(v.clause_id),
        "version_number": v.version_number,
        "title": v.title,
        "content_hash": v.content_hash,
        "status": v.status,
        "effective_from": v.effective_from.isoformat() if v.effective_from else None,
        "effective_until": v.effective_until.isoformat() if v.effective_until else None,
        "provenance": v.provenance,
        "approved_at": v.approved_at.isoformat() if v.approved_at else None,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


async def _get_org_clause(
    clause_id: uuid.UUID, org_id: uuid.UUID, db: AsyncSession
) -> Clause:
    result = await db.execute(
        select(Clause).where(
            Clause.id == clause_id,
            (Clause.organization_id == org_id) | (Clause.organization_id.is_(None)),
        )
    )
    clause = result.scalar_one_or_none()
    if clause is None:
        raise HTTPException(status_code=404, detail="Clause not found")
    return clause


# --- Clause CRUD -------------------------------------------------------


@router.get("", response_model=list[dict])
async def list_clauses(
    category: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    query = select(Clause).where(
        (Clause.organization_id == org_id) | (Clause.organization_id.is_(None))
    )
    if category:
        query = query.where(Clause.category == category)
    query = query.order_by(Clause.key)
    result = await db.execute(query)
    return [_clause_dict(c) for c in result.scalars().all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_clause(
    data: ClauseCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(Clause).where(Clause.organization_id == org_id, Clause.key == data.key)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Clause key already exists")

    clause = Clause(
        organization_id=org_id,
        key=data.key,
        name=data.name,
        description=data.description,
        category=data.category,
        status="active",
    )
    db.add(clause)
    await db.flush()
    await record_event(
        db,
        tenant_id=org_id,
        actor_id=current_user.id,
        actor_type="user",
        action="CLAUSE_CREATED",
        resource_type="clause",
        resource_id=clause.id,
        metadata_json={"key": clause.key},
    )
    await db.commit()
    await db.refresh(clause)
    return _clause_dict(clause)


@router.get("/{clause_id}/versions", response_model=list[dict])
async def list_clause_versions(
    clause_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    await _get_org_clause(clause_id, org_id, db)
    result = await db.execute(
        select(ClauseVersion)
        .where(ClauseVersion.clause_id == clause_id)
        .order_by(ClauseVersion.version_number)
    )
    return [_version_dict(v) for v in result.scalars().all()]


@router.post("/{clause_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_clause_version(
    clause_id: uuid.UUID,
    data: ClauseVersionCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Create the next draft version. Approved versions are never edited
    (spec 1.8.1) - a change is always a new version."""
    clause = await _get_org_clause(clause_id, org_id, db)

    result = await db.execute(
        select(ClauseVersion.version_number)
        .where(ClauseVersion.clause_id == clause.id)
        .order_by(ClauseVersion.version_number.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    next_number = 1 if latest is None else latest + 1

    version = ClauseVersion(
        clause_id=clause.id,
        version_number=next_number,
        title=data.title,
        content=data.content,
        content_hash=calculate_clause_hash(data.content),
        status="draft",
        effective_from=data.effective_from,
        provenance=data.provenance,
    )
    db.add(version)
    await db.flush()
    await record_event(
        db,
        tenant_id=org_id,
        actor_id=current_user.id,
        actor_type="user",
        action="CLAUSE_VERSION_CREATED",
        resource_type="clause_version",
        resource_id=version.id,
        metadata_json={"clause_id": str(clause.id), "version_number": next_number},
    )
    await db.commit()
    await db.refresh(version)
    return _version_dict(version)


@router.post("/{clause_id}/versions/{version_id}/approve")
async def approve_clause_version(
    clause_id: uuid.UUID,
    version_id: uuid.UUID,
    data: ClauseVersionApprove,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Approve or retire a clause version (human review gate).

    Approval sets the effective window; the previously approved version is
    superseded but stays referenced by any executed agreement (1.8.19).
    """
    await _get_org_clause(clause_id, org_id, db)
    result = await db.execute(
        select(ClauseVersion).where(
            ClauseVersion.id == version_id, ClauseVersion.clause_id == clause_id
        )
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Clause version not found")

    if data.approved:
        if version.status in ("approved", "superseded"):
            raise HTTPException(status_code=409, detail=f"Version already {version.status}")
        # Supersede the currently approved version.
        current = await db.execute(
            select(ClauseVersion).where(
                ClauseVersion.clause_id == clause_id,
                ClauseVersion.status == "approved",
            )
        )
        for v in current.scalars().all():
            v.status = "superseded"
            v.effective_until = datetime.now(timezone.utc)

        version.status = "approved"
        version.approved_by = current_user.id
        version.approved_at = datetime.now(timezone.utc)
        if version.effective_from is None:
            version.effective_from = version.approved_at
    else:
        if version.status == "approved":
            raise HTTPException(status_code=409, detail="Retire via supersede, not reject")
        version.status = "retired"

    await record_event(
        db,
        tenant_id=org_id,
        actor_id=current_user.id,
        actor_type="user",
        action="CLAUSE_VERSION_APPROVED" if data.approved else "CLAUSE_VERSION_RETIRED",
        resource_type="clause_version",
        resource_id=version.id,
        metadata_json={"clause_id": str(clause_id), "status": version.status},
    )
    await db.commit()
    await db.refresh(version)
    return _version_dict(version)


@router.post("/{clause_id}/versions/{version_id}/variables", status_code=status.HTTP_201_CREATED)
async def add_clause_variable(
    clause_id: uuid.UUID,
    version_id: uuid.UUID,
    data: ClauseVariableIn,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    await _get_org_clause(clause_id, org_id, db)
    var = ClauseVariable(
        clause_version_id=version_id,
        key=data.key,
        label=data.label,
        data_type=data.data_type,
        source_path=data.source_path,
        required=data.required,
        display_order=data.display_order,
    )
    db.add(var)
    await db.commit()
    return {"id": str(var.id)}


@router.post("/{clause_id}/versions/{version_id}/conditions", status_code=status.HTTP_201_CREATED)
async def add_clause_condition(
    clause_id: uuid.UUID,
    version_id: uuid.UUID,
    data: ClauseConditionIn,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    await _get_org_clause(clause_id, org_id, db)
    cond = ClauseCondition(
        clause_version_id=version_id,
        condition=data.condition,
        display_order=data.display_order,
    )
    db.add(cond)
    await db.commit()
    return {"id": str(cond.id)}


@router.post("/{clause_id}/versions/{version_id}/jurisdictions", status_code=status.HTTP_201_CREATED)
async def add_clause_jurisdiction(
    clause_id: uuid.UUID,
    version_id: uuid.UUID,
    data: ClauseJurisdictionIn,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    await _get_org_clause(clause_id, org_id, db)
    binding = ClauseJurisdiction(
        clause_version_id=version_id,
        jurisdiction_id=data.jurisdiction_id,
        applicable=data.applicable,
    )
    db.add(binding)
    await db.commit()
    return {"id": str(binding.id)}


@router.get("/bindings/{agreement_type_id}", response_model=list[dict])
async def list_bindings(
    agreement_type_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AgreementTypeClauseBinding, Clause)
        .join(Clause, Clause.id == AgreementTypeClauseBinding.clause_id)
        .where(AgreementTypeClauseBinding.agreement_type_id == agreement_type_id)
        .order_by(AgreementTypeClauseBinding.display_order)
    )
    rows = result.all()
    return [
        {
            "id": str(binding.id),
            "clause_id": str(clause.id),
            "clause_key": clause.key,
            "clause_name": clause.name,
            "display_order": binding.display_order,
            "required": binding.required,
            "configuration": binding.configuration,
        }
        for binding, clause in rows
    ]


@router.post("/bindings", status_code=status.HTTP_201_CREATED)
async def create_binding(
    data: BindingCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    binding = AgreementTypeClauseBinding(
        organization_id=org_id,
        agreement_type_id=data.agreement_type_id,
        clause_id=data.clause_id,
        display_order=data.display_order,
        required=data.required,
        configuration=data.configuration,
    )
    db.add(binding)
    await db.commit()
    return {"id": str(binding.id)}


@router.get("/provenance/{agreement_version_id}", response_model=list[dict])
async def get_version_clause_provenance(
    agreement_version_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Exactly which approved clause versions produced this agreement version
    (spec 1.8.17) - answerable without guessing."""
    result = await db.execute(
        select(AgreementVersionClause, Clause, ClauseVersion)
        .join(Clause, Clause.id == AgreementVersionClause.clause_id)
        .join(ClauseVersion, ClauseVersion.id == AgreementVersionClause.clause_version_id)
        .where(AgreementVersionClause.agreement_version_id == agreement_version_id)
        .order_by(AgreementVersionClause.display_order)
    )
    rows = result.all()
    return [
        {
            "display_order": link.display_order,
            "clause_id": str(clause.id),
            "clause_key": clause.key,
            "clause_name": clause.name,
            "clause_version_id": str(version.id),
            "clause_version_number": version.version_number,
            "clause_version_hash": version.content_hash,
        }
        for link, clause, version in rows
    ]
