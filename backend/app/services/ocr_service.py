"""Legacy contract ingestion & OCR pipeline (spec 24.1).

Provider abstraction over OCR engines (mock for dev, Tesseract when
available, cloud providers later). Quality control: every document gets a
confidence score; documents below the configured threshold are routed to a
human-in-the-loop (HITL) queue and are NOT committed to the contract
repository until manually verified.

Once text passes verification, the standard intelligence layer extracts
the same metadata (effective date, termination, renewal, liability cap,
parties) as natively generated contracts.
"""

from __future__ import annotations

import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings_lazy
from app.models.ingestion import (
    HumanReviewTask,
    IngestionJob,
    OCRDocument,
)

# Documents below this confidence must be real cloud OCR (never the mock
# engine) to be trusted for contract ingestion.
DEFAULT_CONFIDENCE_THRESHOLD = 0.8

# Implicit provider chain when nothing is configured explicitly:
# AWS Textract first (chosen over the mock engine by product decision),
# Google Document AI as fallback. 'mock' is never an implicit default.
_IMPLICIT_DEFAULT_CHAIN = ("aws_textract", "google_document_ai")


class OCRProviderError(Exception):
    """Raised when OCR extraction fails."""


class MockProviderInProductionError(OCRProviderError):
    """The mock OCR engine is not permitted in production."""


class _CloudFallbackExhaustedError(OCRProviderError):
    """No cloud OCR provider in the chain could extract the document."""


@dataclass
class OCRResult:
    """Result of an OCR extraction."""

    text: str
    confidence: float  # 0.0 - 1.0
    provider: str


class OCRProvider(ABC):
    """Abstract OCR provider."""

    name = "base"

    @abstractmethod
    def extract_text(self, data: bytes, *, filename: str) -> OCRResult:
        """Extract text + confidence from raw document bytes."""


class MockOCRProvider(OCRProvider):
    """Deterministic mock provider for development/testing.

    Returns a confidence derived from the file size so tests can exercise
    both the commit path and the HITL path.

    Guard: instantiating the mock engine in production raises — legacy
    ingestion must run on real cloud OCR (Textract/Doc AI), never on
    synthetic text.
    """

    name = "mock"

    def __init__(self) -> None:
        if get_settings_lazy().environment == "production":
            raise MockProviderInProductionError(
                "The mock OCR provider is disabled in production; "
                "configure OCR_PROVIDER=aws_textract (or google_document_ai)."
            )

    def extract_text(self, data: bytes, *, filename: str) -> OCRResult:
        # Degraded (tiny) files fall below the threshold -> HITL.
        confidence = min(1.0, max(0.0, len(data) / 5000))
        text = (
            "This is mock OCR extracted text for "
            f"{filename}. Effective date: 2026-01-01. "
            "Term: 12 months. Confidentiality: 2 years. "
            "Liability cap: USD 1,000,000. Parties: Acme Corp and Globex Inc."
        )
        return OCRResult(text=text, confidence=confidence, provider=self.name)


class TesseractOCRProvider(OCRProvider):
    """Tesseract-based OCR when pytesseract is installed."""

    name = "tesseract"

    def extract_text(self, data: bytes, *, filename: str) -> OCRResult:
        try:
            import io

            from PIL import Image  # type: ignore
            import pytesseract  # type: ignore
        except ImportError:
            raise OCRProviderError("pytesseract/PIL not installed")

        image = Image.open(io.BytesIO(data))
        text = pytesseract.image_to_string(image)
        # Tesseract does not emit a confidence per document; heuristic.
        return OCRResult(text=text, confidence=0.85, provider=self.name)


class AwsTextractProvider(OCRProvider):
    """AWS Textract OCR Provider."""

    name = "aws_textract"

    def extract_text(self, data: bytes, *, filename: str) -> OCRResult:
        try:
            import boto3
        except ImportError:
            raise OCRProviderError("boto3 not installed")
        
        client = boto3.client('textract', region_name=os.environ.get("AWS_REGION", "us-east-1"))
        response = client.detect_document_text(Document={'Bytes': data})
        
        blocks = response.get('Blocks', [])
        text = "\n".join([block['Text'] for block in blocks if block['BlockType'] == 'LINE'])
        
        # Calculate average confidence of LINE blocks
        line_blocks = [b for b in blocks if b['BlockType'] == 'LINE']
        if line_blocks:
            avg_conf = sum([b.get('Confidence', 0.0) for b in line_blocks]) / len(line_blocks)
            confidence = avg_conf / 100.0  # textract returns 0-100
        else:
            confidence = 0.0
            
        return OCRResult(text=text, confidence=confidence, provider=self.name)


