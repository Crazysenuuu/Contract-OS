"""Phase 4 API endpoints: Bulk operations, document intelligence, multi-tenant, performance."""
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import PlainTextResponse
from sqlalchemy import select, desc as _desc
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List, Dict
from datetime import datetime
from pydantic import BaseModel
import json

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.models.bulk_operations import BulkJob, BulkJobItem, ImportTemplate, ExportJob, SavedFilter, ImportStatus, ImportType, BulkActionType
from app.models.document_intelligence import ExtractedClause, ClauseLibrary, SmartTag, ClauseCategory
from app.models.tenant import Tenant, TenantBranding, TenantTheme, TenantInvitation
from app.services.bulk_operations import BulkOperationsService
from app.services.document_intelligence import DocumentIntelligenceService
from app.services.tenant_service import TenantService
from app.services.performance_service import PerformanceService
from uuid import UUID

router = APIRouter(prefix="/phase4", tags=["Phase 4"])


# ===== BULK OPERATIONS =====

class BulkActionRequest(BaseModel):
    filter_criteria: Dict = {}
    options: Dict = {}
    action: str


class ImportRequest(BaseModel):
    csv_content: str
    import_type: str = "agreements"
    template_id: Optional[str] = None


class SavedFilterRequest(BaseModel):
    name: str
    entity_type: str
    filters: Dict
    sort_by: Optional[str] = None
    sort_order: str = "desc"
    is_shared: bool = False


@router.post("/bulk/import")
async def bulk_import(
    request: ImportRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Import entities from CSV."""
    try:
        import_type = ImportType(request.import_type)
        service = BulkOperationsService(db)

        job = await service.import_from_csv(
            organization_id=str(org_id),
            created_by=str(current_user.id),
            import_type=import_type,
            csv_content=request.csv_content,
            template_id=request.template_id,
        )

        return {
            "job_id": job.id,
            "status": job.status.value,
            "total_items": job.total_items,
            "successful_items": job.successful_items,
            "failed_items": job.failed_items,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/bulk/action")
async def bulk_action(
    request: BulkActionRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Execute a bulk action on filtered entities."""
    action_map = {
        "status_change": BulkActionType.STATUS_CHANGE,
        "assign": BulkActionType.ASSIGN,
        "delete": BulkActionType.DELETE,
        "archive": BulkActionType.ARCHIVE,
        "compliance_check": BulkActionType.COMPLIANCE_CHECK,
    }

    action_type = action_map.get(request.action)
    if not action_type:
        raise HTTPException(status_code=400, detail=f"Unknown action: {request.action}")

    service = BulkOperationsService(db)
    job = await service.create_bulk_job(
        organization_id=str(org_id),
        created_by=str(current_user.id),
        job_type=action_type,
        filter_criteria=request.filter_criteria,
        options=request.options,
    )

    job = await service.execute_bulk_action(job)

    return {
        "job_id": job.id,
        "status": job.status.value,
        "total_items": job.total_items,
        "successful_items": job.successful_items,
        "failed_items": job.failed_items,
    }


@router.get("/bulk/jobs")
async def list_bulk_jobs(
    status: Optional[str] = None,
    limit: int = Query(20, le=100),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db)
):
    """List bulk jobs."""
    query = (
        select(BulkJob)
        .where(BulkJob.organization_id == str(org_id))
        .order_by(_desc(BulkJob.created_at))
        .limit(limit)
    )
    if status:
        query = query.where(BulkJob.status == status)

    result = await db.execute(query)
    jobs = result.scalars().all()
    return [{
        "id": j.id,
        "job_type": j.job_type.value,
        "status": j.status.value,
        "total_items": j.total_items,
        "successful_items": j.successful_items,
        "failed_items": j.failed_items,
        "created_at": j.created_at.isoformat() if j.created_at else None,
    } for j in jobs]


@router.get("/bulk/jobs/{job_id}")
async def get_bulk_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get bulk job details."""
    from app.models.bulk_operations import BulkJob as _BulkJob
    result = await db.execute(
        select(_BulkJob).where(_BulkJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "id": job.id,
        "job_type": job.job_type.value,
        "status": job.status.value,
        "total_items": job.total_items,
        "processed_items": job.processed_items,
        "successful_items": job.successful_items,
        "failed_items": job.failed_items,
        "errors": job.errors,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@router.post("/bulk/export")
async def bulk_export(
    export_type: str = "agreements",
    format: str = "csv",
    filters: Dict = {},
    columns: List[str] = ["title", "status", "created_at"],
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Export entities to CSV/XLSX."""
    service = BulkOperationsService(db)
    job = await service.export_agreements(
        organization_id=str(org_id),
        created_by=str(current_user.id),
        filters=filters,
        columns=columns,
        format=format,
    )

    return {
        "job_id": job.id,
        "status": job.status.value,
        "row_count": job.row_count,
        "format": format,
    }


