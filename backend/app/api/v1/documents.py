"""Documents API endpoints.

Handle final document generation, hashing, and storage.
"""

import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement, AgreementVersion
from app.models.audit import AuditEvent
from app.models.document import Document, DocumentType
from app.models.user import User
from app.services import document_storage
from app.services.audit_service import record_event
from app.services.agreement_versioning import get_latest_version, lock_version
from app.services.agreement_renderer import render_agreement
from app.services.document_repository import (
    ImmutableDocumentError,
    DocumentIngestError,
    agreement_evidence,
    agreement_timeline,
    archive_document,
    ensure_document_type,
    get_agreement_document,
    ingest_document,
    link_documents,
    list_agreement_documents,
    register_executed_artifact,
    repository_summary,
    seed_document_types,
    update_document_metadata,
)

router = APIRouter(
    prefix="/agreements",
    tags=["documents"],
)


class FinalDocumentResponse(BaseModel):
    document_hash: str
    version_number: int
    status: str
    message: str


async def _record_audit_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agreement_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    actor_type: str,
    action: str,
    metadata: dict | None = None,
):
    """Record an audit event."""
    return await record_event(
        db,
        tenant_id=tenant_id,
        agreement_id=agreement_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        resource_type="agreement",
        resource_id=agreement_id,
        metadata_json=metadata,
    )


