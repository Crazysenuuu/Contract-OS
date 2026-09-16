"""Translation progress API endpoints for real-time dashboard."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID
import json
import asyncio

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.services.translation_progress import TranslationProgressService

router = APIRouter(prefix="/translation-progress", tags=["Translation Progress"])


@router.get("/overall")
async def get_overall_progress(
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get overall translation progress."""
    service = TranslationProgressService(db)
    return await service.get_overall_progress(str(org_id))


@router.get("/live")
async def get_live_metrics(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get live metrics for real-time dashboard."""
    service = TranslationProgressService(db)
    return await service.get_live_metrics()


@router.get("/history")
async def get_progress_history(
    hours: int = Query(24, le=168),
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get translation progress history."""
    service = TranslationProgressService(db)
    return await service.get_progress_history(hours=hours, organization_id=str(org_id))


@router.get("/language/{language_code}")
async def get_language_progress(
    language_code: str,
    hours: int = Query(24, le=168),
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get progress for a specific language."""
    service = TranslationProgressService(db)
    return await service.get_language_history(
        language_code=language_code,
        hours=hours,
        organization_id=str(org_id)
    )


@router.get("/stream")
async def stream_progress(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Stream real-time progress updates via SSE."""
    service = TranslationProgressService(db)

    async def event_generator():
        # Send initial state
        progress = await service.get_overall_progress()
        yield f"data: {json.dumps(progress)}\n\n"

        # Poll for updates every 5 seconds
        for _ in range(120):  # 10 minutes max
            await asyncio.sleep(5)
            progress = await service.get_overall_progress()
            yield f"data: {json.dumps(progress)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/stats")
async def get_translation_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get comprehensive translation statistics."""
    service = TranslationProgressService(db)

    overall = await service.get_overall_progress()
    live = await service.get_live_metrics()

    return {
        "overall": overall["overall"],
        "by_language": overall["by_language"],
        "workers": overall["workers"],
        "throughput": overall["throughput"],
        "live": {
            "active_workers": live["active_workers"],
            "current_processing": len(live["current_processing"]),
        },
        "timestamp": overall["timestamp"],
    }


@router.get("/dashboard")
async def get_dashboard_data(
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get comprehensive dashboard data."""
    service = TranslationProgressService(db)

    overall = await service.get_overall_progress(str(org_id))
    live = await service.get_live_metrics()
    history = await service.get_progress_history(hours=24, organization_id=str(org_id))

    # Language breakdown with details
    language_details = {}
    for lang_code in overall["by_language"].keys():
        lang_history = await service.get_language_history(
            lang_code, hours=24, organization_id=str(org_id)
        )
        language_details[lang_code] = {
            **overall["by_language"][lang_code],
            **lang_history,
        }

    return {
        "summary": overall["overall"],
        "languages": language_details,
        "workers": overall["workers"],
        "throughput": overall["throughput"],
        "recent_completions": overall["recent_completions"],
        "live": {
            "queue_depth": live["queue_depth"],
            "active_workers": live["active_workers"],
            "current_processing": live["current_processing"],
        },
        "history": history,
    }
