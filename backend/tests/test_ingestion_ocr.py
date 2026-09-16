"""Tests for OCR / legacy contract ingestion pipeline (spec 24.1)."""

import uuid

import pytest
from sqlalchemy import select

from app.models.ingestion import HumanReviewTask, IngestionJob, OCRDocument
from app.services import document_storage
from app.services.ai_service import AIService, AnalysisResult
from app.services.ocr_service import (
    create_ingestion_job,
    get_confidence_threshold,
    list_review_queue,
    process_document,
    review_document,
)


def _mock_ai(monkeypatch: pytest.MonkeyPatch, effective_date: str) -> None:
    """Stub the LLM boundary: the pipeline must store whatever metadata the
    AI layer returns, without tests depending on live credentials."""

    async def fake_analyze(self, contract_text, agreement_type="mutual_nda",
                           is_ocr=False, ocr_confidence=1.0):
        return AnalysisResult(
            summary="stub",
            key_terms={
                "effective_date": effective_date,
                "parties": ["Acme Corp", "Globex Inc"],
            },
            model_used="stub",
        )

    monkeypatch.setattr(AIService, "analyze_contract", fake_analyze)


def _seed_blob(text: str, *, pad: bool = False) -> str:
    content_ref = f"test-blob-{uuid.uuid4().hex}.pdf"
    data = text.encode("utf-8")
    if pad:
        # Mock provider confidence = len(data)/5000; pad to exceed threshold.
        data = data + b" " * 8000
    document_storage.store_blob(content_ref, data)
    return content_ref


@pytest.mark.asyncio
async def test_create_ingestion_job(db_session, test_org, test_user):
    job = await create_ingestion_job(
        db_session,
        organization_id=test_org.id,
        created_by=test_user.id,
        source="bulk_import",
        target_agreement_type_key="nda",
    )
    assert job.status == "queued"
    assert job.organization_id == test_org.id
    assert job.total_files == 0


def test_malware_rejection_persisted(monkeypatch):
    """Spec 1.22 §26: an infected upload must be marked failed so it does
    not sit in the queue forever, and OCR must not be dispatched for it."""
    from unittest.mock import MagicMock

    import app.core.database as db_mod
    import app.tasks.ingestion as ingestion_task
    from app.services.virus_scan_service import ScanResult

    recorded = {}

    class FakeSession:
        def __init__(self):
            self.executed = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            self.executed.append(stmt)

        async def commit(self):
            recorded["committed"] = True

    fake_session = FakeSession()

    class FakeSessionLocal:
        def __call__(self):
            return fake_session

    fake_ocr = MagicMock()
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", FakeSessionLocal())
    monkeypatch.setattr(ingestion_task, "process_ocr_document", fake_ocr)
    monkeypatch.setattr(
        "app.services.virus_scan_service.scan_bytes",
        lambda raw: ScanResult(
            infected=True, threat="EICAR-Test-File", backend="mock"
        ),
    )

    # Invoke the underlying function, bypassing Celery task machinery.
    ingestion_task.process_bulk_ingestion.__wrapped__(
        organization_id="00000000-0000-0000-0000-000000000001",
        job_id="00000000-0000-0000-0000-000000000002",
        document_refs=[
            {
                "filename": "evil.pdf",
                "content_ref": "org1/job2/evil.pdf",
                "content_bytes": b"infected-bytes",
            }
        ],
    )

    # OCR must never be dispatched for the infected file.
    fake_ocr.delay.assert_not_called()
    # The rejection was committed against ocr_documents.
    assert recorded.get("committed") is True
    assert fake_session.executed, "expected an UPDATE for the rejected document"


@pytest.mark.asyncio
async def test_process_high_confidence_document_commits(db_session, test_org, test_user, monkeypatch):
    _mock_ai(monkeypatch, "2026-01-01")
    job = await create_ingestion_job(
        db_session,
        organization_id=test_org.id,
        created_by=test_user.id,
    )
    content_ref = _seed_blob(
        "NON-DISCLOSURE AGREEMENT\n"
        "Effective Date: 2026-03-01\n"
        "Termination: 24 months after effective date\n"
        "Renewal: automatic annual renewal\n"
        "Confidentiality: standard mutual obligations\n",
        pad=True,
    )
    doc = await process_document(
        db_session,
        organization_id=test_org.id,
        job_id=job.id,
        filename="legacy-nda.pdf",
        content_ref=content_ref,
        provider="mock",
    )
    assert doc.status == "committed"
    assert doc.confidence >= get_confidence_threshold()
    assert doc.extracted_text and "legacy-nda.pdf" in doc.extracted_text
    # Metadata extraction populates effective date etc. (mock emits 2026-01-01).
    assert doc.extracted_metadata and doc.extracted_metadata.get("effective_date") == "2026-01-01"

    await db_session.refresh(job)
    assert job.completed_files == 1
    assert job.status == "processing"


