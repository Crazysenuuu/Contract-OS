"""Translation sync API endpoints for contract versioning."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from pydantic import BaseModel
from uuid import UUID

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.services.translation_sync import TranslationSyncService

router = APIRouter(prefix="/translation-sync", tags=["Translation Sync"])


# ===== Request Models =====

class CreateVersionRequest(BaseModel):
    content: str
    sync_existing: bool = True


class UpdateTranslationRequest(BaseModel):
    language_code: str
    title: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None


class SyncTranslationsRequest(BaseModel):
    from_version_id: str
    to_version_id: str


class BulkSyncRequest(BaseModel):
    to_version_id: str


# ===== Version Translation Endpoints =====

@router.post("/agreements/{agreement_id}/versions/translate")
async def create_version_with_translations(
    agreement_id: UUID,
    request: CreateVersionRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new version with automatic translation sync."""
    service = TranslationSyncService(db)
    try:
        version = await service.create_version_with_translations(
            agreement_id=str(agreement_id),
            content=request.content,
            created_by=str(current_user.id),
            sync_existing=request.sync_existing,
        )
        return {
            "version_id": version.id,
            "version_number": version.version_number,
            "translation_sync": version.translation_sync,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/agreements/{agreement_id}/versions/{version_id}/translations")
async def get_version_translations(
    agreement_id: UUID,
    version_id: UUID,
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db)
):
    """Get translation status for a specific version."""
    service = TranslationSyncService(db)
    result = await service.get_version_translation_status(
        agreement_id=str(agreement_id),
        version_id=str(version_id),
    )
    return result


@router.post("/agreements/{agreement_id}/versions/{version_id}/translations")
async def update_version_translation(
    agreement_id: UUID,
    version_id: UUID,
    request: UpdateTranslationRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update a translation for a specific version."""
    service = TranslationSyncService(db)
    try:
        localized = await service.update_translation_for_version(
            version_id=str(version_id),
            language_code=request.language_code,
            title=request.title,
            content=request.content,
            summary=request.summary,
            reviewed_by=str(current_user.id),
        )
        return {
            "id": localized.id,
            "language": request.language_code,
            "status": localized.status.value,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/agreements/{agreement_id}/sync-translations")
async def sync_translations(
    agreement_id: UUID,
    request: SyncTranslationsRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Sync translations between two versions."""
    service = TranslationSyncService(db)
    result = await service.sync_translations_to_version(
        agreement_id=str(agreement_id),
        from_version_id=request.from_version_id,
        to_version_id=request.to_version_id,
        source_content="",  # Will be fetched internally
    )
    return result


@router.post("/agreements/{agreement_id}/bulk-sync")
async def bulk_sync_translations(
    agreement_id: UUID,
    request: BulkSyncRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Bulk sync all translations for an agreement to a version."""
    service = TranslationSyncService(db)
    try:
        result = await service.bulk_sync_translations(
            agreement_id=str(agreement_id),
            to_version_id=request.to_version_id,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/agreements/{agreement_id}/compare-translations")
async def compare_version_translations(
    agreement_id: UUID,
    version_id_1: str = Query(...),
    version_id_2: str = Query(...),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db)
):
    """Compare translations between two versions."""
    service = TranslationSyncService(db)
    result = await service.compare_version_translations(
        agreement_id=str(agreement_id),
        version_id_1=version_id_1,
        version_id_2=version_id_2,
    )
    return result


# ===== Clause Translation Sync =====

class SyncClauseTranslationsRequest(BaseModel):
    clause_id: str
    source_content: str
    target_languages: List[str] = ["en", "si", "ta", "zh"]


@router.post("/clauses/sync")
async def sync_clause_translations(
    request: SyncClauseTranslationsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Sync clause translations when content changes."""
    service = TranslationSyncService(db)
    result = await service.sync_clause_translations(
        clause_id=request.clause_id,
        source_content=request.source_content,
        target_languages=request.target_languages,
    )
    return result


# ===== Translation Dashboard =====

@router.get("/agreements/{agreement_id}/translation-dashboard")
async def translation_dashboard(
    agreement_id: UUID,
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db)
):
    """Get translation overview dashboard for an agreement."""
    from sqlalchemy import select, func
    from app.models.agreement import AgreementVersion
    from app.models.i18n import LocalizedContent, DocumentLocale, Language

    # Get all versions (async)
    versions_result = await db.execute(
        select(AgreementVersion)
        .where(AgreementVersion.agreement_id == agreement_id)
        .order_by(AgreementVersion.version_number.desc())
    )
    versions = versions_result.scalars().all()

    # Get locale settings
    locale_result = await db.execute(
        select(DocumentLocale).where(DocumentLocale.agreement_id == agreement_id)
    )
    locale = locale_result.scalar_one_or_none()

    primary_lang = locale.primary_language if locale else "en"
    secondary_langs = locale.secondary_languages if locale else []
    all_langs = [primary_lang] + secondary_langs

    # Get translation counts per version
    version_data = []
    for v in versions:
        translations_result = await db.execute(
            select(LocalizedContent).where(
                LocalizedContent.source_type == "agreement_version",
                LocalizedContent.source_id == v.id,
            )
        )
        translations = translations_result.scalars().all()

        translated_langs = []
        for t in translations:
            lang_result = await db.execute(
                select(Language).where(Language.id == t.language_id)
            )
            lang = lang_result.scalar_one_or_none()
            if lang:
                translated_langs.append({
                    "code": lang.code,
                    "status": t.status.value,
                    "quality_score": t.quality_score,
                })

        version_data.append({
            "version_id": v.id,
            "version_number": v.version_number,
            "status": v.status,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "translation_sync": v.translation_sync,
            "translated_languages": translated_langs,
        })

    # Overall stats
    total_translations_result = await db.execute(
        select(func.count(LocalizedContent.id)).where(
            LocalizedContent.source_type == "agreement_version",
        )
    )
    total_translations = total_translations_result.scalar()

    return {
        "agreement_id": str(agreement_id),
        "primary_language": primary_lang,
        "secondary_languages": secondary_langs,
        "total_versions": len(versions),
        "total_translations": total_translations,
        "versions": version_data,
    }