class GoogleDocumentAiProvider(OCRProvider):
    """Google Document AI OCR Provider."""

    name = "google_document_ai"

    def extract_text(self, data: bytes, *, filename: str) -> OCRResult:
        try:
            from google.cloud import documentai
            from google.api_core.client_options import ClientOptions
        except ImportError:
            raise OCRProviderError("google-cloud-documentai not installed")
            
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us")
        processor_id = os.environ.get("GOOGLE_DOC_AI_PROCESSOR_ID")
        
        if not all([project_id, processor_id]):
            raise OCRProviderError("Missing Google Cloud Document AI configuration")

        # Narrow types: both are guaranteed non-None after the guard above.
        assert project_id is not None
        assert processor_id is not None

        opts = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
        client = documentai.DocumentProcessorServiceClient(client_options=opts)
        name = client.processor_path(project_id, location, processor_id)
        
        raw_document = documentai.RawDocument(content=data, mime_type="application/pdf")
        request = documentai.ProcessRequest(name=name, raw_document=raw_document)
        
        result = client.process_document(request=request)
        document = result.document
        text = document.text
        
        # Document AI confidence is typically page-level or block-level, we average over pages
        if document.pages:
            avg_conf = sum([p.layout.confidence for p in document.pages if p.layout]) / len(document.pages)
        else:
            avg_conf = 0.85 # Fallback
            
        return OCRResult(text=text, confidence=avg_conf, provider=self.name)


class AzureFormRecognizerProvider(OCRProvider):
    """Azure Form Recognizer (Document Intelligence) OCR Provider."""

    name = "azure_form_recognizer"

    def extract_text(self, data: bytes, *, filename: str) -> OCRResult:
        try:
            from azure.ai.formrecognizer import DocumentAnalysisClient
            from azure.core.credentials import AzureKeyCredential
        except ImportError:
            raise OCRProviderError("azure-ai-formrecognizer not installed")
            
        endpoint = os.environ.get("AZURE_FORM_RECOGNIZER_ENDPOINT")
        key = os.environ.get("AZURE_FORM_RECOGNIZER_KEY")
        
        if not endpoint or not key:
            raise OCRProviderError("Missing Azure Form Recognizer configuration")
            
        document_analysis_client = DocumentAnalysisClient(
            endpoint=endpoint, credential=AzureKeyCredential(key)
        )
        
        poller = document_analysis_client.begin_analyze_document("prebuilt-read", data)
        result = poller.result()
        
        text = result.content
        # Average confidence across pages using word-level scores (DocumentWord has .confidence)
        if result.pages:
            pages_conf = []
            for p in result.pages:
                if p.words:
                    pages_conf.append(
                        sum(w.confidence for w in p.words if w.confidence is not None) / len(p.words)
                    )
            confidence = sum(pages_conf) / len(pages_conf) if pages_conf else 0.85
        else:
            confidence = 0.85

            
        return OCRResult(text=text, confidence=confidence, provider=self.name)


def _ocr_environment() -> str:
    """Environment guard value, centralized so tests can monkeypatch it."""
    return get_settings_lazy().environment


def _configured_providers(explicit: str | None) -> tuple[str, ...]:
    """Resolve the ordered provider chain for this extraction.

    - explicit request (per-document/per-job override): single-provider chain
      as requested; 'mock' allowed in non-production only.
    - OCR_PROVIDER env: [env, fallback] — cloud fallback still applies.
    - nothing configured: the implicit default chain (aws -> google).
    """
    if explicit:
        chain: tuple[str, ...] = (explicit.lower(),)
    else:
        env = get_settings_lazy().ocr_provider
        if env:
            env_chain = [env.lower()]
            fallback = get_settings_lazy().ocr_fallback_provider
            if fallback and fallback.lower() not in env_chain:
                env_chain.append(fallback.lower())
            chain = tuple(env_chain)
        else:
            chain = _IMPLICIT_DEFAULT_CHAIN

    # Production hard-stop: the mock engine may never be selected there,
    # whether implicitly or via an explicit misconfiguration.
    if _ocr_environment() == "production":
        if "mock" in chain:
            raise MockProviderInProductionError(
                "The mock OCR provider is disabled in production; "
                "configure OCR_PROVIDER=aws_textract (or google_document_ai)."
            )
    return chain


