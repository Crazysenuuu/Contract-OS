"""Execution evidence service (spec 1.15 / 2.06).

Coordinates signature requests, signer records, execution requirements and
the sealed execution package. Every material action is appended to the
tenant's audit hash chain.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.execution import (
    ExecutionEvidenceItem,
    ExecutionPackage,
    ExecutionRequirement,
    SignatureRequest,
    SignerRecord,
)
from app.services.audit_service import record_event


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


# --------------------------------------------------------------------------
# Signature requests
# --------------------------------------------------------------------------

async def create_signature_request(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agreement_id: uuid.UUID,
    version_id: uuid.UUID,
    name: str,
    email: str,
    party_id: uuid.UUID | None = None,
    role: str = "signer",
    signer_type: str = "external",
    expires_at: datetime | None = None,
    created_by: uuid.UUID,
    metadata_json: dict | None = None,
) -> SignatureRequest:
    request = SignatureRequest(
        tenant_id=tenant_id,
        agreement_id=agreement_id,
        version_id=version_id,
        party_id=party_id,
        name=name,
        email=email,
        role=role,
        signer_type=signer_type,
        status="pending",
        expires_at=expires_at,
        created_by=created_by,
        metadata_json=metadata_json or {},
    )
    db.add(request)
    await db.flush()

    await record_event(
        db,
        tenant_id=tenant_id,
        agreement_id=agreement_id,
        actor_id=created_by,
        actor_type="user",
        action="SIGNATURE_REQUEST_CREATED",
        resource_type="signature_request",
        resource_id=request.id,
        metadata_json={
            "email": email,
            "party_id": str(party_id) if party_id else None,
            "role": role,
            "signer_type": signer_type,
        },
    )
    await db.flush()
    return request


async def list_signature_requests(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> list[SignatureRequest]:
    result = await db.execute(
        select(SignatureRequest)
        .where(SignatureRequest.agreement_id == agreement_id)
        .order_by(SignatureRequest.created_at.asc())
    )
    return list(result.scalars().all())


async def mark_request_sent(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    signature_request: SignatureRequest,
    *,
    actor_id: uuid.UUID,
) -> SignatureRequest:
    if signature_request.status != "pending":
        raise ValueError(
            f"Cannot send signature request in status: {signature_request.status}"
        )
    signature_request.status = "sent"
    signature_request.sent_at = now_utc()
    await db.flush()

    await record_event(
        db,
        tenant_id=tenant_id,
        agreement_id=signature_request.agreement_id,
        actor_id=actor_id,
        actor_type="user",
        action="SIGNATURE_REQUEST_SENT",
        resource_type="signature_request",
        resource_id=signature_request.id,
        metadata_json={"email": signature_request.email},
    )
    await db.flush()
    return signature_request


async def decline_signature_request(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    signature_request: SignatureRequest,
    *,
    actor_id: uuid.UUID,
    reason: str | None = None,
) -> SignatureRequest:
    if signature_request.status == "signed":
        raise ValueError("Cannot decline an already-signed signature request")
    signature_request.status = "declined"
    await db.flush()

    await record_event(
        db,
        tenant_id=tenant_id,
        agreement_id=signature_request.agreement_id,
        actor_id=actor_id,
        actor_type="user",
        action="SIGNATURE_REQUEST_DECLINED",
        resource_type="signature_request",
        resource_id=signature_request.id,
        metadata_json={"reason": reason},
    )
    await db.flush()
    return signature_request


async def record_signer(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    signature_request: SignatureRequest,
    name: str,
    email: str,
    consent_text: str,
    signer_type: str | None = None,
    user_id: uuid.UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    identity_verified: bool = False,
    identity_method: str | None = None,
) -> SignerRecord:
    """Record a completed signature with evidence + audit event."""
    signer_type = signer_type or signature_request.signer_type

    # Signature hash binds the signer, the version hash and the timestamp.
    # Version content_hash lives on the version row; we bind the request id
    # and consent text so the record cannot be replayed against another
    # version/consent.
    timestamp_str = now_utc().isoformat()
    signature_input = (
        f"{signature_request.id}:{signature_request.version_id}:"
        f"{email}:{timestamp_str}:{consent_text}"
    )
    signature_hash = hashlib.sha256(signature_input.encode()).hexdigest()

    record = SignerRecord(
        signature_request_id=signature_request.id,
        agreement_id=signature_request.agreement_id,
        version_id=signature_request.version_id,
        signer_type=signer_type,
        user_id=user_id,
        name=name,
        email=email,
        signed_at=now_utc(),
        ip_address=ip_address,
        user_agent=user_agent,
        consent_text=consent_text,
        signature_hash=signature_hash,
        identity_verified=identity_verified,
        identity_method=identity_method,
    )
    db.add(record)

    signature_request.status = "signed"
    signature_request.signed_at = now_utc()

    await record_event(
        db,
        tenant_id=tenant_id,
        agreement_id=signature_request.agreement_id,
        actor_id=user_id,
        actor_type="system" if user_id is None else "user",
        action="SIGNED",
        resource_type="signature_request",
        resource_id=signature_request.id,
        metadata_json={
            "signature_hash": signature_hash,
            "email": email,
            "identity_verified": identity_verified,
            "identity_method": identity_method,
        },
        ip_address=ip_address,
    )
    await db.flush()
    return record


# --------------------------------------------------------------------------
# Execution requirements
# --------------------------------------------------------------------------

async def create_execution_requirement(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    requirement_type: str,
    description: str,
    agreement_id: uuid.UUID | None = None,
    agreement_type_id: uuid.UUID | None = None,
    severity: str = "required",
    created_by: uuid.UUID,
    metadata_json: dict | None = None,
) -> ExecutionRequirement:
    requirement = ExecutionRequirement(
        tenant_id=tenant_id,
        agreement_id=agreement_id,
        agreement_type_id=agreement_type_id,
        requirement_type=requirement_type,
        description=description,
        severity=severity,
        status="pending",
        metadata_json=metadata_json or {},
    )
    db.add(requirement)
    await db.flush()

    await record_event(
        db,
        tenant_id=tenant_id,
        agreement_id=agreement_id,
        actor_id=created_by,
        actor_type="user",
        action="EXECUTION_REQUIREMENT_CREATED",
        resource_type="execution_requirement",
        resource_id=requirement.id,
        metadata_json={"requirement_type": requirement_type, "severity": severity},
    )
    await db.flush()
    return requirement


async def list_execution_requirements(
    db: AsyncSession,
    agreement_id: uuid.UUID | None = None,
) -> list[ExecutionRequirement]:
    query = select(ExecutionRequirement)
    if agreement_id is not None:
        query = query.where(
            (ExecutionRequirement.agreement_id == agreement_id)
            | (ExecutionRequirement.agreement_id.is_(None))
        )
    result = await db.execute(query.order_by(ExecutionRequirement.created_at.asc()))
    return list(result.scalars().all())


async def satisfy_execution_requirement(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    requirement: ExecutionRequirement,
    actor_id: uuid.UUID,
    status_value: str = "satisfied",
    metadata_json: dict | None = None,
) -> ExecutionRequirement:
    requirement.status = status_value
    requirement.satisfied_at = now_utc()
    requirement.satisfied_by = actor_id
    await db.flush()

    await record_event(
        db,
        tenant_id=tenant_id,
        agreement_id=requirement.agreement_id,
        actor_id=actor_id,
        actor_type="user",
        action="EXECUTION_REQUIREMENT_SATISFIED",
        resource_type="execution_requirement",
        resource_id=requirement.id,
        metadata_json={
            "status": status_value,
            **(metadata_json or {}),
        },
    )
    await db.flush()
    return requirement


async def check_execution_requirements(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> dict:
    """Compute execution readiness for an agreement.

    Returns required vs satisfied counts plus the list of pending
    requirements. Optional (non-blocking) requirements are reported but do
    not block readiness.
    """
    requirements = await list_execution_requirements(db, agreement_id)
    required = [r for r in requirements if r.severity == "required"]
    satisfied_required = [r for r in required if r.status == "satisfied"]
    pending = [r for r in requirements if r.status == "pending"]
    return {
        "ready": len(required) > 0 and len(satisfied_required) == len(required),
        "required_count": len(required),
        "satisfied_required_count": len(satisfied_required),
        "pending_count": len(pending),
        "pending": [
            {
                "id": str(r.id),
                "requirement_type": r.requirement_type,
                "description": r.description,
                "severity": r.severity,
            }
            for r in pending
        ],
    }


# --------------------------------------------------------------------------
# Execution package
# --------------------------------------------------------------------------

async def get_execution_package(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> ExecutionPackage | None:
    result = await db.execute(
        select(ExecutionPackage)
        .options(selectinload(ExecutionPackage.items))
        .where(ExecutionPackage.agreement_id == agreement_id)
        .order_by(ExecutionPackage.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _package_hash(
    final_document_hash: str,
    items: list[ExecutionEvidenceItem],
) -> str:
    """Hash of the final document hash + every evidence item hash."""
    item_hashes = "".join(sorted(i.content_hash for i in items))
    return hashlib.sha256(
        f"{final_document_hash}:{item_hashes}".encode()
    ).hexdigest()


async def seal_execution_package(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agreement_id: uuid.UUID,
    version_id: uuid.UUID,
    final_document_hash: str,
    sealed_by: uuid.UUID,
    metadata_json: dict | None = None,
) -> ExecutionPackage:
    """Seal the execution package.

    Collects every signer record as evidence items, computes the package
    hash, and appends an audit event. Returns 409-style error (ValueError)
    if a package is already sealed for this agreement.
    """
    existing = await get_execution_package(db, agreement_id)
    if existing is not None and existing.status == "sealed":
        raise ValueError("An execution package is already sealed for this agreement")

    package = ExecutionPackage(
        tenant_id=tenant_id,
        agreement_id=agreement_id,
        version_id=version_id,
        final_document_hash=final_document_hash,
        status="sealed",
        sealed_at=now_utc(),
        sealed_by=sealed_by,
        metadata_json=metadata_json or {},
    )
    db.add(package)
    await db.flush()

    # Pull all signer records for this agreement into the package.
    result = await db.execute(
        select(SignerRecord).where(SignerRecord.agreement_id == agreement_id)
    )
    signers = list(result.scalars().all())
    items: list[ExecutionEvidenceItem] = []
    for signer in signers:
        item = ExecutionEvidenceItem(
            package_id=package.id,
            evidence_type="signature",
            data_json={
                "signer_record_id": str(signer.id),
                "name": signer.name,
                "email": signer.email,
                "signed_at": signer.signed_at.isoformat(),
                "signature_hash": signer.signature_hash,
                "identity_verified": signer.identity_verified,
                "identity_method": signer.identity_method,
            },
            content_hash=signer.signature_hash,
        )
        db.add(item)
        items.append(item)
    await db.flush()

    # Add a document-hash evidence item.
    doc_item = ExecutionEvidenceItem(
        package_id=package.id,
        evidence_type="document_hash",
        data_json={
            "version_id": str(version_id),
            "document_hash": final_document_hash,
        },
        content_hash=final_document_hash,
    )
    db.add(doc_item)
    items.append(doc_item)
    await db.flush()

    package.package_hash = _package_hash(final_document_hash, items)
    await db.flush()
    await db.refresh(package, ["items"])

    await record_event(
        db,
        tenant_id=tenant_id,
        agreement_id=agreement_id,
        actor_id=sealed_by,
        actor_type="user",
        action="EXECUTION_PACKAGE_SEALED",
        resource_type="execution_package",
        resource_id=package.id,
        metadata_json={
            "package_hash": package.package_hash,
            "final_document_hash": final_document_hash,
            "signature_count": len(signers),
        },
    )
    await db.flush()
    return package


async def verify_execution_package(
    db: AsyncSession,
    package: ExecutionPackage,
) -> dict:
    """Recompute the package hash and check every evidence item."""
    recomputed = _package_hash(package.final_document_hash, package.items)
    return {
        "valid": recomputed == package.package_hash,
        "package_id": str(package.id),
        "package_hash": package.package_hash,
        "recomputed_hash": recomputed,
        "evidence_item_count": len(package.items),
        "status": package.status,
    }


def serialize_request(r: SignatureRequest) -> dict:
    return {
        "id": str(r.id),
        "agreement_id": str(r.agreement_id),
        "version_id": str(r.version_id),
        "party_id": str(r.party_id) if r.party_id else None,
        "name": r.name,
        "email": r.email,
        "role": r.role,
        "signer_type": r.signer_type,
        "status": r.status,
        "sent_at": r.sent_at.isoformat() if r.sent_at else None,
        "signed_at": r.signed_at.isoformat() if r.signed_at else None,
        "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        "metadata_json": r.metadata_json or {},
        "created_at": r.created_at.isoformat(),
    }


def serialize_requirement(r: ExecutionRequirement) -> dict:
    return {
        "id": str(r.id),
        "agreement_id": str(r.agreement_id) if r.agreement_id else None,
        "agreement_type_id": str(r.agreement_type_id) if r.agreement_type_id else None,
        "requirement_type": r.requirement_type,
        "description": r.description,
        "severity": r.severity,
        "status": r.status,
        "satisfied_at": r.satisfied_at.isoformat() if r.satisfied_at else None,
        "satisfied_by": str(r.satisfied_by) if r.satisfied_by else None,
        "metadata_json": r.metadata_json or {},
        "created_at": r.created_at.isoformat(),
    }


def serialize_package(p: ExecutionPackage) -> dict:
    return {
        "id": str(p.id),
        "agreement_id": str(p.agreement_id),
        "version_id": str(p.version_id),
        "final_document_hash": p.final_document_hash,
        "package_hash": p.package_hash,
        "status": p.status,
        "sealed_at": p.sealed_at.isoformat() if p.sealed_at else None,
        "sealed_by": str(p.sealed_by) if p.sealed_by else None,
        "metadata_json": p.metadata_json or {},
        "created_at": p.created_at.isoformat(),
        "items": [
            {
                "id": str(i.id),
                "evidence_type": i.evidence_type,
                "content_hash": i.content_hash,
                "data_json": i.data_json or {},
                "created_at": i.created_at.isoformat(),
            }
            for i in p.items
        ],
    }