@router.get("/bulk/export/{job_id}/download")
async def download_export(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Download exported data."""
    job_result = await db.execute(
        select(ExportJob).where(ExportJob.id == job_id)
    )
    job = job_result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found")

    # Build CSV inline with an async query (the bulk service is sync-only).
    import csv as _csv
    import io as _io

    from app.models.agreement import Agreement as _Agreement

    rows_result = await db.execute(
        select(_Agreement).where(
            _Agreement.organization_id == job.organization_id
        )
    )
    agreements = rows_result.scalars().all()

    output = _io.StringIO()
    fieldnames = job.columns or ["title", "status", "created_at"]
    writer = _csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for agreement in agreements:
        row = {}
        for col in fieldnames:
            if col == "title":
                row[col] = agreement.title
            elif col == "status":
                row[col] = agreement.status
            elif col == "created_at":
                row[col] = agreement.created_at.isoformat() if agreement.created_at else ""
            else:
                row[col] = ""
        writer.writerow(row)
    csv_content = output.getvalue()

    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={job.export_type}.{job.format}"}
    )


# ===== SAVED FILTERS =====

@router.post("/filters")
async def create_saved_filter(
    request: SavedFilterRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a saved filter."""
    service = BulkOperationsService(db)
    sf = await service.create_saved_filter(
        organization_id=str(org_id),
        created_by=str(current_user.id),
        name=request.name,
        entity_type=request.entity_type,
        filters=request.filters,
        sort_by=request.sort_by,
        sort_order=request.sort_order,
        is_shared=request.is_shared,
    )
    return {"id": sf.id, "name": sf.name}


@router.get("/filters")
async def list_saved_filters(
    entity_type: Optional[str] = None,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List saved filters."""
    service = BulkOperationsService(db)
    filters = await service.get_saved_filters(str(org_id), str(current_user.id))
    return [{
        "id": f.id,
        "name": f.name,
        "entity_type": f.entity_type,
        "filters": f.filters,
        "use_count": f.use_count,
    } for f in filters]


@router.delete("/filters/{filter_id}")
async def delete_saved_filter(
    filter_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a saved filter."""
    sf_result = await db.execute(
        select(SavedFilter).where(SavedFilter.id == filter_id)
    )
    sf = sf_result.scalar_one_or_none()
    if not sf:
        raise HTTPException(status_code=404, detail="Filter not found")
    await db.delete(sf)
    await db.commit()
    return {"status": "deleted"}


# ===== DOCUMENT INTELLIGENCE =====

class ExtractClausesRequest(BaseModel):
    agreement_id: str
    text: str
    version_id: Optional[str] = None


class AddToLibraryRequest(BaseModel):
    clause_id: str
    title: str
    description: Optional[str] = None
    jurisdictions: List[str] = []
    agreement_types: List[str] = []


@router.post("/clauses/extract")
async def extract_clauses(
    request: ExtractClausesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Extract and classify clauses from text."""
    service = DocumentIntelligenceService(db)
    clauses = await service.extract_clauses(
        agreement_id=request.agreement_id,
        text=request.text,
        version_id=request.version_id,
    )

    return {
        "clauses_found": len(clauses),
        "clauses": [{
            "id": c.id,
            "title": c.title,
            "category": c.category.value,
            "risk_level": c.risk_level.value if c.risk_level else None,
            "risk_score": c.risk_score,
            "sentiment": c.sentiment.value if c.sentiment else None,
            "tags": c.tags,
            "text_preview": c.text[:200] + "..." if len(c.text) > 200 else c.text,
        } for c in clauses],
    }


@router.get("/clauses")
async def list_clauses(
    agreement_id: str,
    category: Optional[str] = None,
    risk_level: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List extracted clauses for an agreement."""
    query = (
        select(ExtractedClause)
        .where(ExtractedClause.agreement_id == agreement_id)
        .order_by(_desc(ExtractedClause.risk_score))
    )

    if category:
        query = query.where(ExtractedClause.category == category)
    if risk_level:
        query = query.where(ExtractedClause.risk_level == risk_level)

    result = await db.execute(query)
    clauses = result.scalars().all()

    return [{
        "id": c.id,
        "title": c.title,
        "section_number": c.section_number,
        "category": c.category.value,
        "risk_level": c.risk_level.value if c.risk_level else None,
        "risk_score": c.risk_score,
        "sentiment": c.sentiment.value if c.sentiment else None,
        "tags": c.tags,
        "key_entities": c.key_entities,
        "text": c.text,
    } for c in clauses]


@router.post("/clauses/add-to-library")
async def add_clause_to_library(
    request: AddToLibraryRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Add an extracted clause to the clause library."""
    service = DocumentIntelligenceService(db)
    try:
        entry = await service.add_to_library(
            clause_id=request.clause_id,
            organization_id=str(org_id),
            title=request.title,
            description=request.description,
            jurisdictions=request.jurisdictions,
            agreement_types=request.agreement_types,
            created_by=str(current_user.id),
        )
        return {"id": entry.id, "title": entry.title}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/clauses/library")
async def list_clause_library(
    category: Optional[str] = None,
    jurisdiction: Optional[str] = None,
    limit: int = Query(20, le=100),
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List clauses in the library."""
    query = (
        select(ClauseLibrary)
        .where(ClauseLibrary.organization_id == str(org_id))
        .order_by(_desc(ClauseLibrary.usage_count))
        .limit(limit)
    )
    if category:
        query = query.where(ClauseLibrary.category == category)

    result = await db.execute(query)
    clauses = result.scalars().all()

    return [{
        "id": c.id,
        "title": c.title,
        "category": c.category.value,
        "risk_level": c.risk_level.value if c.risk_level else None,
        "risk_score": c.risk_score,
        "usage_count": c.usage_count,
        "tags": c.tags,
        "jurisdictions": c.jurisdictions,
        "agreement_types": c.agreement_types,
        "text_preview": c.text[:200] + "..." if len(c.text) > 200 else c.text,
    } for c in clauses]


@router.get("/clauses/{clause_id}/similar")
async def find_similar_clauses(
    clause_id: str,
    threshold: float = Query(0.3, ge=0, le=1),
    limit: int = Query(5, le=20),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Find similar clauses in the library."""
    service = DocumentIntelligenceService(db)
    similar = await service.find_similar_clauses(clause_id, threshold=threshold, limit=limit)

    return [{
        "library_clause_id": c.id,
        "title": c.title,
        "similarity": round(score, 3),
        "text_preview": c.text[:200],
    } for c, score in similar]


# ===== CLAUSE LIFECYCLE GOVERNANCE (spec 24.8) =====

class PublishClauseRequest(BaseModel):
    new_text: Optional[str] = None
    new_title: Optional[str] = None


class SetClauseStatusRequest(BaseModel):
    status: str
    reason: Optional[str] = None


class ResolveDraftRequest(BaseModel):
    resolution: str  # 'upgrade' | 'keep_legacy'


@router.post("/clauses/library/{clause_id}/publish")
async def publish_clause(
    clause_id: str,
    request: PublishClauseRequest = PublishClauseRequest(),
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Publish a new version of a library clause (Legal Admin only)."""
    from app.services.clause_governance import (
        ClauseGovernanceError,
        publish_clause_version,
    )
    try:
        entry = await publish_clause_version(
            db,
            clause_id=UUID(clause_id),
            actor_id=current_user.id,
            org_id=org_id,
            new_text=request.new_text,
            new_title=request.new_title,
        )
        await db.commit()
        return {
            "id": entry.id,
            "title": entry.title,
            "version": entry.version,
            "lifecycle_status": entry.lifecycle_status,
        }
    except ClauseGovernanceError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.patch("/clauses/library/{clause_id}/status")
async def update_clause_status(
    clause_id: str,
    request: SetClauseStatusRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Set clause lifecycle status: active / deprecated / archived."""
    from app.services.clause_governance import (
        ClauseGovernanceError,
        set_clause_status,
    )
    try:
        entry = await set_clause_status(
            db,
            clause_id=UUID(clause_id),
            status=request.status,
            actor_id=current_user.id,
            org_id=org_id,
            reason=request.reason,
        )
        await db.commit()
        return {
            "id": entry.id,
            "title": entry.title,
            "version": entry.version,
            "lifecycle_status": entry.lifecycle_status,
        }
    except ClauseGovernanceError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/clauses/library/{clause_id}/inflight-drafts")
async def inflight_drafts(
    clause_id: str,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List un-signed drafts that reference a clause version (in-flight)."""
    from app.services.clause_governance import find_inflight_drafts

    drafts = await find_inflight_drafts(
        db,
        org_id=org_id,
        clause_id=UUID(clause_id),
    )
    return {"count": len(drafts), "drafts": drafts}


@router.post("/clauses/resolve-draft")
async def resolve_draft(
    agreement_id: str,
    clause_id: str,
    request: ResolveDraftRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Explicitly upgrade a draft to the new clause version or keep legacy."""
    from app.services.clause_governance import (
        ClauseGovernanceError,
        resolve_inflight_draft,
    )
    try:
        result = await resolve_inflight_draft(
            db,
            agreement_id=UUID(agreement_id),
            clause_id=UUID(clause_id),
            resolution=request.resolution,
            actor_id=current_user.id,
        )
        await db.commit()
        return result
    except ClauseGovernanceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/clauses/stats")
async def clause_stats(
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get clause statistics."""
    service = DocumentIntelligenceService(db)
    return await service.get_clause_stats(str(org_id))


# ===== CONTRACT HEALTH (spec section 17) =====

@router.get("/contract-health/{agreement_id}")
async def get_contract_health(
    agreement_id: str,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Compute the Contract Health profile for an agreement.

    Overall Risk aggregates Payment, IP, Liability, Termination,
    Data Protection and Renewal risks derived from clause analysis.
    """
    try:
        from app.services.contract_health_service import compute_contract_health

        return await compute_contract_health(db, uuid.UUID(agreement_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/contract-health")
async def portfolio_contract_health(
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate Contract Health across the organization's agreements."""
    from sqlalchemy import select

    from app.models.agreement import Agreement
    from app.services.contract_health_service import compute_contract_health

    result = await db.execute(
        select(Agreement)
        .where(Agreement.organization_id == org_id)
        .order_by(Agreement.created_at.desc())
        .limit(500)
    )
    agreements = result.scalars().all()

    profiles = []
    for agreement in agreements:
        try:
            profiles.append(await compute_contract_health(db, agreement.id))
        except Exception:
            continue

    if not profiles:
        return {
            "organization_id": str(org_id),
            "agreements_analyzed": 0,
            "average_health": None,
            "overall_risk": "low",
            "risk_distribution": {"low": 0, "medium": 0, "high": 0},
        }

    avg_health = round(sum(p["contract_health"] for p in profiles) / len(profiles), 1)

    distribution = {"low": 0, "medium": 0, "high": 0}
    for p in profiles:
        level = p["overall_risk"]["level"]
        distribution[level] = distribution.get(level, 0) + 1

    heaviest = max(distribution.items(), key=lambda kv: kv[1])
    return {
        "organization_id": str(org_id),
        "agreements_analyzed": len(profiles),
        "average_health": avg_health,
        "overall_risk": heaviest[0],
        "risk_distribution": distribution,
    }


# ===== SMART TAGS =====

@router.get("/tags")
async def list_smart_tags(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List smart tags."""
    result = await db.execute(
        select(SmartTag).order_by(_desc(SmartTag.usage_count))
    )
    tags = result.scalars().all()
    return [{
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "color": t.color,
        "usage_count": t.usage_count,
        "is_system": t.is_system,
    } for t in tags]


@router.post("/tags")
async def create_smart_tag(
    name: str,
    description: str = "",
    color: str = "#0070f3",
    keywords: List[str] = [],
    categories: List[str] = [],
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a smart tag."""
    tag = SmartTag(
        organization_id=str(org_id),
        name=name,
        description=description,
        color=color,
        keywords=keywords,
        categories=categories,
    )
    db.add(tag)
    await db.commit()
    return {"id": tag.id, "name": tag.name}


# ===== TENANT & BRANDING =====

class BrandingUpdateRequest(BaseModel):
    company_name: Optional[str] = None
    tagline: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    background_color: Optional[str] = None
    text_color: Optional[str] = None
    font_family: Optional[str] = None
    custom_css: Optional[str] = None
    footer_text: Optional[str] = None
    privacy_policy_url: Optional[str] = None
    terms_of_service_url: Optional[str] = None


@router.get("/tenant")
async def get_tenant(
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get tenant configuration."""
    service = TenantService(db)
    tenant = await service.get_tenant(str(org_id))
    if not tenant:
        return {"exists": False}

    branding = await service.get_branding_payload(tenant.id)

    return {
        "exists": True,
        "id": tenant.id,
        "slug": tenant.slug,
        "plan": tenant.plan,
        "features": tenant.features,
        "branding": branding,
        "limits": {
            "max_users": tenant.max_users,
            "max_agreements": tenant.max_agreements,
            "storage_limit_mb": tenant.storage_limit_mb,
        },
        "usage": {
            "users": tenant.users_count,
            "storage_mb": tenant.storage_used_mb,
        },
    }


@router.post("/tenant")
async def create_tenant(
    slug: str,
    plan: str = "free",
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a tenant."""
    service = TenantService(db)
    try:
        tenant = await service.create_tenant(
            organization_id=str(org_id),
            slug=slug,
            plan=plan,
        )
        return {"id": tenant.id, "slug": tenant.slug, "plan": tenant.plan}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tenant/branding")
async def get_branding(
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get branding configuration."""
    service = TenantService(db)
    tenant = await service.get_tenant(str(org_id))
    if not tenant:
        return {}

    return await service.get_branding_payload(tenant.id)


@router.patch("/tenant/branding")
async def update_branding(
    request: BrandingUpdateRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update branding configuration."""
    service = TenantService(db)
    tenant = await service.get_tenant(str(org_id))
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    updates = {k: v for k, v in request.dict().items() if v is not None}
    branding = await service.update_branding(tenant.id, updates)

    return await service.get_branding_payload(tenant.id)


@router.get("/tenant/css")
async def get_branding_css(
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get CSS custom properties for branding."""
    service = TenantService(db)
    tenant = await service.get_tenant(str(org_id))
    if not tenant:
        return PlainTextResponse(content=":root {}")

    css = await service.generate_css_variables(tenant.id)
    return PlainTextResponse(content=css, media_type="text/css")


@router.get("/tenant/themes")
async def list_themes(
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List themes."""
    service = TenantService(db)
    tenant = await service.get_tenant(str(org_id))
    if not tenant:
        return []

    themes = await service.get_themes(tenant.id)
    return [{
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "colors": t.colors,
        "is_default": t.is_default,
        "supports_dark_mode": t.supports_dark_mode,
    } for t in themes]


@router.get("/tenant/limits")
async def check_tenant_limits(
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Check tenant resource limits."""
    service = TenantService(db)
    tenant = await service.get_tenant(str(org_id))
    if not tenant:
        return {"allowed": False, "reason": "No tenant"}

    return await service.check_limits(tenant.id)


@router.get("/tenant/audit")
async def tenant_audit_logs(
    limit: int = Query(50, le=200),
    action: Optional[str] = None,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get tenant audit logs."""
    service = TenantService(db)
    tenant = await service.get_tenant(str(org_id))
    if not tenant:
        return []

    logs = await service.get_audit_logs(tenant.id, limit=limit, action=action)
    return [{
        "id": l.id,
        "action": l.action,
        "resource_type": l.resource_type,
        "resource_id": l.resource_id,
        "details": l.details,
        "created_at": l.created_at.isoformat() if l.created_at else None,
    } for l in logs]


# ===== PERFORMANCE & CACHING =====

class PerformanceQuery(BaseModel):
    page: int = 1
    page_size: int = 20
    sort_by: str = "created_at"
    sort_order: str = "desc"
    search: Optional[str] = None
    status: Optional[str] = None


@router.get("/performance/agreements")
async def get_agreements_optimized(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, le=100),
    sort_by: str = "created_at",
    sort_order: str = "desc",
    search: Optional[str] = None,
    status: Optional[str] = None,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get agreements with optimized pagination and caching."""
    service = PerformanceService(db)
    return await service.get_agreements_optimized(
        organization_id=str(org_id),
        status=status,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        search=search,
    )


@router.get("/performance/stats")
async def get_performance_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get performance statistics including cache hit rates."""
    service = PerformanceService(db)
    return {
        "cache": service.cache_stats(),
        "jobs": service.get_queue_stats(),
    }


@router.get("/performance/cache/invalidate")
async def invalidate_cache(
    pattern: str = "",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Invalidate cached values."""
    service = PerformanceService(db)
    service.invalidate_pattern(pattern)
    return {"status": "invalidated", "pattern": pattern}


# ===== BACKGROUND JOBS =====

class JobEnqueueRequest(BaseModel):
    job_type: str
    payload: Dict
    priority: int = 0
    delay_seconds: int = 0


@router.post("/jobs/enqueue")
async def enqueue_job(
    request: JobEnqueueRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Enqueue a background job."""
    service = PerformanceService(db)
    job_id = service.enqueue_job(
        job_type=request.job_type,
        payload=request.payload,
        priority=request.priority,
        delay_seconds=request.delay_seconds,
    )
    return {"job_id": job_id, "status": "queued"}


@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get background job status."""
    service = PerformanceService(db)
    job = service.get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/process")
async def process_jobs(
    max_jobs: int = Query(10, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Process pending background jobs."""
    service = PerformanceService(db)
    processed = await service.process_pending_jobs(max_jobs)
    return {
        "processed": len(processed),
        "jobs": processed,
    }


@router.get("/jobs/queue/stats")
async def queue_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get job queue statistics."""
    service = PerformanceService(db)
    return service.get_queue_stats()
