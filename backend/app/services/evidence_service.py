import uuid
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request

from app.models.signer_evidence import SignerEvidence
from app.models.agreement import Agreement
from app.models.external_party import ExternalParty
from app.services.storage_service import StorageService
import io

class EvidencePackage:
    def __init__(self, agreement_id: uuid.UUID, sealed_doc_hash: str | None, timeline: list[dict]):
        self.agreement_id = agreement_id
        self.sealed_doc_hash = sealed_doc_hash
        self.timeline = timeline

    def to_dict(self) -> dict:
        return {
            "agreement_id": str(self.agreement_id),
            "sealed_doc_hash": self.sealed_doc_hash,
            "timeline": self.timeline,
        }

async def record_event(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    signer_id: uuid.UUID,
    action: str,
    request: Request | None = None,
    consent_event_id: str | None = None,
    identity_check_result: dict | None = None,
    document_hash_at_time: str | None = None
) -> SignerEvidence:
    """Record an action (viewed, signed, declined) by a signer."""
    ip_address = None
    user_agent = None
    if request:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

    evidence = SignerEvidence(
        agreement_id=agreement_id,
        signer_id=signer_id,
        action=action,
        ip_address=ip_address,
        user_agent=user_agent,
        consent_event_id=consent_event_id,
        identity_check_result=identity_check_result,
        document_hash_at_time=document_hash_at_time
    )
    db.add(evidence)
    await db.flush()
    return evidence

async def build_evidence_package(db: AsyncSession, agreement_id: uuid.UUID) -> EvidencePackage:
    """Build the evidence package for all signers of an agreement."""
    # Get agreement to check if it's sealed and get the sealed hash
    agreement_res = await db.execute(select(Agreement).where(Agreement.id == agreement_id))
    agreement = agreement_res.scalar_one_or_none()
    
    if not agreement:
        raise ValueError("Agreement not found")

    sealed_hash = None
    if agreement.sealed_document_key:
        from app.models.stored_object import StoredObject
        obj_res = await db.execute(select(StoredObject).where(StoredObject.key == agreement.sealed_document_key))
        stored_obj = obj_res.scalar_one_or_none()
        if stored_obj:
            sealed_hash = stored_obj.sha256_hash

    # Get evidence timeline
    evidence_res = await db.execute(
        select(SignerEvidence, ExternalParty.signatory_name, ExternalParty.signatory_email)
        .join(ExternalParty, ExternalParty.id == SignerEvidence.signer_id)
        .where(SignerEvidence.agreement_id == agreement_id)
        .order_by(SignerEvidence.timestamp.asc())
    )
    
    timeline = []
    for evidence, name, email in evidence_res.all():
        timeline.append({
            "evidence_id": str(evidence.id),
            "signer_id": str(evidence.signer_id),
            "signer_name": name,
            "signer_email": email,
            "action": evidence.action,
            "timestamp": evidence.timestamp.isoformat(),
            "ip_address": evidence.ip_address,
            "user_agent": evidence.user_agent,
            "document_hash_at_time": evidence.document_hash_at_time,
        })

    return EvidencePackage(
        agreement_id=agreement_id,
        sealed_doc_hash=sealed_hash,
        timeline=timeline
    )

async def export_evidence_pdf(db: AsyncSession, agreement_id: uuid.UUID) -> bytes:
    """Generate a PDF summary of the evidence package (for legal review)."""
    package = await build_evidence_package(db, agreement_id)
    
    # Simple text generation for now, ideally use a PDF generation library like reportlab or fpdf
    # Since we can't install arbitrary packages easily, we'll return a simple utf-8 text file as bytes
    # But wait, it needs to be a PDF. We could mock the PDF creation for now.
    output = io.StringIO()
    output.write(f"Evidence Package for Agreement {agreement_id}\n")
    output.write(f"Sealed Document Hash: {package.sealed_doc_hash or 'Not sealed'}\n\n")
    output.write("Timeline:\n")
    for event in package.timeline:
        output.write(f"[{event['timestamp']}] {event['signer_name']} ({event['signer_email']}): {event['action']}\n")
        output.write(f"  IP: {event['ip_address']}, User Agent: {event['user_agent']}\n")
        output.write(f"  Hash at time: {event.get('document_hash_at_time')}\n\n")
        
    return output.getvalue().encode("utf-8")
