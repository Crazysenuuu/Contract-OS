"""Audit log API endpoints.

View and verify the immutable, tamper-evident audit trail (spec 1.20).
Audit events form a per-tenant hash chain: verification recomputes every
hash and reports the first broken link, if any.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.audit import AuditEvent, AuditEvidence
from app.models.user import User
from app.services.audit_service import (
    create_evidence,
    export_history,
    list_evidence,
    verify_chain,
)

router = APIRouter(
    prefix="/agreements",
    tags=["audit"],
)


class AuditEventResponse(BaseModel):
    id: UUID
    agreement_id: UUID | None
    actor_id: UUID | None
    actor_type: str
    action: str
    resource_type: str | None
    resource_id: UUID | None
    metadata_json: dict | None
    ip_address: str | None
    sequence_number: int | None
    prev_hash: str | None
    event_hash: str | None
    created_at: str

    model_config = {"from_attributes": True}


class ChainVerificationResponse(BaseModel):
    valid: bool
    checked: int
    message: str
    first_broken: dict | None = None


class EvidenceCreate(BaseModel):
    evidence_type: str
    content_hash: str
    version_id: UUID | None = None
    content_ref: str | None = None
    metadata_json: dict | None = None


class EvidenceResponse(BaseModel):
    id: UUID
    agreement_id: UUID | None
    version_id: UUID | None
    evidence_type: str
    content_hash: str
    content_ref: str | None
    metadata_json: dict | None
    created_at: str

    model_config = {"from_attributes": True}


@router.get(
    "/{agreement_id}/audit",
    response_model=list[AuditEventResponse],
)
async def list_audit_events(
    agreement_id: UUID,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List audit log entries for an agreement.

    Only accessible by internal users with agreement access.
    """
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.agreement_id == agreement_id)
        .order_by(AuditEvent.sequence_number.desc())
        .limit(limit)
    )
    events = result.scalars().all()

    return [
        AuditEventResponse(
            id=event.id,
            agreement_id=event.agreement_id,
            actor_id=event.actor_id,
            actor_type=event.actor_type,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            metadata_json=event.metadata_json,
            ip_address=event.ip_address,
            sequence_number=event.sequence_number,
            prev_hash=event.prev_hash,
            event_hash=event.event_hash,
            created_at=event.created_at.isoformat(),
        )
        for event in events
    ]


@router.post(
    "/{agreement_id}/audit/verify",
    response_model=ChainVerificationResponse,
)
async def verify_audit_chain(
    agreement_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Verify the integrity of the tenant's audit hash chain."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    return await verify_chain(db, tenant_id=org_id, agreement_id=agreement_id)


@router.get("/{agreement_id}/audit/export")
async def export_audit_history(
    agreement_id: UUID,
    limit: int = 1000,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Export an agreement's complete audit history (with chain hashes)."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    return await export_history(db, agreement_id=agreement_id, limit=limit)


@router.get(
    "/{agreement_id}/audit/evidence",
    response_model=list[EvidenceResponse],
)
async def list_audit_evidence(
    agreement_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List evidence snapshots captured for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    evidence = await list_evidence(db, agreement_id=agreement_id)
    return [
        EvidenceResponse(
            id=e.id,
            agreement_id=e.agreement_id,
            version_id=e.version_id,
            evidence_type=e.evidence_type,
            content_hash=e.content_hash,
            content_ref=e.content_ref,
            metadata_json=e.metadata_json,
            created_at=e.created_at.isoformat(),
        )
        for e in evidence
    ]


@router.post(
    "/{agreement_id}/audit/evidence",
    response_model=EvidenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_audit_evidence(
    agreement_id: UUID,
    data: EvidenceCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Capture an evidence snapshot for an agreement version.

    Used at signing/execution/amendment moments to prove what the document
    content was at that point in time.
    """
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    evidence = await create_evidence(
        db,
        tenant_id=org_id,
        agreement_id=agreement_id,
        version_id=data.version_id,
        evidence_type=data.evidence_type,
        content_hash=data.content_hash,
        content_ref=data.content_ref,
        metadata_json=data.metadata_json,
        created_by=current_user.id,
    )
    return EvidenceResponse(
        id=evidence.id,
        agreement_id=evidence.agreement_id,
        version_id=evidence.version_id,
        evidence_type=evidence.evidence_type,
        content_hash=evidence.content_hash,
        content_ref=evidence.content_ref,
        metadata_json=evidence.metadata_json,
        created_at=evidence.created_at.isoformat(),
    )


@router.delete(
    "/{agreement_id}/audit",
    status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
    include_in_schema=False,
)
async def delete_audit_events_not_allowed():
    """Audit events are immutable — deletion is refused at the API layer."""
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="Audit events cannot be deleted",
    )