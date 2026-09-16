"""Translation queue API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from pydantic import BaseModel
from uuid import UUID

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.services.translation_queue_service import TranslationQueueService
from app.models.translation_queue import QueueStatus, TranslationPriority

router = APIRouter(prefix="/translation-queue", tags=["Translation Queue"])


# ===== Request Models =====

class EnqueueTranslationRequest(BaseModel):
    source_type: str
    source_id: str
    target_language: str
    source_content: str
    source_title: Optional[str] = None
    source_language: str = "en"
    priority: str = "normal"


class BulkEnqueueRequest(BaseModel):
    source_type: str
    source_id: str
    target_languages: List[str]
    source_content: str
    source_title: Optional[str] = None
    priority: str = "normal"


class AutoQueueRequest(BaseModel):
    source_type: str
    source_id: str
    source_content: str
    source_title: Optional[str] = None


class CompleteTranslationRequest(BaseModel):
    translated_content: str
    translated_title: Optional[str] = None
    quality_score: Optional[float] = None
    confidence_score: Optional[float] = None
    is_machine_translated: bool = True


class UseTemplateRequest(BaseModel):
    template_id: str
    variables: dict = {}
    target_languages: List[str]


# ===== Queue Endpoints =====

@router.get("/stats")
async def get_queue_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get translation queue statistics."""
    service = TranslationQueueService(db)
    return await service.get_queue_stats()