@pytest.mark.asyncio
async def test_low_confidence_document_routes_to_hitl(db_session, test_org, test_user):
    job = await create_ingestion_job(
        db_session,
        organization_id=test_org.id,
        created_by=test_user.id,
    )
    content_ref = _seed_blob("garbled low quality scan ")  # tiny -> low confidence
    doc = await process_document(
        db_session,
        organization_id=test_org.id,
        job_id=job.id,
        filename="scanned.pdf",
        content_ref=content_ref,
        provider="mock",
    )
    assert doc.status == "needs_review"

    queue = await list_review_queue(db_session, organization_id=test_org.id)
    assert len(queue) == 1
    assert queue[0]["ocr_document_id"] == str(doc.id)
    assert queue[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_human_review_approve_commits_document(db_session, test_org, test_user, monkeypatch):
    _mock_ai(monkeypatch, "2026-06-01")
    job = await create_ingestion_job(
        db_session,
        organization_id=test_org.id,
        created_by=test_user.id,
    )
    content_ref = _seed_blob("noise")  # tiny -> low confidence
    doc = await process_document(
        db_session,
        organization_id=test_org.id,
        job_id=job.id,
        filename="bad-scan.pdf",
        content_ref=content_ref,
        provider="mock",
    )
    assert doc.status == "needs_review"

    queue = await list_review_queue(db_session, organization_id=test_org.id)
    review = queue[0]
    corrected = "CONFIDENTIALITY AGREEMENT between Acme and Globex. Effective 2026-06-01."
    await review_document(
        db_session,
        organization_id=test_org.id,
        task_id=uuid.UUID(review["id"]),
        approved=True,
        corrected_text=corrected,
        reviewed_by=test_user.id,
        notes="Fixed garbled text",
    )

    await db_session.refresh(doc)
    assert doc.status == "committed"
    assert doc.extracted_text == corrected
    assert doc.extracted_metadata.get("effective_date") == "2026-06-01"


@pytest.mark.asyncio
async def test_failed_blob_marks_document_failed(db_session, test_org, test_user):
    job = await create_ingestion_job(
        db_session,
        organization_id=test_org.id,
        created_by=test_user.id,
    )
    doc = await process_document(
        db_session,
        organization_id=test_org.id,
        job_id=job.id,
        filename="missing.pdf",
        content_ref="does-not-exist-12345.pdf",
        provider="mock",
    )
    assert doc.status == "failed"
    assert doc.error_message
    await db_session.refresh(job)
    assert job.failed_files == 1


@pytest.mark.asyncio
async def test_no_llm_configured_leaves_metadata_empty_but_commits(
    db_session, test_org, test_user, monkeypatch
):
    """Without LLM credentials the AI layer returns an honest empty analysis
    (analysis_status: not_analyzed). The pipeline must still commit the OCR
    text — metadata enrichment is simply absent, never fabricated."""

    async def honest_empty(self, contract_text, agreement_type="mutual_nda",
                           is_ocr=False, ocr_confidence=1.0):
        return AnalysisResult(key_terms={}, model_used="none")

    monkeypatch.setattr(AIService, "analyze_contract", honest_empty)
    job = await create_ingestion_job(
        db_session,
        organization_id=test_org.id,
        created_by=test_user.id,
    )
    content_ref = _seed_blob("clean scan text ", pad=True)
    doc = await process_document(
        db_session,
        organization_id=test_org.id,
        job_id=job.id,
        filename="no-llm.pdf",
        content_ref=content_ref,
        provider="mock",
    )
    assert doc.status == "committed"
    assert not doc.extracted_metadata  # absent, not invented

# ---------------------------------------------------------------------------
# Provider chain: implicit AWS->Google default, failover, production guard
# ---------------------------------------------------------------------------

from app.services.ocr_service import (  # noqa: E402
    AwsTextractProvider,
    GoogleDocumentAiProvider,
    MockProviderInProductionError,
    OCRProviderError,
    OCRResult,
    get_ocr_provider,
    get_ocr_provider_chain,
)


def test_implicit_default_chain_is_cloud_not_mock(monkeypatch):
    """No configuration at all must yield the AWS Textract -> Google Doc AI
    chain; the mock engine is never an implicit default."""
    from app.core.config import get_settings_lazy

    s = get_settings_lazy()
    monkeypatch.setattr(s, "ocr_provider", "")
    monkeypatch.setattr(s, "ocr_fallback_provider", "google_document_ai")

    chain = [p.name for p in get_ocr_provider_chain()]
    assert chain == ["aws_textract", "google_document_ai"]


def test_env_override_keeps_cloud_fallback(monkeypatch):
    """OCR_PROVIDER env picks the primary; the configured fallback still
    applies (deduped) so a broken primary never blocks ingestion."""
    from app.core.config import get_settings_lazy

    s = get_settings_lazy()
    monkeypatch.setattr(s, "ocr_provider", "aws_textract")
    monkeypatch.setattr(s, "ocr_fallback_provider", "google_document_ai")
    assert [p.name for p in get_ocr_provider_chain()] == [
        "aws_textract",
        "google_document_ai",
    ]

    # Same provider for both: no duplicate attempts.
    monkeypatch.setattr(s, "ocr_provider", "google_document_ai")
    monkeypatch.setattr(s, "ocr_fallback_provider", "google_document_ai")
    assert [p.name for p in get_ocr_provider_chain()] == ["google_document_ai"]


def test_explicit_mock_allowed_outside_production(monkeypatch):
    from app.core.config import get_settings_lazy

    s = get_settings_lazy()
    monkeypatch.setattr(s, "environment", "development")
    assert [p.name for p in get_ocr_provider_chain("mock")] == ["mock"]


def test_explicit_mock_refused_in_production(monkeypatch):
    from app.core.config import get_settings_lazy

    s = get_settings_lazy()
    monkeypatch.setattr(s, "environment", "production")
    with pytest.raises(MockProviderInProductionError):
        get_ocr_provider_chain("mock")
    with pytest.raises(MockProviderInProductionError):
        get_ocr_provider("mock")  # single-provider path guards too


@pytest.mark.asyncio
async def test_primary_failure_falls_back_to_secondary(
    db_session, test_org, test_user, monkeypatch
):
    """Textract outage must not fail the document: the Doc AI fallback runs
    and the committed record names the provider that actually produced text."""

    def aws_down(self, data, *, filename):
        raise OCRProviderError("textract unavailable")

    def google_ok(self, data, *, filename):
        return OCRResult(
            text="fallback extraction", confidence=0.92,
            provider="google_document_ai",
        )

    monkeypatch.setattr(AwsTextractProvider, "extract_text", aws_down)
    monkeypatch.setattr(GoogleDocumentAiProvider, "extract_text", google_ok)

    job = await create_ingestion_job(
        db_session, organization_id=test_org.id, created_by=test_user.id,
    )
    doc = await process_document(
        db_session,
        organization_id=test_org.id,
        job_id=job.id,
        filename="failover.pdf",
        content_ref=_seed_blob("anything", pad=True),
        provider=None,  # implicit chain: aws -> google
    )
    assert doc.provider == "google_document_ai"
    assert doc.status == "committed"
    assert doc.extracted_text == "fallback extraction"


@pytest.mark.asyncio
async def test_chain_exhaustion_marks_document_failed(
    db_session, test_org, test_user, monkeypatch
):
    """Every provider in the chain failing is a real failure — the document
    records the last error and the job counts it, it is not silently lost."""

    def down(self, data, *, filename):
        raise OCRProviderError("provider outage")

    monkeypatch.setattr(AwsTextractProvider, "extract_text", down)
    monkeypatch.setattr(GoogleDocumentAiProvider, "extract_text", down)

    job = await create_ingestion_job(
        db_session, organization_id=test_org.id, created_by=test_user.id,
    )
    doc = await process_document(
        db_session,
        organization_id=test_org.id,
        job_id=job.id,
        filename="doomed.pdf",
        content_ref=_seed_blob("anything"),
        provider=None,
    )
    assert doc.status == "failed"
    assert "provider outage" in (doc.error_message or "")
    await db_session.refresh(job)
    assert job.failed_files == 1
