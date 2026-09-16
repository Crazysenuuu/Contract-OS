"""Data privacy API (spec 24.3): crypto shredding, redaction, erasure."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement
from app.models.privacy import (
    ErasureRequest,
    RedactionRequest,
)
from app.models.user import User
from app.services.privacy_service import (
    PrivacyError,
    complete_redaction,
    create_erasure_request,
    create_redaction_request,
    encrypt_agreement_pii,
    execute_erasure,
    list_encrypted_fields,
    redact_text,
    shred_field,
)

router = APIRouter(prefix="/privacy", tags=["Data Privacy"])


class EncryptRequest(BaseModel):
    encrypt_all_fields: bool = False


class ShredRequest(BaseModel):
    field_path: str = Field(min_length=1)


class RedactionCreate(BaseModel):
    targets: dict
    reason: str = Field(min_length=1)
    document_type: str = "executed"


class RedactionComplete(BaseModel):
    redacted_content: str


class ErasureCreate(BaseModel):
    data_subject: str = Field(min_length=1)
    agreement_id: uuid.UUID | None = None
    regulation: str = "gdpr"


class ErasureExecute(BaseModel):
    field_paths: list[str] | None = None
    agreement_id: uuid.UUID | None = None
    preserved_notes: str | None = None


async def _get_agreement_or_404(
    db: AsyncSession, agreement_id: uuid.UUID, org_id: uuid.UUID
) -> Agreement:
    result = await db.execute(
        select(Agreement).where(
            Agreement.id == agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()
    if agreement is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    return agreement


@router.post("/agreements/{agreement_id}/encrypt", status_code=status.HTTP_200_OK)
async def encrypt_agreement_fields(
    agreement_id: uuid.UUID,
    data: EncryptRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Encrypt PII-looking fields of an agreement's data in place."""
    agreement = await _get_agreement_or_404(db, agreement_id, org_id)
    agreement.data = await encrypt_agreement_pii(
        db,
        organization_id=org_id,
        agreement_id=agreement_id,
        data=dict(agreement.data or {}),
        encrypt_all_fields=data.encrypt_all_fields,
    )
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(agreement, "data")
    await db.commit()
    return {"status": "encrypted", "data": agreement.data}


@router.get("/agreements/{agreement_id}/fields")
async def encrypted_fields(
    agreement_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_agreement_or_404(db, agreement_id, org_id)
    return await list_encrypted_fields(db, organization_id=org_id, agreement_id=agreement_id)


@router.post("/agreements/{agreement_id}/shred", status_code=status.HTTP_200_OK)
async def shred_field_endpoint(
    agreement_id: uuid.UUID,
    data: ShredRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cryptographically shred a single field (delete its key envelope)."""
    await _get_agreement_or_404(db, agreement_id, org_id)
    try:
        record = await shred_field(
            db,
            organization_id=org_id,
            agreement_id=agreement_id,
            field_path=data.field_path,
        )
    except PrivacyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return {
        "field_path": record.field_path,
        "shredded": record.shredded,
        "shredded_at": record.shredded_at.isoformat() if record.shredded_at else None,
    }


@router.post("/redactions", status_code=status.HTTP_201_CREATED)
async def create_redaction(
    agreement_id: uuid.UUID,
    data: RedactionCreate,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_agreement_or_404(db, agreement_id, org_id)
    req = await create_redaction_request(
        db,
        organization_id=org_id,
        agreement_id=agreement_id,
        targets=data.targets,
        reason=data.reason,
        requested_by=current_user.id,
        document_type=data.document_type,
    )
    await db.commit()
    return {"id": str(req.id), "status": req.status}


@router.post("/redactions/{request_id}/complete")
async def complete_redaction_endpoint(
    request_id: uuid.UUID,
    data: RedactionComplete,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Store the redacted document and mark the request completed."""
    try:
        req = await complete_redaction(
            db,
            organization_id=org_id,
            request_id=request_id,
            redacted_content=data.redacted_content,
            processed_by=current_user.id,
        )
    except PrivacyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await db.commit()
    return {"id": str(req.id), "status": req.status, "content_ref": req.redacted_content_ref}


@router.post("/erasure-requests", status_code=status.HTTP_201_CREATED)
async def create_erasure(
    data: ErasureCreate,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if data.agreement_id is not None:
        await _get_agreement_or_404(db, data.agreement_id, org_id)
    req = await create_erasure_request(
        db,
        organization_id=org_id,
        agreement_id=data.agreement_id,
        data_subject=data.data_subject,
        regulation=data.regulation,
        requested_by=current_user.id,
    )
    await db.commit()
    return {"id": str(req.id), "status": req.status}


@router.post("/erasure-requests/{request_id}/execute")
async def execute_erasure_endpoint(
    request_id: uuid.UUID,
    data: ErasureExecute,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute the erasure: cryptographically shred the targeted PII."""
    try:
        req = await execute_erasure(
            db,
            organization_id=org_id,
            request_id=request_id,
            field_paths=data.field_paths,
            agreement_id=data.agreement_id,
            preserved_notes=data.preserved_notes,
        )
    except PrivacyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return {
        "id": str(req.id),
        "status": req.status,
        "shredded_fields": req.shredded_fields,
        "preserved_notes": req.preserved_notes,
    }


@router.get("/erasure-requests")
async def list_erasures(
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ErasureRequest)
        .where(ErasureRequest.organization_id == org_id)
        .order_by(ErasureRequest.created_at.desc())
        .limit(50)
    )
    return [
        {
            "id": str(r.id),
            "data_subject": r.data_subject,
            "regulation": r.regulation,
            "status": r.status,
            "agreement_id": str(r.agreement_id) if r.agreement_id else None,
            "shredded_fields": r.shredded_fields,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in result.scalars().all()
    ]