@router.get("/items")
async def list_queue_items(
    status: Optional[str] = None,
    target_language: Optional[str] = None,
    source_type: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List translation queue items."""
    service = TranslationQueueService(db)
    status_enum = QueueStatus(status) if status else None
    items = await service.get_queue_items(
        status=status_enum,
        target_language=target_language,
        source_type=source_type,
        limit=limit,
        offset=offset,
    )
    return [{
        "id": item.id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "target_language": item.target_language,
        "source_language": item.source_language,
        "source_title": item.source_title,
        "status": item.status.value,
        "priority": item.priority.value,
        "source": item.source.value,
        "attempts": item.attempts,
        "error_message": item.error_message,
        "quality_score": item.quality_score,
        "queued_at": item.queued_at.isoformat() if item.queued_at else None,
        "started_at": item.started_at.isoformat() if item.started_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
    } for item in items]


@router.get("/items/{item_id}")
async def get_queue_item(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get queue item details."""
    service = TranslationQueueService(db)
    item = await service.get_item_status(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    return {
        "id": item.id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "target_language": item.target_language,
        "source_language": item.source_language,
        "source_title": item.source_title,
        "source_content": item.source_content[:500] + "..." if len(item.source_content or "") > 500 else item.source_content,
        "translated_content": item.translated_content[:500] + "..." if item.translated_content and len(item.translated_content) > 500 else item.translated_content,
        "status": item.status.value,
        "priority": item.priority.value,
        "source": item.source.value,
        "attempts": item.attempts,
        "max_attempts": item.max_attempts,
        "error_message": item.error_message,
        "quality_score": item.quality_score,
        "confidence_score": item.confidence_score,
        "is_machine_translated": item.is_machine_translated,
        "needs_review": item.needs_review,
        "queued_at": item.queued_at.isoformat() if item.queued_at else None,
        "started_at": item.started_at.isoformat() if item.started_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "metadata": item.extra_data,
    }


@router.post("/enqueue")
async def enqueue_translation(
    request: EnqueueTranslationRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Add a translation to the queue."""
    service = TranslationQueueService(db)
    priority_map = {
        "low": TranslationPriority.LOW,
        "normal": TranslationPriority.NORMAL,
        "high": TranslationPriority.HIGH,
        "urgent": TranslationPriority.URGENT,
    }

    item = await service.enqueue_translation(
        source_type=request.source_type,
        source_id=request.source_id,
        target_language=request.target_language,
        source_content=request.source_content,
        source_title=request.source_title,
        source_language=request.source_language,
        priority=priority_map.get(request.priority, TranslationPriority.NORMAL),
        organization_id=str(org_id),
    )

    return {
        "id": item.id,
        "status": item.status.value,
        "target_language": item.target_language,
    }


@router.post("/enqueue/bulk")
async def bulk_enqueue(
    request: BulkEnqueueRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Add multiple translations to the queue."""
    service = TranslationQueueService(db)
    priority_map = {
        "low": TranslationPriority.LOW,
        "normal": TranslationPriority.NORMAL,
        "high": TranslationPriority.HIGH,
        "urgent": TranslationPriority.URGENT,
    }

    items = await service.enqueue_bulk_translations(
        source_type=request.source_type,
        source_id=request.source_id,
        target_languages=request.target_languages,
        source_content=request.source_content,
        source_title=request.source_title,
        priority=priority_map.get(request.priority, TranslationPriority.NORMAL),
        organization_id=str(org_id),
    )

    return {
        "queued": len(items),
        "items": [{
            "id": item.id,
            "target_language": item.target_language,
            "status": item.status.value,
        } for item in items],
    }


@router.post("/auto-queue")
async def auto_queue_missing(
    request: AutoQueueRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Automatically queue translations for missing languages."""
    service = TranslationQueueService(db)
    items = await service.auto_queue_missing_translations(
        source_type=request.source_type,
        source_id=request.source_id,
        source_content=request.source_content,
        source_title=request.source_title,
        organization_id=str(org_id),
    )

    return {
        "queued": len(items),
        "languages": [item.target_language for item in items],
    }


@router.post("/process")
async def process_next(
    worker_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Process the next item in the queue."""
    service = TranslationQueueService(db)
    item = await service.process_next_item(worker_id=worker_id)

    if not item:
        return {"status": "empty", "message": "No items in queue"}

    return {
        "id": item.id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "target_language": item.target_language,
        "source_content": item.source_content,
        "source_title": item.source_title,
        "status": item.status.value,
    }


@router.post("/items/{item_id}/complete")
async def complete_translation(
    item_id: str,
    request: CompleteTranslationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Mark a translation as completed."""
    service = TranslationQueueService(db)
    try:
        item = await service.complete_translation(
            item_id=item_id,
            translated_content=request.translated_content,
            translated_title=request.translated_title,
            quality_score=request.quality_score,
            confidence_score=request.confidence_score,
            is_machine_translated=request.is_machine_translated,
        )
        return {
            "id": item.id,
            "status": item.status.value,
            "target_language": item.target_language,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/items/{item_id}/fail")
async def fail_translation(
    item_id: str,
    error_message: str,
    retry: bool = True,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Mark a translation as failed."""
    service = TranslationQueueService(db)
    try:
        item = await service.fail_translation(
            item_id=item_id,
            error_message=error_message,
            retry=retry,
        )
        return {
            "id": item.id,
            "status": item.status.value,
            "attempts": item.attempts,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/items/{item_id}/cancel")
async def cancel_translation(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Cancel a translation task."""
    service = TranslationQueueService(db)
    try:
        item = await service.cancel_translation(item_id)
        return {
            "id": item.id,
            "status": item.status.value,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/retry-failed")
async def retry_failed(
    max_items: int = Query(10, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retry all failed items."""
    service = TranslationQueueService(db)
    items = await service.retry_failed_items(max_items)
    return {
        "retried": len(items),
        "items": [{"id": item.id, "target_language": item.target_language} for item in items],
    }


@router.post("/agreement/{agreement_id}/process")
async def process_agreement_translations(
    agreement_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Process all pending translations for an agreement."""
    service = TranslationQueueService(db)
    result = await service.process_pending_for_agreement(str(agreement_id))
    return result


# ===== Template Endpoints =====

@router.get("/templates")
async def list_templates(
    category: Optional[str] = None,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List translation templates."""
    service = TranslationQueueService(db)
    templates = await service.get_templates(category=category, organization_id=str(org_id))
    return [{
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "category": t.category,
        "source_language": t.source_language,
        "source_variables": t.source_variables,
        "translations": list(t.translations.keys()) if t.translations else [],
        "usage_count": t.usage_count,
        "is_system": t.is_system,
    } for t in templates]


@router.post("/templates/{template_id}/use")
async def use_template(
    template_id: str,
    request: UseTemplateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Use a template to create translations."""
    service = TranslationQueueService(db)
    try:
        items = await service.use_template(
            template_id=template_id,
            variables=request.variables,
            target_languages=request.target_languages,
        )
        return {
            "queued": len(items),
            "items": [{"id": item.id, "target_language": item.target_language} for item in items],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ===== Worker Endpoints =====

@router.post("/workers/register")
async def register_worker(
    worker_id: str,
    hostname: str = None,
    process_id: int = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Register a translation worker."""
    service = TranslationQueueService(db)
    worker = await service.register_worker(
        worker_id=worker_id,
        hostname=hostname,
        process_id=process_id,
    )
    return {
        "id": worker.id,
        "worker_id": worker.worker_id,
        "is_active": worker.is_active,
    }


@router.post("/workers/{worker_id}/heartbeat")
async def worker_heartbeat(
    worker_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update worker heartbeat."""
    service = TranslationQueueService(db)
    success = await service.heartbeat(worker_id)
    return {"success": success}


@router.get("/workers")
async def list_workers(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List active workers."""
    service = TranslationQueueService(db)
    workers = await service.get_active_workers()
    return [{
        "id": w.id,
        "worker_id": w.worker_id,
        "hostname": w.hostname,
        "tasks_completed": w.tasks_completed,
        "tasks_failed": w.tasks_failed,
        "last_heartbeat": w.last_heartbeat.isoformat() if w.last_heartbeat else None,
    } for w in workers]
