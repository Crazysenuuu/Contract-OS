"""i18n API endpoints for multi-language support."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List, Dict
from pydantic import BaseModel
from uuid import UUID

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.services.translation_service import TranslationService

router = APIRouter(prefix="/i18n", tags=["Internationalization"])


# ===== Language Endpoints =====

@router.get("/languages")
async def list_languages(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db)
):
    """List all supported languages."""
    service = TranslationService(db)
    languages = await service.get_languages(active_only=active_only)
    return [{
        "id": lang.id,
        "code": lang.code,
        "name": lang.name,
        "native_name": lang.native_name,
        "locale": lang.locale,
        "direction": lang.direction.value,
        "date_format": lang.has_date_format,
        "currency": lang.has_currency,
        "legal_systems": lang.legal_systems,
        "supported_jurisdictions": lang.supported_jurisdictions,
        "is_beta": lang.is_beta,
    } for lang in languages]


@router.get("/languages/{code}")
async def get_language(
    code: str,
    db: AsyncSession = Depends(get_db)
):
    """Get language details."""
    service = TranslationService(db)
    lang = await service.get_language(code)
    if not lang:
        raise HTTPException(status_code=404, detail=f"Language '{code}' not found")

    return {
        "id": lang.id,
        "code": lang.code,
        "name": lang.name,
        "native_name": lang.native_name,
        "locale": lang.locale,
        "direction": lang.direction.value,
        "date_format": lang.has_date_format,
        "currency": lang.has_currency,
        "legal_systems": lang.legal_systems,
        "supported_jurisdictions": lang.supported_jurisdictions,
    }


@router.post("/languages/seed")
async def seed_languages(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Seed default languages (admin only)."""
    service = TranslationService(db)
    count = await service.seed_languages()
    return {"seeded": count, "message": f"Seeded {count} new languages"}


# ===== Translation Endpoints =====

class SetTranslationRequest(BaseModel):
    namespace: str
    key: str
    language_code: str
    value: str
    context: Optional[str] = None


@router.get("/translations/{namespace}")
async def get_translations(
    namespace: str,
    language: str = Query("en"),
    db: AsyncSession = Depends(get_db)
):
    """Get all translations for a namespace."""
    service = TranslationService(db)
    translations = await service.get_translations(namespace, language)
    return {
        "namespace": namespace,
        "language": language,
        "translations": translations,
        "count": len(translations),
    }


@router.get("/translations/{namespace}/{key}")
async def get_translation(
    namespace: str,
    key: str,
    language: str = Query("en"),
    db: AsyncSession = Depends(get_db)
):
    """Get a single translation."""
    service = TranslationService(db)
    value = await service.get_translation(namespace, key, language)
    if value is None:
        raise HTTPException(status_code=404, detail="Translation not found")

    return {
        "namespace": namespace,
        "key": key,
        "language": language,
        "value": value,
    }