@router.post(
    "/{agreement_id}/submit-review",
    status_code=status.HTTP_200_OK,
)
async def submit_for_review(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Submit agreement for internal review.

    Transitions DRAFT → INTERNAL_REVIEW.
    """
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    if agreement.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot submit for review in status: {agreement.status}",
        )

    from app.services.lifecycle_service import apply_transition, TransitionNotAllowed

    try:
        await apply_transition(
            db,
            agreement=agreement,
            action_key="submit",
            actor_id=current_user.id,
            org_id=org_id,
            actor_type="user",
            metadata_json={"source": "documents.submit_for_review"},
        )
    except TransitionNotAllowed as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    # Record audit event
    await _record_audit_event(
        db,
        tenant_id=org_id,
        agreement_id=agreement_id,
        actor_id=current_user.id,
        actor_type="user",
        action="SUBMITTED_FOR_REVIEW",
        metadata={"previous_status": "draft"},
    )

    await db.flush()

    return {"status": "internal_review", "message": "Agreement submitted for internal review"}


@router.post(
    "/{agreement_id}/documents/final",
    response_class=Response,
)
async def generate_final_document(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Generate final PDF, hash it, and lock the agreement.

    Only callable when agreement status is EXECUTED.
    The PDF is rendered, SHA-256 hashed, and the version is locked.
    """
    result = await db.execute(
        select(Agreement).where(
            Agreement.id == agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()

    if agreement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement not found",
        )

    if agreement.status != "executed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Can only generate final document for EXECUTED agreements, current status: {agreement.status}",
        )

    # Render the final PDF
    try:
        render_result = await render_agreement(
            db,
            agreement_id=agreement_id,
            generate_pdf=True,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Lock the version
    version = await get_latest_version(db, agreement_id)
    if version:
        await lock_version(db, version)

    # Persist the final document blob to object storage and record it in
    # the executed-agreement repository (spec 2.07 / 1.23).
    from app.services import document_storage
    from app.services.retention_service import store_repository_record

    if render_result.pdf:
        from app.models.execution import ExecutionPackage

        content_ref = document_storage.build_content_ref(
            org_id=org_id,
            agreement_id=agreement_id,
            doc_type="executed",
        )
        document_storage.store_blob(content_ref, render_result.pdf)
        await store_repository_record(
            db,
            org_id=org_id,
            agreement_id=agreement_id,
            version_id=version.id if version else None,
            document_type="executed",
            content_ref=content_ref,
            content_hash=render_result.content_hash,
            size_bytes=len(render_result.pdf),
            classification="confidential",
            is_executed=True,
            metadata_={"version_number": version.version_number if version else None},
        )
        # Register the immutable executed document in the repository (2.07).
        await seed_document_types(db)
        executed_doc = await register_executed_artifact(
            db,
            organization_id=org_id,
            agreement_id=agreement_id,
            content_ref=content_ref,
            content_hash=render_result.content_hash,
            size_bytes=len(render_result.pdf),
            version_number=version.version_number if version else None,
            actor_id=current_user.id,
        )

    # Record audit event
    await _record_audit_event(
        db,
        tenant_id=org_id,
        agreement_id=agreement_id,
        actor_id=current_user.id,
        actor_type="user",
        action="FINAL_DOCUMENT_GENERATED",
        metadata={
            "document_hash": render_result.content_hash,
            "version_number": version.version_number if version else None,
        },
    )

    await db.flush()

    if render_result.pdf:
        return Response(
            content=render_result.pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="final_agreement_{agreement_id}.pdf"',
                "X-Document-Hash": render_result.content_hash,
            },
        )

    return FinalDocumentResponse(
        document_hash=render_result.content_hash,
        version_number=version.version_number if version else 0,
        status="executed",
        message="Final document generated and locked",
    )


# --- Executed-agreement repository (spec 2.07.13-2.07.33) --------------------

class DocumentLinkRequest(BaseModel):
    source_document_id: uuid.UUID
    target_document_id: uuid.UUID
    relationship_type: str
    metadata: dict | None = None


class DocumentPatchRequest(BaseModel):
    title: str | None = None
    classification: str | None = None
    metadata: dict | None = None


@router.get("/{agreement_id}/repository")
async def get_repository(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Consolidated repository view for an agreement (spec 2.07.14)."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    await seed_document_types(db)
    return await repository_summary(
        db, organization_id=org_id, agreement_id=agreement_id
    )


@router.get("/{agreement_id}/documents")
async def get_agreement_documents(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List repository documents for an agreement (spec 2.07.24)."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    await seed_document_types(db)
    docs = await list_agreement_documents(
        db, organization_id=org_id, agreement_id=agreement_id
    )
    return {
        "documents": [
            {
                "id": str(d.id),
                "title": d.title,
                "filename": d.filename,
                "media_type": d.media_type,
                "sha256": d.sha256,
                "size_bytes": d.size_bytes,
                "classification": d.classification,
                "status": d.status,
                "immutable": d.immutable,
                "document_type": (
                    d.document_type.code if d.document_type else None
                ),
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ]
    }


@router.post("/{agreement_id}/documents")
async def upload_agreement_document(
    agreement_id: uuid.UUID,
    file: UploadFile = File(...),
    document_type_code: str = Form(...),
    title: str | None = Form(None),
    classification: str | None = Form(None),
    immutable: bool = Form(False),
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Upload a document into the repository (spec 2.07.9)."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    await seed_document_types(db)
    atype = await ensure_document_type(db, document_type_code)
    if not atype.active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Document type is deactivated: {document_type_code}",
        )
    data = await file.read()
    try:
        doc = await ingest_document(
            db,
            organization_id=org_id,
            agreement_id=agreement_id,
            document_type_id=atype.id,
            title=title or file.filename or "document",
            filename=file.filename or "document",
            media_type=file.content_type or "application/octet-stream",
            data=data,
            actor_id=current_user.id,
            classification=classification,
            immutable=immutable,
            metadata_={"document_type_code": document_type_code},
        )
    except DocumentIngestError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    await db.flush()
    return {
        "id": str(doc.id),
        "title": doc.title,
        "filename": doc.filename,
        "media_type": doc.media_type,
        "sha256": doc.sha256,
        "size_bytes": doc.size_bytes,
        "classification": doc.classification,
        "immutable": doc.immutable,
        "document_type": document_type_code,
    }


@router.post("/{agreement_id}/documents/link")
async def relate_documents(
    agreement_id: uuid.UUID,
    body: DocumentLinkRequest,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Link two documents with a typed relationship (spec 2.07.6)."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    source = await get_agreement_document(
        db,
        organization_id=org_id,
        agreement_id=agreement_id,
        document_id=body.source_document_id,
    )
    target = await get_agreement_document(
        db,
        organization_id=org_id,
        agreement_id=agreement_id,
        document_id=body.target_document_id,
    )
    if source is None or target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source or target document not found",
        )
    rel = await link_documents(
        db,
        source_document_id=source.id,
        target_document_id=target.id,
        relationship_type=body.relationship_type,
        metadata_=body.metadata,
    )
    await record_event(
        db,
        tenant_id=org_id,
        agreement_id=agreement_id,
        actor_id=current_user.id,
        actor_type="user",
        action="DOCUMENT_LINKED",
        resource_type="document",
        resource_id=source.id,
        metadata_json={
            "relationship_type": body.relationship_type,
            "target_document_id": str(target.id),
        },
    )
    await db.flush()
    return {"id": str(rel.id), "relationship_type": body.relationship_type}


@router.get("/{agreement_id}/documents/{document_id}")
async def get_agreement_document_detail(
    agreement_id: uuid.UUID,
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Document detail including integrity metadata (spec 2.07.16)."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    doc = await get_agreement_document(
        db,
        organization_id=org_id,
        agreement_id=agreement_id,
        document_id=document_id,
    )
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return {
        "id": str(doc.id),
        "title": doc.title,
        "filename": doc.filename,
        "media_type": doc.media_type,
        "sha256": doc.sha256,
        "size_bytes": doc.size_bytes,
        "classification": doc.classification,
        "status": doc.status,
        "immutable": doc.immutable,
        "document_type": doc.document_type.code if doc.document_type else None,
        "metadata": doc.metadata_,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "created_by": str(doc.created_by) if doc.created_by else None,
    }


@router.patch("/{agreement_id}/documents/{document_id}")
async def update_agreement_document(
    agreement_id: uuid.UUID,
    document_id: uuid.UUID,
    body: DocumentPatchRequest,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Metadata patch with immutable-document guard (spec 2.07.7)."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    doc = await get_agreement_document(
        db,
        organization_id=org_id,
        agreement_id=agreement_id,
        document_id=document_id,
    )
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    changes = {
        k: v
        for k, v in {
            "title": body.title,
            "classification": body.classification,
            "metadata_": body.metadata,
        }.items()
        if v is not None
    }
    try:
        doc = await update_document_metadata(
            db, doc, changes=changes, actor_id=current_user.id
        )
    except ImmutableDocumentError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    await db.flush()
    return {"id": str(doc.id), "title": doc.title, "status": "updated"}


@router.delete("/{agreement_id}/documents/{document_id}")
async def archive_agreement_document(
    agreement_id: uuid.UUID,
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Archive a document; immutable legal docs always return 409 (spec 2.07.7)."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    doc = await get_agreement_document(
        db,
        organization_id=org_id,
        agreement_id=agreement_id,
        document_id=document_id,
    )
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    try:
        doc = await archive_document(db, doc, actor_id=current_user.id)
    except ImmutableDocumentError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    await record_event(
        db,
        tenant_id=org_id,
        agreement_id=agreement_id,
        actor_id=current_user.id,
        actor_type="user",
        action="DOCUMENT_ARCHIVED",
        resource_type="document",
        resource_id=doc.id,
    )
    await db.flush()
    return {"id": str(doc.id), "status": "archived"}


@router.get("/{agreement_id}/documents/{document_id}/download")
async def download_agreement_document(
    agreement_id: uuid.UUID,
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Issue a short-lived signed download token (spec 2.07.17 / 1.22)."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    doc = await get_agreement_document(
        db,
        organization_id=org_id,
        agreement_id=agreement_id,
        document_id=document_id,
    )
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    if doc.status not in ("ACTIVE", "UNDER_LEGAL_HOLD"):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Document is no longer available",
        )
    token = document_storage.create_download_token(
        content_ref=doc.storage_key,
        user_id=current_user.id,
        org_id=org_id,
    )
    return {
        "document_id": str(doc.id),
        "download_url": f"/api/v1/repository/download?token={token}",
        "expires_in_seconds": document_storage.DOWNLOAD_TOKEN_TTL,
    }


@router.get("/{agreement_id}/timeline")
async def get_agreement_timeline(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Full activity timeline for an agreement (spec 2.07.31-2.07.32)."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    events = await agreement_timeline(
        db, organization_id=org_id, agreement_id=agreement_id
    )
    return {"agreement_id": str(agreement_id), "events": events}


@router.get("/{agreement_id}/evidence")
async def get_agreement_evidence(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Evidence aggregation for an agreement (spec 2.07.18 / 2.06)."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    await seed_document_types(db)
    evidence = await agreement_evidence(
        db, organization_id=org_id, agreement_id=agreement_id
    )
    return {"agreement_id": str(agreement_id), **evidence}


@router.get("/{agreement_id}/repository/search")
async def repository_search(
    agreement_id: uuid.UUID,
    q: str = "",
    document_type_code: str | None = None,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Search repository documents for an agreement (spec 2.07.29)."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    query = select(Document).where(
        Document.organization_id == org_id,
        Document.agreement_id == agreement_id,
        Document.status.in_(("ACTIVE", "UNDER_LEGAL_HOLD")),
    )
    if q:
        query = query.where(
            Document.title.ilike(f"%{q}%") | Document.filename.ilike(f"%{q}%")
        )
    if document_type_code:
        result = await db.execute(
            select(DocumentType.id).where(DocumentType.code == document_type_code)
        )
        atype_id = result.scalar_one_or_none()
        if atype_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown document type: {document_type_code}",
            )
        query = query.where(Document.document_type_id == atype_id)
    result = await db.execute(query.limit(100))
    docs = list(result.scalars().all())
    return {
        "documents": [
            {
                "id": str(d.id),
                "title": d.title,
                "classification": d.classification,
                "status": d.status,
                "document_type": (
                    d.document_type.code if d.document_type else None
                ),
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ]
    }