def get_ocr_provider(provider: str | None = None) -> OCRProvider:
    """Resolve the OCR provider (kept for backward compatibility).

    Single-provider resolution; raises ``MockProviderInProductionError`` if
    the resolution lands on the mock engine in production. For the full
    failover behavior use :func:`get_ocr_provider_chain`.
    """
    chain = _configured_providers(provider)
    return _instantiate_provider(chain[0])


def _instantiate_provider(name: str) -> OCRProvider:
    if name == "tesseract":
        return TesseractOCRProvider()
    if name == "aws_textract":
        return AwsTextractProvider()
    if name == "google_document_ai":
        return GoogleDocumentAiProvider()
    if name == "azure_form_recognizer":
        return AzureFormRecognizerProvider()
    if name == "mock":
        return MockOCRProvider()
    raise OCRProviderError(f"Unknown OCR provider: {name}")


def get_ocr_provider_chain(provider: str | None = None) -> list[OCRProvider]:
    """Ordered providers to attempt: primary first, then the fallback.

    Instantiation errors (missing SDK, missing credentials) are skipped in
    the chain so a broken secondary never masks a working primary.
    """
    providers: list[OCRProvider] = []
    for name in _configured_providers(provider):
        try:
            providers.append(_instantiate_provider(name))
        except MockProviderInProductionError:
            raise
        except OCRProviderError:
            continue
    return providers


def get_confidence_threshold() -> float:
    return float(os.environ.get("OCR_CONFIDENCE_THRESHOLD", DEFAULT_CONFIDENCE_THRESHOLD))


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------

async def create_ingestion_job(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    created_by: uuid.UUID,
    source: str = "upload",
    target_agreement_type_key: str | None = None,
    metadata_: dict | None = None,
) -> IngestionJob:
    job = IngestionJob(
        organization_id=organization_id,
        created_by=created_by,
        source=source,
        status="queued",
        target_agreement_type_key=target_agreement_type_key,
        metadata_=metadata_,
    )
    db.add(job)
    await db.flush()
    return job


async def process_document(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    filename: str,
    content_ref: str,
    mime_type: str = "application/pdf",
    provider: str | None = None,
) -> OCRDocument:
    """Run OCR on one document and route to commit or HITL review.

    Provider chain: the primary engine is attempted first; if it raises
    (SDK/credential/network failures), the fallback cloud provider runs
    before the document is marked failed. A degraded scan then follows the
    normal human-in-the-loop path.
    """
    ocr_providers = get_ocr_provider_chain(provider)
    if not ocr_providers:
        raise OCRProviderError(
            "No usable OCR provider is configured "
            "(primary and fallback both failed to initialize)."
        )

    doc = OCRDocument(
        organization_id=organization_id,
        job_id=job_id,
        original_filename=filename,
        content_ref=content_ref,
        mime_type=mime_type,
        provider=ocr_providers[0].name,
        status="processing",
    )
    db.add(doc)
    await db.flush()

    try:
        from app.services import document_storage

        data = document_storage.load_blob(content_ref)
    except Exception as e:
        doc.status = "failed"
        doc.error_message = str(e)
        await db.flush()
        await _bump_job(db, job_id, failed=True)
        return doc

    result: OCRResult | None = None
    last_error: Exception | None = None
    for ocr_provider in ocr_providers:
        doc.provider = ocr_provider.name
        try:
            result = ocr_provider.extract_text(data, filename=filename)
            break
        except Exception as e:  # noqa: BLE001 - provider failures must fall through
            last_error = e
            continue

    if result is None:
        doc.status = "failed"
        doc.error_message = str(last_error) if last_error else "OCR failed"
        await db.flush()
        await _bump_job(db, job_id, failed=True)
        return doc

    doc.extracted_text = result.text
    doc.confidence = result.confidence
    threshold = get_confidence_threshold()

    if result.confidence >= threshold:
        doc.status = "committed"
        await _bump_job(db, job_id, completed=True)
        # Metadata extraction happens via the intelligence layer on demand.
        await _extract_metadata(db, doc)
    else:
        doc.status = "needs_review"
        review = HumanReviewTask(
            organization_id=organization_id,
            ocr_document_id=doc.id,
            status="pending",
            confidence=result.confidence,
        )
        db.add(review)
        await _bump_job(db, job_id, needs_review=True)

    await db.flush()
    return doc


