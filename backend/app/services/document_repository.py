"""Executed-agreement repository service (spec 2.07).

Provides document metadata CRUD, secure ingest, immutable-document
protection, repository summaries, evidence/timeline aggregation, and the
default document-type catalog. Bytes always live in object storage; the
database only holds metadata + integrity hashes.
"""

import hashlib
import io
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.document import (
    Document,
    DocumentClassification,
    DocumentRelationship,
    DocumentStatus,
    DocumentType,
)
from app.models.execution import ExecutionPackage
from app.models.retention import (
    LegalHold,
    RepositoryRecord,
    RetentionRecord,
)
from app.services import document_storage
from app.services.audit_service import record_event

DEFAULT_DOCUMENT_TYPES = [
    {
        "code": "executed",
        "name": "Executed Agreement",
        "configuration": {
            "classification": DocumentClassification.LEGAL_RECORD,
            "immutable_by_default": True,
        },
    },
    {
        "code": "execution_package",
        "name": "Execution Package",
        "configuration": {
            "classification": DocumentClassification.EXECUTION_EVIDENCE,
        },
    },
    {
        "code": "signature_evidence",
        "name": "Signature Evidence",
        "configuration": {
            "classification": DocumentClassification.EXECUTION_EVIDENCE,
        },
    },
    {
        "code": "schedule",
        "name": "Schedule",
        "configuration": {
            "classification": DocumentClassification.SUPPORTING_DOCUMENT,
        },
    },
    {
        "code": "annex",
        "name": "Annex",
        "configuration": {
            "classification": DocumentClassification.SUPPORTING_DOCUMENT,
        },
    },
    {
        "code": "amendment",
        "name": "Amendment",
        "configuration": {
            "classification": DocumentClassification.LEGAL_RECORD,
            "immutable_by_default": True,
        },
    },
    {
        "code": "termination_notice",
        "name": "Termination Notice",
        "configuration": {
            "classification": DocumentClassification.SUPPORTING_DOCUMENT,
        },
    },
    {
        "code": "settlement",
        "name": "Settlement Document",
        "configuration": {
            "classification": DocumentClassification.LEGAL_RECORD,
        },
    },
    {
        "code": "obligation_evidence",
        "name": "Obligation Evidence",
        "configuration": {
            "classification": DocumentClassification.SUPPORTING_DOCUMENT,
        },
    },
    {
        "code": "certificate",
        "name": "Certificate",
        "configuration": {
            "classification": DocumentClassification.SUPPORTING_DOCUMENT,
        },
    },
]

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB

# Allowed media types: PDFs, Office docs, text, images for evidence.
ALLOWED_MEDIA_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/csv",
    "image/png",
    "image/jpeg",
    "application/json",
}

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\- ]+")


def secure_filename(filename: str, *, max_len: int = 200) -> str:
    """Sanitize a user-supplied filename (never used in storage paths)."""
    cleaned = _SAFE_FILENAME_RE.sub("_", filename or "document")
    cleaned = cleaned.strip().strip(".")
    if not cleaned:
        cleaned = "document"
    return cleaned[:max_len]


