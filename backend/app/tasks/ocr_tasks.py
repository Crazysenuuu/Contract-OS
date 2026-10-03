import uuid
import asyncio
from typing import Optional
from app.worker import celery_app
from app.core.database import AsyncSessionLocal
from app.services import ocr_service
from app.services.tenant_context import tenant_scope

@celery_app.task(name="process_ocr_document")
def process_ocr_document(
    organization_id: str,
    job_id: str,
    filename: str,
    content_ref: str,
    mime_type: str = "application/pdf",
    provider: Optional[str] = None,
):
    """Celery task to run OCR processing asynchronously."""
    async def _run_process():
        async with AsyncSessionLocal() as db:
            # Worker sessions have no request to set RLS context from, so the
            # tenant has to be pinned explicitly from the task argument.
            async with tenant_scope(db, uuid.UUID(organization_id)):
                await ocr_service.process_document(
                    db=db,
                    organization_id=uuid.UUID(organization_id),
                    job_id=uuid.UUID(job_id),
                    filename=filename,
                    content_ref=content_ref,
                    mime_type=mime_type,
                    provider=provider,
                )
                await db.commit()

    # Create an event loop if there isn't one
    loop = asyncio.get_event_loop()
    loop.run_until_complete(_run_process())