@router.post("/translations")
async def set_translation(
    request: SetTranslationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Set a translation."""
    service = TranslationService(db)
    try:
        translation = await service.set_translation(
            namespace=request.namespace,
            key=request.key,
            language_code=request.language_code,
            value=request.value,
            context=request.context,
        )
        return {
            "id": translation.id,
            "namespace": translation.namespace,
            "key": translation.key,
            "language": request.language_code,
            "status": translation.status.value,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/translations/seed")
async def seed_translations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Seed default UI translations."""
    service = TranslationService(db)
    count = await service.seed_ui_translations()
    return {"seeded": count, "message": f"Seeded {count} new translations"}


# ===== Localized Content Endpoints =====

class LocalizedContentRequest(BaseModel):
    source_type: str
    source_id: str
    language_code: str
    title: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None


@router.get("/content/{source_type}/{source_id}")
async def get_localized_content(
    source_type: str,
    source_id: str,
    language: str = Query("en"),
    db: AsyncSession = Depends(get_db)
):
    """Get localized content for an entity."""
    service = TranslationService(db)
    localized = await service.get_localized_content(source_type, source_id, language)

    if not localized:
        return {"found": False, "language": language}

    return {
        "found": True,
        "id": localized.id,
        "title": localized.title,
        "content": localized.content,
        "summary": localized.summary,
        "status": localized.status.value,
        "language": language,
    }


@router.post("/content")
async def set_localized_content(
    request: LocalizedContentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Set localized content."""
    service = TranslationService(db)
    try:
        localized = await service.set_localized_content(
            source_type=request.source_type,
            source_id=request.source_id,
            language_code=request.language_code,
            title=request.title,
            content=request.content,
            summary=request.summary,
        )
        return {
            "id": localized.id,
            "status": localized.status.value,
            "language": request.language_code,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ===== Clause Translation Endpoints =====

@router.get("/clauses/{clause_id}/translations")
async def get_clause_translations(
    clause_id: str,
    languages: str = Query("en,si,ta"),
    db: AsyncSession = Depends(get_db)
):
    """Get clause translations in multiple languages."""
    service = TranslationService(db)
    lang_codes = [l.strip() for l in languages.split(",")]
    translations = await service.get_clause_translations(clause_id, lang_codes)

    result = {}
    for code, trans in translations.items():
        if trans:
            result[code] = {
                "title": trans.title,
                "text": trans.text,
                "explanation": trans.explanation,
                "status": trans.status.value,
            }
        else:
            result[code] = None

    return {
        "clause_id": clause_id,
        "translations": result,
        "available_languages": [code for code, t in translations.items() if t],
    }


# ===== Glossary Endpoints =====

class GlossaryTermRequest(BaseModel):
    source_term: str
    translations: Dict[str, str]
    category: Optional[str] = None
    definition: Optional[str] = None


@router.get("/glossary")
async def get_glossary(
    language: str = Query("en"),
    db: AsyncSession = Depends(get_db)
):
    """Get glossary terms."""
    service = TranslationService(db)

    if language == "en":
        # Return all source terms
        from app.models.i18n import GlossaryTerm
        from sqlalchemy import select as _select
        result = await db.execute(_select(GlossaryTerm))
        terms = result.scalars().all()
        return [{
            "source_term": t.source_term,
            "translations": t.translations,
            "category": t.category,
            "definition": t.definition,
            "usage_count": t.usage_count,
        } for t in terms]
    else:
        return await service.get_glossary_for_language(language)


@router.get("/glossary/{term}")
async def get_glossary_term(
    term: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a glossary term with all translations."""
    service = TranslationService(db)
    glossary_term = await service.get_glossary_term(term)

    if not glossary_term:
        raise HTTPException(status_code=404, detail=f"Term '{term}' not found")

    return {
        "source_term": glossary_term.source_term,
        "translations": glossary_term.translations,
        "category": glossary_term.category,
        "definition": glossary_term.definition,
        "usage_count": glossary_term.usage_count,
    }


@router.post("/glossary")
async def add_glossary_term(
    request: GlossaryTermRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Add a term to the glossary."""
    service = TranslationService(db)
    term = await service.add_glossary_term(
        source_term=request.source_term,
        translations=request.translations,
        category=request.category,
        definition=request.definition,
    )
    return {
        "id": term.id,
        "source_term": term.source_term,
        "translations": term.translations,
    }


@router.post("/glossary/seed")
async def seed_glossary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Seed default legal glossary."""
    service = TranslationService(db)
    count = await service.seed_glossary()
    return {"seeded": count, "message": f"Seeded {count} glossary terms"}


# ===== Document Locale Endpoints =====

class DocumentLocaleRequest(BaseModel):
    agreement_id: str
    primary_language: str = "en"
    secondary_languages: List[str] = []
    translation_mode: str = "side_by_side"
    governing_language: Optional[str] = None
    number_format: str = "western"
    date_format: str = "DD/MM/YYYY"
    currency_format: str = "LKR ##,###"


@router.get("/documents/{agreement_id}/locale")
async def get_document_locale(
    agreement_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get document locale settings."""
    service = TranslationService(db)
    locale = await service.get_document_locale(agreement_id)

    if not locale:
        return {
            "found": False,
            "defaults": {
                "primary_language": "en",
                "secondary_languages": [],
                "translation_mode": "side_by_side",
                "governing_language": "en",
            }
        }

    return {
        "found": True,
        "id": locale.id,
        "primary_language": locale.primary_language,
        "secondary_languages": locale.secondary_languages,
        "translation_mode": locale.translation_mode,
        "governing_language": locale.governing_language,
        "show_governing_clause": locale.show_governing_clause,
        "number_format": locale.number_format,
        "date_format": locale.date_format,
        "currency_format": locale.currency_format,
    }


@router.post("/documents/{agreement_id}/locale")
async def set_document_locale(
    agreement_id: str,
    request: DocumentLocaleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Set document locale settings."""
    service = TranslationService(db)
    try:
        locale = await service.set_document_locale(
            agreement_id=agreement_id,
            primary_language=request.primary_language,
            secondary_languages=request.secondary_languages,
            governing_language=request.governing_language,
            translation_mode=request.translation_mode,
            number_format=request.number_format,
            date_format=request.date_format,
            currency_format=request.currency_format,
        )
        return {
            "id": locale.id,
            "primary_language": locale.primary_language,
            "secondary_languages": locale.secondary_languages,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ===== Utility Endpoints =====

@router.get("/detect-language")
async def detect_language(text: str = Query(...)):
    """Detect language from text."""
    service = TranslationService(None)  # No DB needed for detection
    detected = service.detect_language(text)
    return {
        "text_preview": text[:100] + "..." if len(text) > 100 else text,
        "detected_language": detected,
    }


@router.get("/jurisdiction/{code}/languages")
async def get_jurisdiction_languages(
    code: str,
    db: AsyncSession = Depends(get_db)
):
    """Get languages supported in a jurisdiction."""
    service = TranslationService(db)
    languages = await service.get_languages_for_jurisdiction(code)
    return [{
        "code": lang.code,
        "name": lang.name,
        "native_name": lang.native_name,
    } for lang in languages]


@router.get("/format/date")
async def format_date_endpoint(
    date: str = Query(..., description="ISO date string"),
    language: str = Query("en"),
    format: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Format date according to locale."""
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    service = TranslationService(db)
    formatted = await service.format_date(dt, language, format)
    return {"date": date, "formatted": formatted, "language": language}


@router.get("/format/currency")
async def format_currency_endpoint(
    amount: float = Query(...),
    language: str = Query("en"),
    currency: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Format currency according to locale."""
    service = TranslationService(db)
    formatted = await service.format_currency(amount, language, currency)
    return {"amount": amount, "formatted": formatted, "language": language}


@router.get("/css/rtl")
async def get_rtl_css():
    """Get CSS for RTL language support."""
    rtl_css = """
/* RTL Language Support */
[dir="rtl"] {
  direction: rtl;
  text-align: right;
}

[dir="rtl"] .flex-row {
  flex-direction: row-reverse;
}

[dir="rtl"] .ml-auto {
  margin-left: 0;
  margin-right: auto;
}

[dir="rtl"] .mr-auto {
  margin-right: 0;
  margin-left: auto;
}

[dir="rtl"] .text-left {
  text-align: right;
}

[dir="rtl"] .text-right {
  text-align: left;
}

[dir="rtl"] .border-l {
  border-left: none;
  border-right: 1px solid;
}

[dir="rtl"] .border-r {
  border-right: none;
  border-left: 1px solid;
}

[dir="rtl"] .rounded-l {
  border-top-left-radius: 0;
  border-bottom-left-radius: 0;
  border-top-right-radius: 0.375rem;
  border-bottom-right-radius: 0.375rem;
}

[dir="rtl"] .rounded-r {
  border-top-right-radius: 0;
  border-bottom-right-radius: 0;
  border-top-left-radius: 0.375rem;
  border-bottom-left-radius: 0.375rem;
}

/* Form inputs */
[dir="rtl"] input[type="text"],
[dir="rtl"] input[type="email"],
[dir="rtl"] input[type="number"],
[dir="rtl"] textarea,
[dir="rtl"] select {
  text-align: right;
}

/* Table */
[dir="rtl"] th {
  text-align: right;
}

[dir="rtl"] td {
  text-align: right;
}
"""
    return PlainTextResponse(content=rtl_css, media_type="text/css")
