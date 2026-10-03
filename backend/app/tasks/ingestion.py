"""Celery tasks for document ingestion and bulk processing."""

import logging
import uuid

from app.worker import celery_app
from app.tasks.ocr_tasks import process_ocr_document

logger = logging.getLogger(__name__)


@celery_app.task(name="process_bulk_ingestion", bind=True, max_retries=3)
def process_bulk_ingestion(self, organization_id: str, job_id: str, document_refs: list[dict]):
    """Kick off bulk ingestion: virus-scan then OCR each document.

    document_refs items may include:
      - filename       (str, required)
      - content_ref    (str, required) — storage path / pre-signed URL
      - mime_type      (str, optional)
      - provider       (str, optional)
      - content_bytes  (bytes, optional) — inline bytes for small uploads;
                        when absent the scan is skipped here and deferred to
                        the OCR worker which fetches bytes from storage.
    """
    from app.services.virus_scan_service import scan_bytes, ScanResult

    # Celery tasks run outside the request's session lifecycle, so persistence
    # uses a fresh session. Resolved lazily so importing this module in tests
    # (where the DB may be SQLite) does not require engine setup.
    def _mark_rejected(ref: str, reason: str) -> None:
        try:
            from sqlalchemy import update
            from app.core.database import AsyncSessionLocal
            from app.models.ingestion import OCRDocument

            import asyncio

            async def _apply() -> None:
                async with AsyncSessionLocal() as session:
                    # Pin the tenant: this worker session has no RLS context
                    # from a request, so without it an RLS-protected
                    # ocr_documents would make the UPDATE match zero rows.
                    from app.services.tenant_context import tenant_scope

                    async with tenant_scope(session, uuid.UUID(organization_id)):
                        await session.execute(
                            update(OCRDocument)
                            .where(OCRDocument.content_ref == ref)
                            .values(
                                status="failed",
                                error_message=reason,
                            )
                        )
                        await session.commit()

            asyncio.run(_apply())
        except Exception:
            # Persistence is best-effort here; the security decision (not
            # dispatching OCR on a rejected file) never depends on it.
            logger.exception(
                "[ingestion] Could not persist rejection for %s", ref
            )

    for doc in document_refs:
        filename = doc["filename"]
        content_ref = doc["content_ref"]
        raw: bytes | None = doc.get("content_bytes")

        # ── Virus scan (inline bytes path) ──────────────────────────────────
        if raw is not None:
            try:
                result: ScanResult = scan_bytes(raw)
            except Exception as exc:
                logger.error(
                    "[ingestion] Virus scan error for %s (job=%s): %s",
                    filename,
                    job_id,
                    exc,
                )
                # Fail safe: reject the document rather than skip the scan
                logger.warning(
                    "[ingestion] Rejecting %s due to scan error — not dispatching OCR",
                    filename,
                )
                _mark_rejected(
                    content_ref,
                    f"Virus scan error: {exc}",
                )
                continue

            if result.infected:
                logger.warning(
                    "[ingestion] INFECTED file rejected: %s  threat=%s  backend=%s  job=%s",
                    filename,
                    result.threat,
                    result.backend,
                    job_id,
                )
                # Spec 1.22 §26 (malware scanning): an infected upload must be
                # visible as rejected, not left stuck in 'queued' forever.
                _mark_rejected(
                    content_ref,
                    f"Rejected: malware detected ({result.threat})",
                )
                continue

            logger.debug(
                "[ingestion] Scan clean: %s  backend=%s", filename, result.backend
            )

        # ── Dispatch OCR ────────────────────────────────────────────────────
        process_ocr_document.delay(  # pyright: ignore[reportFunctionMemberAccess]
            organization_id=organization_id,
            job_id=job_id,
            filename=filename,
            content_ref=content_ref,
            mime_type=doc.get("mime_type", "application/pdf"),
            provider=doc.get("provider"),
        )

