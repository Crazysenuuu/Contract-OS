"""Retention, legal hold, repository, and secure document download API.

Covers spec 1.22 (retention engine, legal hold, watermarking, secure
object-storage access) and 2.07 (executed-agreement repository).
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement
from app.models.retention import (
    LegalHold,
    RepositoryRecord,
    RetentionPolicy,
    RetentionRecord,
)
from app.models.user import User
from app.services import document_storage
from app.services.retention_service import (
    apply_policy_to_agreement,
    list_active_holds,
    place_legal_hold,
    release_legal_hold,
    run_retention_worker,
    store_repository_record,
)

router = APIRouter(prefix="/retention", tags=["Retention & Legal Hold"])
repository_router = APIRouter(prefix="/repository", tags=["Document Repository"])


# --- Retention policies -----------------------------------------------------

class RetentionPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    scope: str = "all"  # 'all' | 'agreement_type'
    agreement_type_key: str | None = None
    retention_months: int = Field(default=84, ge=1)
    disposition: str = "archive"  # 'archive' | 'delete'
    is_active: bool = True


class RetentionPolicyUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    retention_months: int | None = None
    disposition: str | None = None
    is_active: bool | None = None


@router.get("/policies")
async def list_policies(
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RetentionPolicy)
        .where(RetentionPolicy.organization_id == org_id)
        .order_by(RetentionPolicy.created_at.desc())
    )
    return result.scalars().all()


@router.post("/policies", status_code=status.HTTP_201_CREATED)
async def create_policy(
    data: RetentionPolicyCreate,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if data.scope not in {"all", "agreement_type"}:
        raise HTTPException(status_code=422, detail="scope must be 'all' or 'agreement_type'")
    if data.disposition not in {"archive", "delete"}:
        raise HTTPException(status_code=422, detail="disposition must be 'archive' or 'delete'")
    if data.scope == "agreement_type" and not data.agreement_type_key:
        raise HTTPException(status_code=422, detail="agreement_type_key required for scope='agreement_type'")

    policy = RetentionPolicy(
        organization_id=org_id,
        name=data.name,
        description=data.description,
        scope=data.scope,
        agreement_type_key=data.agreement_type_key,
        retention_months=data.retention_months,
        disposition=data.disposition,
        is_active=data.is_active,
        created_by=current_user.id,
    )
    db.add(policy)
    await db.flush()

    # Apply to existing agreements so records exist immediately.
    result = await db.execute(
        select(Agreement).where(Agreement.organization_id == org_id)
    )
    for agreement in result.scalars().all():
        await apply_policy_to_agreement(db, org_id=org_id, agreement=agreement)

    await db.commit()
    return policy


@router.patch("/policies/{policy_id}")
async def update_policy(
    policy_id: uuid.UUID,
    data: RetentionPolicyUpdate,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RetentionPolicy).where(
            RetentionPolicy.id == policy_id,
            RetentionPolicy.organization_id == org_id,
        )
    )
    policy = result.scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(policy, field, value)
    await db.commit()
    return policy


@router.get("/records")
async def list_retention_records(
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RetentionRecord)
        .where(RetentionRecord.organization_id == org_id)
        .order_by(RetentionRecord.retention_until)
    )
    return result.scalars().all()


# --- Legal holds -------------------------------------------------------------

class LegalHoldCreate(BaseModel):
    agreement_id: uuid.UUID | None = None
    reason: str = Field(min_length=1)
    hold_type: str = "manual"


@router.get("/holds")
async def list_holds(
    agreement_id: uuid.UUID | None = None,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_active_holds(db, org_id=org_id, agreement_id=agreement_id)


@router.post("/holds", status_code=status.HTTP_201_CREATED)
async def create_hold(
    data: LegalHoldCreate,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if data.agreement_id is not None:
        result = await db.execute(
            select(Agreement).where(
                Agreement.id == data.agreement_id,
                Agreement.organization_id == org_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Agreement not found")

    hold = await place_legal_hold(
        db,
        org_id=org_id,
        agreement_id=data.agreement_id,
        reason=data.reason,
        hold_type=data.hold_type,
        placed_by=current_user.id,
    )
    await db.commit()
    return hold


@router.post("/holds/{hold_id}/release")
async def release_hold(
    hold_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        hold = await release_legal_hold(db, hold_id=hold_id, released_by=current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await db.commit()
    return hold


# --- Retention worker ---------------------------------------------------------

@router.post("/worker/run")
async def run_worker(
    dry_run: bool = True,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run the retention worker for this org (dry_run=True reports only)."""
    result = await run_retention_worker(db, org_id=org_id, dry_run=dry_run)
    if not dry_run:
        await db.commit()
    return result


# --- Secure download -----------------------------------------------------------

@repository_router.get("/{agreement_id}/documents")
async def list_repository_documents(
    agreement_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List stored repository artifacts for an agreement."""
    result = await db.execute(
        select(RepositoryRecord)
        .where(
            RepositoryRecord.organization_id == org_id,
            RepositoryRecord.agreement_id == agreement_id,
        )
        .order_by(RepositoryRecord.created_at.desc())
    )
    records = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "document_type": r.document_type,
            "content_hash": r.content_hash,
            "mime_type": r.mime_type,
            "size_bytes": r.size_bytes,
            "classification": r.classification,
            "is_executed": r.is_executed,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]


@repository_router.post("/{agreement_id}/store")
async def store_document(
    agreement_id: uuid.UUID,
    document_type: str = Query(...),
    classification: str = "internal",
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an empty repository placeholder (metadata-only until upload).

    Returns a secure download token so the caller can PUT bytes; for PDF
    documents generated by the platform use /agreements/{id}/documents/final
    which stores the blob automatically.
    """
    result = await db.execute(
        select(Agreement).where(
            Agreement.id == agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()
    if agreement is None:
        raise HTTPException(status_code=404, detail="Agreement not found")

    content_ref = document_storage.build_content_ref(
        org_id=org_id, agreement_id=agreement_id, doc_type=document_type
    )
    record = await store_repository_record(
        db,
        org_id=org_id,
        agreement_id=agreement_id,
        version_id=None,
        document_type=document_type,
        content_ref=content_ref,
        content_hash="",
        classification=classification,
    )
    await db.commit()
    return {
        "id": str(record.id),
        "content_ref": content_ref,
        "upload_token": document_storage.create_download_token(
            content_ref=content_ref, user_id=current_user.id, org_id=org_id
        ),
    }


@repository_router.get("/download")
async def download_document(
    token: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Securely download a stored document via a signed short-lived token."""
    # Mass-download protection (spec 1.22.32): sliding-window per-user limit.
    from app.services.document_security import get_download_guard

    get_download_guard().check(str(current_user.id))

    payload = document_storage.verify_download_token(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or expired download token")
    if payload["user_id"] != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Download token belongs to another user")

    try:
        data = document_storage.load_blob(payload["content_ref"])
    except document_storage.StorageError as e:
        raise HTTPException(status_code=404, detail=str(e))

    result = await db.execute(
        select(RepositoryRecord).where(
            RepositoryRecord.content_ref == payload["content_ref"],
            RepositoryRecord.organization_id == payload["org_id"],
        )
    )
    record = result.scalar_one_or_none()

    return Response(
        content=data,
        media_type=record.mime_type if record else "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="document.pdf"',
            "X-Content-Hash": record.content_hash if record else "",
        },
    )