async def _extract_metadata(db: AsyncSession, doc: OCRDocument) -> None:
    """Extract metadata from OCR text using the AI service.

    Passes the OCR context and confidence so the model can account for degradation.
    """
    from app.services.ai_service import AIService

    text = doc.extracted_text or ""
    if not text:
        return

    ai_service = AIService()
    
    # We pass the OCR confidence so the AI can adjust its tolerance
    result = await ai_service.analyze_contract(
        contract_text=text,
        agreement_type="commercial_contract",
        is_ocr=True,
        ocr_confidence=doc.confidence or 0.0,
    )

    if result.key_terms:
        doc.extracted_metadata = result.key_terms
        await db.flush()


async def _bump_job(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    completed: bool = False,
    failed: bool = False,
    needs_review: bool = False,
) -> None:
    result = await db.execute(
        select(IngestionJob).where(IngestionJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        return
    if completed:
        job.completed_files += 1
    if failed:
        job.failed_files += 1
    job.total_files += 1
    if job.status == "queued":
        job.status = "processing"
    await db.flush()


async def list_review_queue(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    status_filter: str | None = None,
    limit: int = 50,
) -> list[dict]:
    query = (
        select(HumanReviewTask)
        .where(HumanReviewTask.organization_id == organization_id)
        .order_by(HumanReviewTask.created_at.asc())
        .limit(limit)
    )
    if status_filter:
        query = query.where(HumanReviewTask.status == status_filter)
    tasks = (await db.execute(query)).scalars().all()
    out = []
    for t in tasks:
        doc_result = await db.execute(
            select(OCRDocument).where(OCRDocument.id == t.ocr_document_id)
        )
        doc = doc_result.scalar_one_or_none()
        out.append({
            "id": str(t.id),
            "ocr_document_id": str(t.ocr_document_id),
            "filename": doc.original_filename if doc else None,
            "confidence": t.confidence,
            "status": t.status,
            "assigned_to": str(t.assigned_to) if t.assigned_to else None,
            "corrected_text": t.corrected_text,
            "extracted_text_preview": (doc.extracted_text or "")[:300] if doc else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })
    return out


async def review_document(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    task_id: uuid.UUID,
    approved: bool,
    corrected_text: str | None = None,
    reviewed_by: uuid.UUID,
    notes: str | None = None,
) -> OCRDocument:
    """Human verdict on a degraded OCR document."""
    result = await db.execute(
        select(HumanReviewTask).where(
            HumanReviewTask.id == task_id,
            HumanReviewTask.organization_id == organization_id,
        )
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise ValueError("Review task not found")

    if not approved:
        task.status = "rejected"
        task.review_notes = notes
        task.reviewed_by = reviewed_by
        task.reviewed_at = datetime.now(timezone.utc)
        await db.flush()

        doc_result = await db.execute(
            select(OCRDocument).where(OCRDocument.id == task.ocr_document_id)
        )
        doc = doc_result.scalar_one_or_none()
        if doc:
            doc.status = "failed"
            doc.error_message = "Rejected in human review"
        await db.flush()
        return doc  # type: ignore[return-value]

    doc_result = await db.execute(
        select(OCRDocument).where(OCRDocument.id == task.ocr_document_id)
    )
    doc = doc_result.scalar_one_or_none()
    if doc is None:
        raise ValueError("OCR document not found")

    doc.extracted_text = corrected_text if corrected_text is not None else doc.extracted_text
    doc.confidence = 1.0  # Human verification is ground truth.
    doc.status = "committed"
    task.status = "approved"
    task.corrected_text = doc.extracted_text
    task.reviewed_by = reviewed_by
    task.reviewed_at = datetime.now(timezone.utc)
    await _extract_metadata(db, doc)
    await db.flush()
    return doc


async def get_job_summary(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
) -> dict:
    """Dashboard stats for the ingestion pipeline."""
    total = (
        await db.scalar(
            select(func.count(IngestionJob.id)).where(
                IngestionJob.organization_id == organization_id
            )
        )
    ) or 0
    pending_review = (
        await db.scalar(
            select(func.count(HumanReviewTask.id)).where(
                HumanReviewTask.organization_id == organization_id,
                HumanReviewTask.status.in_(["pending", "in_progress"]),
            )
        )
    ) or 0
    committed = (
        await db.scalar(
            select(func.count(OCRDocument.id)).where(
                OCRDocument.organization_id == organization_id,
                OCRDocument.status == "committed",
            )
        )
    ) or 0
    return {
        "jobs": total,
        "pending_review": pending_review,
        "committed_documents": committed,
    }