import uuid
import asyncio
import logging
from typing import Optional
from sqlalchemy import select
from app.worker import celery_app
from app.core.database import AsyncSessionLocal
from app.models.agreement import Agreement
from app.services.document_intelligence import DocumentIntelligenceService
from app.services.tenant_context import tenant_scope

logger = logging.getLogger(__name__)

@celery_app.task(name="extract_clauses_task")
def extract_clauses_task(agreement_id: str, text: str, version_id: Optional[str] = None):
    """Celery task to extract and classify clauses asynchronously."""
    async def _run_extract():
        async with AsyncSessionLocal() as db:
            # This task is addressed by agreement, not tenant, so resolve the
            # owner first: RLS has no request to read it from and would
            # otherwise scope the worker's queries to no tenant at all.
            org_row = await db.execute(
                select(Agreement.organization_id).where(
                    Agreement.id == uuid.UUID(agreement_id)
                )
            )
            org_id = org_row.scalar_one_or_none()
            if org_id is None:
                logger.warning(
                    "[extract_clauses] agreement %s not found; skipping",
                    agreement_id,
                )
                return

            async with tenant_scope(db, org_id):
                service = DocumentIntelligenceService(db=db)
                await service.extract_clauses(
                    agreement_id=agreement_id,
                    text=text,
                    version_id=version_id
                )

    loop = asyncio.get_event_loop()
    loop.run_until_complete(_run_extract())