def sha256_stream(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def verify_document_integrity(document: "Document") -> dict:
    """Verify the stored blob matches the registered integrity hash (2.07.41).

    Returns ``{"match": bool, "computed_hash": ..., "stored_hash": ...}``.
    A missing blob or a recomputed hash that differs from ``document.sha256``
    yields ``match = False`` — the caller must reject the artifact.
    """
    stored_hash = document.sha256
    try:
        blob = document_storage.load_blob(document.storage_key)
    except document_storage.StorageError:
        return {
            "match": False,
            "computed_hash": None,
            "stored_hash": stored_hash,
            "reason": "blob_unavailable",
        }
    computed = sha256_stream(blob)
    return {
        "match": computed == stored_hash,
        "computed_hash": computed,
        "stored_hash": stored_hash,
        "reason": "ok" if computed == stored_hash else "hash_mismatch",
    }


async def seed_document_types(db: AsyncSession) -> None:
    """Insert the default document-type catalog (idempotent, by code)."""
    for spec_ in DEFAULT_DOCUMENT_TYPES:
        result = await db.execute(
            select(DocumentType.id).where(DocumentType.code == spec_["code"])
        )
        if result.scalar_one_or_none() is not None:
            continue
        db.add(
            DocumentType(
                code=spec_["code"],
                name=spec_["name"],
                configuration=spec_.get("configuration", {}),
            )
        )
    await db.flush()


async def get_document_type_by_code(
    db: AsyncSession, code: str
) -> DocumentType | None:
    result = await db.execute(
        select(DocumentType).where(DocumentType.code == code)
    )
    return result.scalar_one_or_none()


async def ensure_document_type(
    db: AsyncSession, code: str, *, default_classification: str | None = None
) -> DocumentType:
    atype = await get_document_type_by_code(db, code)
    if atype is not None:
        return atype
    conf = {}
    if default_classification:
        conf["default_classification"] = default_classification
    atype = DocumentType(code=code, name=code.replace("_", " ").title(), configuration=conf)
    db.add(atype)
    await db.flush()
    return atype


class DocumentIngestError(Exception):
    pass


async def ingest_document(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement_id: uuid.UUID | None,
    document_type_id: uuid.UUID,
    title: str,
    filename: str,
    media_type: str,
    data: bytes,
    actor_id: uuid.UUID | None,
    classification: str | None = None,
    immutable: bool = False,
    metadata_: dict | None = None,
) -> Document:
    """Validate, hash, store and register a document (spec 2.07.9).

    Uploads pass the document security gate first (spec 1.22.26/27):
    malware scan and DLP inspection. A positive verdict aborts the ingest
    before anything touches storage.
    """
    from app.services.document_security import scan_upload

    if not data:
        raise DocumentIngestError("Empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentIngestError("Document exceeds maximum upload size")
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise DocumentIngestError(f"Unsupported media type: {media_type}")

    security_verdicts = scan_upload(data)

    digest = sha256_stream(data)
    # Opaque key is generated from IDs; the raw filename is never in the path.
    content_ref = document_storage.build_content_ref(
        org_id=organization_id,
        agreement_id=agreement_id or uuid.uuid4(),
        doc_type="document",
        suffix="bin",
    )
    document_storage.store_blob(content_ref, data)

    atype = (
        await db.execute(select(DocumentType).where(DocumentType.id == document_type_id))
    ).scalar_one_or_none()
    conf = atype.configuration if atype else {}
    is_named_executed = (conf or {}).get("immutable_by_default") is True

    document = Document(
        organization_id=organization_id,
        agreement_id=agreement_id,
        document_type_id=document_type_id,
        title=secure_filename(title) if not title.strip() else title.strip(),
        filename=secure_filename(filename),
        media_type=media_type,
        storage_key=content_ref,
        sha256=digest,
        size_bytes=len(data),
        classification=classification
        or (conf or {}).get("classification")
        or DocumentClassification.SUPPORTING_DOCUMENT,
        status=DocumentStatus.ACTIVE,
        immutable=immutable or is_named_executed,
        metadata_={**(metadata_ or {}), "security": security_verdicts},
        created_by=actor_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(document)
    await db.flush()

    await record_event(
        db,
        tenant_id=organization_id,
        agreement_id=agreement_id,
        actor_id=actor_id,
        actor_type="user",
        action="DOCUMENT_INGESTED",
        resource_type="document",
        resource_id=document.id,
        metadata_json={"sha256": digest, "size_bytes": len(data)},
    )
    return document


class ImmutableDocumentError(Exception):
    pass


async def update_document_metadata(
    db: AsyncSession, document: Document, *, changes: dict, actor_id: uuid.UUID | None
) -> Document:
    """Service-level immutable-document guard (2.07.7)."""
    if document.immutable:
        raise ImmutableDocumentError("Immutable legal document cannot be modified")
    safe_keys = {"title", "classification", "metadata_"}
    for key, value in changes.items():
        if key in safe_keys and hasattr(document, key):
            setattr(document, key, value)
    await db.flush()
    return document


async def archive_document(
    db: AsyncSession, document: Document, *, actor_id: uuid.UUID | None
) -> Document:
    if document.immutable:
        raise ImmutableDocumentError("Immutable legal document cannot be archived")
    document.status = DocumentStatus.ARCHIVED
    document.archived_at = datetime.now(timezone.utc)
    await db.flush()
    return document


async def link_documents(
    db: AsyncSession,
    *,
    source_document_id: uuid.UUID,
    target_document_id: uuid.UUID,
    relationship_type: str,
    metadata_: dict | None = None,
) -> DocumentRelationship:
    rel = DocumentRelationship(
        source_document_id=source_document_id,
        target_document_id=target_document_id,
        relationship_type=relationship_type,
        metadata_=metadata_ or {},
    )
    db.add(rel)
    await db.flush()
    return rel


async def list_agreement_documents(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement_id: uuid.UUID,
    include_quarantined: bool = False,
) -> list[Document]:
    stmt = (
        select(Document)
        .where(
            Document.organization_id == organization_id,
            Document.agreement_id == agreement_id,
            Document.status.in_(
                [DocumentStatus.ACTIVE, DocumentStatus.UNDER_LEGAL_HOLD]
            ),
        )
        .order_by(Document.created_at.desc())
    )
    if not include_quarantined:
        stmt = stmt.where(Document.is_quarantined.is_(False))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_agreement_document(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement_id: uuid.UUID,
    document_id: uuid.UUID,
) -> Document | None:
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.organization_id == organization_id,
            Document.agreement_id == agreement_id,
        )
    )
    return result.scalar_one_or_none()


def document_to_dict(document: Document) -> dict:
    return {
        "id": str(document.id),
        "title": document.title,
        "filename": document.filename,
        "media_type": document.media_type,
        "sha256": document.sha256,
        "size_bytes": document.size_bytes,
        "classification": document.classification,
        "status": document.status,
        "is_quarantined": document.is_quarantined,
        "quarantine_reason": document.quarantine_reason,
        "immutable": document.immutable,
        "document_type": (
            document.document_type.code if document.document_type else None
        ),
        "created_at": (
            document.created_at.isoformat() if document.created_at else None
        ),
    }


async def repository_summary(
    db: AsyncSession, *, organization_id: uuid.UUID, agreement_id: uuid.UUID
) -> dict:
    """Consolidated repository response (spec 2.07.14)."""
    agreement = (
        await db.execute(
            select(Agreement).where(
                Agreement.id == agreement_id,
                Agreement.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()

    if agreement is None:
        return {}

    documents = await list_agreement_documents(
        db, organization_id=organization_id, agreement_id=agreement_id
    )
    executed_doc = next(
        (d for d in documents if d.document_type and d.document_type.code == "executed"),
        None,
    )

    repo_record = (
        await db.execute(
            select(RepositoryRecord).where(
                RepositoryRecord.agreement_id == agreement_id,
                RepositoryRecord.organization_id == organization_id,
            )
        )
    ).scalars().all()

    retention = (
        await db.execute(
            select(RetentionRecord).where(RetentionRecord.agreement_id == agreement_id)
        )
    ).scalar_one_or_none()

    holds = (
        await db.execute(
            select(LegalHold).where(
                LegalHold.agreement_id == agreement_id,
                LegalHold.status == "active",
            )
        )
    ).scalars().all()

    execution_package = (
        await db.execute(
            select(ExecutionPackage).where(
                ExecutionPackage.agreement_id == agreement_id
            )
        )
    ).scalars().first()

    return {
        "agreement_id": str(agreement.id),
        "agreement_title": agreement.title,
        "agreement_status": agreement.status,
        "effective_at": (
            agreement.effective_date.isoformat() if agreement.effective_date else None
        ),
        "terminated_at": None,
        "executed_document": document_to_dict(executed_doc) if executed_doc else None,
        "related_documents": [
            document_to_dict(d) for d in documents if d.id != (executed_doc.id if executed_doc else None)
        ],
        "execution_summary": (
            {
                "package_id": str(execution_package.id),
                "final_document_hash": execution_package.final_document_hash,
                "sealed_at": (
                    execution_package.created_at.isoformat()
                    if execution_package.created_at
                    else None
                ),
            }
            if execution_package
            else None
        ),
        "retention": {
            "status": retention.status if retention else "active",
            "retention_until": (
                retention.retention_until.isoformat() if retention else None
            ),
        },
        "legal_hold": {
            "on_hold": bool(holds),
            "holds": [
                {"id": str(h.id), "reason": h.reason, "type": h.hold_type}
                for h in holds
            ],
        },
        "repository_records": [
            {
                "id": str(r.id),
                "document_type": r.document_type,
                "content_hash": r.content_hash,
                "is_executed": r.is_executed,
            }
            for r in repo_record
        ],
    }


def _related_document_ids(documents: list[Document]) -> list[uuid.UUID]:
    return [d.id for d in documents]


async def get_document_children(
    db: AsyncSession, document_id: uuid.UUID
) -> list[DocumentRelationship]:
    result = await db.execute(
        select(DocumentRelationship).where(
            DocumentRelationship.source_document_id == document_id
        )
    )
    return list(result.scalars().all())


async def agreement_timeline(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement_id: uuid.UUID,
) -> list[dict]:
    """Activity timeline (spec 2.07.31-2.07.32): audit events + repository artifacts."""
    from app.models.audit import AuditEvent

    result = await db.execute(
        select(AuditEvent)
        .where(
            AuditEvent.tenant_id == organization_id,
            AuditEvent.agreement_id == agreement_id,
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(200)
    )
    events = []
    for evt in result.scalars().all():
        events.append(
            {
                "id": str(evt.id),
                "action": evt.action,
                "actor_id": str(evt.actor_id) if evt.actor_id else None,
                "resource_type": evt.resource_type,
                "resource_id": str(evt.resource_id) if evt.resource_id else None,
                "timestamp": evt.created_at.isoformat(),
                "metadata": evt.metadata_json,
            }
        )
    return events


async def agreement_evidence(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement_id: uuid.UUID,
) -> dict:
    """Evidence package aggregation (spec 2.07.18 / 2.06)."""
    packages = (
        await db.execute(
            select(ExecutionPackage)
            .where(
                ExecutionPackage.tenant_id == organization_id,
                ExecutionPackage.agreement_id == agreement_id,
            )
            .order_by(ExecutionPackage.created_at.desc())
        )
    ).scalars().all()

    evidence_docs = (
        await db.execute(
            select(Document)
            .where(
                Document.organization_id == organization_id,
                Document.agreement_id == agreement_id,
                Document.classification.in_(
                    [DocumentClassification.EXECUTION_EVIDENCE, DocumentClassification.LEGAL_RECORD]
                ),
            )
            .order_by(Document.created_at)
        )
    ).scalars().all()

    return {
        "execution_packages": [
            {
                "id": str(p.id),
                "final_document_hash": p.final_document_hash,
                "created_at": (
                    p.created_at.isoformat() if p.created_at else None
                ),
            }
            for p in packages
        ],
        "evidence_documents": [document_to_dict(d) for d in evidence_docs],
    }


async def register_executed_artifact(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement_id: uuid.UUID,
    content_ref: str,
    content_hash: str,
    size_bytes: int | None,
    version_number: int | None,
    actor_id: uuid.UUID | None,
) -> Document:
    """Create the immutable EXECUTED document row over already-stored bytes."""
    atype = await get_document_type_by_code(db, "executed")
    if atype is None:
        atype = await ensure_document_type(
            db, "executed", default_classification=DocumentClassification.LEGAL_RECORD
        )
    document = Document(
        organization_id=organization_id,
        agreement_id=agreement_id,
        document_type_id=atype.id,
        title=f"Executed Agreement v{version_number or 1}",
        filename=f"executed_agreement_{agreement_id}.pdf",
        media_type="application/pdf",
        storage_key=content_ref,
        sha256=content_hash,
        size_bytes=size_bytes or 0,
        classification=DocumentClassification.LEGAL_RECORD,
        status=DocumentStatus.ACTIVE,
        immutable=True,
        metadata_={"version_number": version_number},
        created_by=actor_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(document)
    await db.flush()
    return document


def presigned_download_url(download_token: str) -> str:
    """Build the secure, short-lived download path handed to the client."""
    return f"/api/v1/documents/token/{download_token}"