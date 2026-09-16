"""Legal knowledge API endpoints (spec 1.10).

Source ingestion, rule management with human review gates, and legal
validation of agreements against jurisdiction rules.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_admin, get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.services.legal_knowledge_service import (
    create_rule,
    get_rule,
    get_source,
    ingest_source,
    list_rules,
    list_sources,
    serialize_rule,
    serialize_source,
    set_rule_status,
    set_source_status,
    validate_agreement_legal_rules,
)

router = APIRouter(tags=["legal-knowledge"])


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

class SourceCreate(BaseModel):
    title: str
    source_type: str
    jurisdiction_code: str
    content_text: str
    url: str | None = None
    source_version: str | None = None
    extracted_text: str | None = None
    change_notes: str | None = None
    metadata_json: dict | None = None


class SourceStatusUpdate(BaseModel):
    notes: str | None = None


@router.post("/legal/sources", status_code=status.HTTP_201_CREATED)
async def create_source_endpoint(
    data: SourceCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Ingest a legal source document.

    Always created 'pending_review' — never auto-activated.
    """
    source = await ingest_source(
        db,
        tenant_id=org_id,
        title=data.title,
        source_type=data.source_type,
        jurisdiction_code=data.jurisdiction_code,
        content_text=data.content_text,
        url=data.url,
        source_version=data.source_version,
        extracted_text=data.extracted_text,
        created_by=current_user.id,
        change_notes=data.change_notes,
        metadata_json=data.metadata_json,
    )
    return serialize_source(source)


@router.get("/legal/sources")
async def list_sources_endpoint(
    jurisdiction_code: str | None = None,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    sources = await list_sources(
        db, tenant_id=org_id, jurisdiction_code=jurisdiction_code
    )
    return [serialize_source(s) for s in sources]


@router.get("/legal/sources/{source_id}")
async def get_source_endpoint(
    source_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    source = await get_source(db, source_id)
    if source is None or (
        source.tenant_id is not None and source.tenant_id != org_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Legal source not found",
        )
    return serialize_source(source)


@router.post("/legal/sources/{source_id}/approve")
async def approve_source_endpoint(
    source_id: uuid.UUID,
    data: SourceStatusUpdate,
    admin: User = Depends(get_current_admin),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Human review gate: activate a source (admin only)."""
    source = await get_source(db, source_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Legal source not found",
        )
    source = await set_source_status(
        db, source=source, status="active", reviewer=admin.id,
        metadata_json={"notes": data.notes},
    )
    return serialize_source(source)


@router.post("/legal/sources/{source_id}/reject")
async def reject_source_endpoint(
    source_id: uuid.UUID,
    data: SourceStatusUpdate,
    admin: User = Depends(get_current_admin),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    source = await get_source(db, source_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Legal source not found",
        )
    source = await set_source_status(
        db, source=source, status="rejected", reviewer=admin.id,
        metadata_json={"notes": data.notes},
    )
    return serialize_source(source)


@router.post("/legal/sources/{source_id}/check-updates")
async def check_source_updates_endpoint(
    source_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Check source freshness.

    Returns the stored freshness metadata; automated background checks can
    be wired to this endpoint's contract (spec 1.10.26–1.10.29).
    """
    source = await get_source(db, source_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Legal source not found",
        )
    return {
        "source_id": str(source.id),
        "last_checked_at": (
            source.last_checked_at.isoformat()
            if source.last_checked_at
            else None
        ),
        "content_hash": source.content_hash,
        "status": source.status,
        "message": "Automated freshness checks are available for scheduling",
    }


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

class RuleCreate(BaseModel):
    rule_key: str
    title: str
    jurisdiction_code: str
    proposition: str
    source_id: uuid.UUID
    source_version_id: uuid.UUID
    executable_condition: dict | None = None
    applies_to_agreement_types: list[str] | None = None
    severity: str = "warning"
    effective_from: datetime | None = None
    change_notes: str | None = None
    metadata_json: dict | None = None


@router.post("/legal/rules", status_code=status.HTTP_201_CREATED)
async def create_rule_endpoint(
    data: RuleCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a legal rule (requires source citation)."""
    try:
        rule = await create_rule(
            db,
            tenant_id=org_id,
            rule_key=data.rule_key,
            title=data.title,
            jurisdiction_code=data.jurisdiction_code,
            proposition=data.proposition,
            source_id=data.source_id,
            source_version_id=data.source_version_id,
            executable_condition=data.executable_condition,
            applies_to_agreement_types=data.applies_to_agreement_types,
            severity=data.severity,
            effective_from=data.effective_from,
            created_by=current_user.id,
            change_notes=data.change_notes,
            metadata_json=data.metadata_json,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return serialize_rule(rule)


@router.get("/legal/rules")
async def list_rules_endpoint(
    jurisdiction_code: str | None = None,
    agreement_type: str | None = None,
    status_filter: str | None = "active",
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    rules = await list_rules(
        db,
        tenant_id=org_id,
        jurisdiction_code=jurisdiction_code,
        status=status_filter,
        agreement_type_key=agreement_type,
    )
    return [serialize_rule(r) for r in rules]


@router.get("/legal/rules/{rule_id}")
async def get_rule_endpoint(
    rule_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    rule = await get_rule(db, rule_id)
    if rule is None or (rule.tenant_id is not None and rule.tenant_id != org_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Legal rule not found",
        )
    return serialize_rule(rule)


@router.post("/legal/rules/{rule_id}/approve")
async def approve_rule_endpoint(
    rule_id: uuid.UUID,
    data: SourceStatusUpdate,
    admin: User = Depends(get_current_admin),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Human review gate: activate a rule (admin only)."""
    rule = await get_rule(db, rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Legal rule not found",
        )
    rule = await set_rule_status(
        db, rule=rule, status="active", reviewer=admin.id,
        metadata_json={"notes": data.notes},
    )
    return serialize_rule(rule)


@router.post("/legal/rules/{rule_id}/retire")
async def retire_rule_endpoint(
    rule_id: uuid.UUID,
    data: SourceStatusUpdate,
    admin: User = Depends(get_current_admin),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    rule = await get_rule(db, rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Legal rule not found",
        )
    rule = await set_rule_status(
        db, rule=rule, status="retired", reviewer=admin.id,
        metadata_json={"notes": data.notes},
    )
    return serialize_rule(rule)


# --------------------------------------------------------------------------
# Agreement legal validation
# --------------------------------------------------------------------------

@router.get("/agreements/{agreement_id}/legal-validation")
async def legal_validation_endpoint(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Validate an agreement against active legal rules."""
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    return await validate_agreement_legal_rules(db, agreement)