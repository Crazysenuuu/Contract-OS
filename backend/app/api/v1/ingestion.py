"""Legacy contract ingestion & OCR API (spec 24.1)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.ingestion import IngestionJob, OCRDocument, HumanReviewTask
from app.models.user import User
from app.services import document_storage
from app.services.ocr_service import (
    create_ingestion_job,
    get_job_summary,
    list_review_queue,
    process_document,
    review_document,
)

router = APIRouter(prefix="/ingestion", tags=["Contract Ingestion"])


class ReviewDecision(BaseModel):
    approved: bool
    corrected_text: str | None = None
    notes: str | None = None


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
async def start_job(
    source: str = "upload",
    target_agreement_type_key: str | None = None,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an ingestion job (metadata only; documents uploaded after)."""
    job = await create_ingestion_job(
        db,
        organization_id=org_id,
        created_by=current_user.id,
        source=source,
        target_agreement_type_key=target_agreement_type_key,
    )
    await db.commit()
    return {"id": str(job.id), "status": job.status}


@router.post("/jobs/{job_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    job_id: uuid.UUID,
    file: UploadFile = File(...),
    provider: str | None = Query(None),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a scanned document, store it, and run OCR on it."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="Empty file")

    content_ref = document_storage.build_content_ref(
        org_id=org_id,
        agreement_id=job_id,  # Group under the job for traceability.
        doc_type="ingestion",
        suffix="pdf",
    )
    document_storage.store_blob(content_ref, data)

    doc = await process_document(
        db,
        organization_id=org_id,
        job_id=job_id,
        filename=file.filename or "document.pdf",
        content_ref=content_ref,
        mime_type=file.content_type or "application/pdf",
        provider=provider,
    )
    await db.commit()

    return {
        "id": str(doc.id),
        "status": doc.status,
        "confidence": doc.confidence,
        "provider": doc.provider,
        "needs_review": doc.status == "needs_review",
        "extracted_metadata": doc.extracted_metadata,
    }


@router.get("/jobs")
async def list_jobs(
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select

    result = await db.execute(
        select(IngestionJob)
        .where(IngestionJob.organization_id == org_id)
        .order_by(IngestionJob.created_at.desc())
        .limit(50)
    )
    jobs = result.scalars().all()
    return [
        {
            "id": str(j.id),
            "status": j.status,
            "source": j.source,
            "total_files": j.total_files,
            "completed_files": j.completed_files,
            "failed_files": j.failed_files,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        for j in jobs
    ]


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select

    job_result = await db.execute(
        select(IngestionJob).where(
            IngestionJob.id == job_id,
            IngestionJob.organization_id == org_id,
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    docs_result = await db.execute(
        select(OCRDocument).where(OCRDocument.job_id == job_id)
    )
    docs = docs_result.scalars().all()

    return {
        "id": str(job.id),
        "status": job.status,
        "source": job.source,
        "target_agreement_type_key": job.target_agreement_type_key,
        "total_files": job.total_files,
        "completed_files": job.completed_files,
        "failed_files": job.failed_files,
        "documents": [
            {
                "id": str(d.id),
                "filename": d.original_filename,
                "status": d.status,
                "confidence": d.confidence,
                "provider": d.provider,
                "agreement_id": str(d.agreement_id) if d.agreement_id else None,
                "extracted_metadata": d.extracted_metadata,
                "error_message": d.error_message,
            }
            for d in docs
        ],
    }


@router.get("/review-queue")
async def review_queue(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, le=200),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the human-in-the-loop OCR review queue."""
    return await list_review_queue(
        db, organization_id=org_id, status_filter=status_filter, limit=limit
    )


@router.post("/review-queue/{task_id}/decision")
async def review_decision(
    task_id: uuid.UUID,
    data: ReviewDecision,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve or reject a degraded OCR document."""
    try:
        doc = await review_document(
            db,
            organization_id=org_id,
            task_id=task_id,
            approved=data.approved,
            corrected_text=data.corrected_text,
            reviewed_by=current_user.id,
            notes=data.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await db.commit()
    return {
        "ocr_document_id": str(doc.id) if doc else None,
        "status": doc.status if doc else "failed",
        "approved": data.approved,
    }


@router.get("/summary")
async def ingestion_summary(
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_job_summary(db, organization_id=org_id)


@router.get("/documents/{doc_id}/pdf")
async def get_document_pdf(
    doc_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from fastapi.responses import Response

    result = await db.execute(
        select(OCRDocument).where(
            OCRDocument.id == doc_id,
            OCRDocument.organization_id == org_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        data = document_storage.load_blob(doc.content_ref)
        return Response(content=data, media_type=doc.mime_type or "application/pdf")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load document: {e}")