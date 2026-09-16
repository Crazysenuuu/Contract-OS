import uuid
import asyncio
from typing import Optional
from app.worker import celery_app
from app.core.database import AsyncSessionLocal
from app.services.document_intelligence import DocumentIntelligenceService

@celery_app.task(name="extract_clauses_task")
def extract_clauses_task(agreement_id: str, text: str, version_id: Optional[str] = None):
    """Celery task to extract and classify clauses asynchronously."""
    async def _run_extract():
        async with AsyncSessionLocal() as db:
            service = DocumentIntelligenceService(db=db)
            await service.extract_clauses(
                agreement_id=agreement_id,
                text=text,
                version_id=version_id
            )

    loop = asyncio.get_event_loop()
    loop.run_until_complete(_run_extract())
