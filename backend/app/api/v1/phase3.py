"""
Phase 3 API Endpoints.

Jurisdiction management, clause suggestions, and e-signature integration.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.jurisdiction import Jurisdiction, JurisdictionClause
from app.models.user import User
from app.services.clause_suggestion import ClauseSuggestionEngine, get_clause_suggestion_engine
from app.services.esignature import (
    get_esignature_provider,
    resolve_esignature_provider,
    SignerInfo,
)

router = APIRouter(prefix="/phase3", tags=["Phase 3"])


# --- Jurisdiction Schemas ---

class JurisdictionResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    region: Optional[str]
    language: str
    legal_system: Optional[str]
    currency: str
    timezone: str
    required_clauses: Optional[list]
    prohibited_clauses: Optional[list]
    signature_requirements: Optional[dict]
    default_dispute_resolution: Optional[str]
    is_active: bool

    model_config = {"from_attributes": True}


class ClauseSuggestionResponse(BaseModel):
    clause_type: str
    name: str
    text: str
    explanation: str
    risk_level: str
    is_mandatory: bool
    alternatives: list
    confidence: float


class RiskAssessmentResponse(BaseModel):
    category: str
    severity: str
    score: float
    description: str
    mitigation: str
    jurisdiction_impact: Optional[str]


class ClauseCompareRequest(BaseModel):
    clause_type: str
    text_a: str
    text_b: str
    jurisdiction_code: Optional[str] = None


class ESignatureRequest(BaseModel):
    agreement_id: uuid.UUID
    signers: list[dict]  # [{"name": "...", "email": "...", "role": "signer"}]
    subject: str
    message: str


# --- Jurisdiction Endpoints ---

@router.get("/jurisdictions", response_model=list[JurisdictionResponse])
async def list_jurisdictions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all active jurisdictions."""
    result = await db.execute(
        select(Jurisdiction).where(
            Jurisdiction.is_active == True
        ).order_by(Jurisdiction.name)
    )
    return result.scalars().all()


@router.get("/jurisdictions/{code}", response_model=JurisdictionResponse)
async def get_jurisdiction(
    code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get jurisdiction details by code."""
    result = await db.execute(
        select(Jurisdiction).where(
            Jurisdiction.code == code.upper(),
        )
    )
    jurisdiction = result.scalar_one_or_none()

    if not jurisdiction:
        raise HTTPException(status_code=404, detail="Jurisdiction not found")

    return jurisdiction


# --- Clause Suggestion Endpoints ---

@router.get(
    "/clause-suggestions/{jurisdiction_code}",
    response_model=list[ClauseSuggestionResponse],
)
async def suggest_clauses(
    jurisdiction_code: str,
    agreement_type: str = "mutual_nda",
    risk_tolerance: str = "medium",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get clause suggestions for a jurisdiction and agreement type."""
    engine = get_clause_suggestion_engine(db)
    suggestions = await engine.suggest_clauses(
        jurisdiction_code=jurisdiction_code.upper(),
        agreement_type=agreement_type,
        risk_tolerance=risk_tolerance,
    )
    return suggestions


@router.post(
    "/risk-assessment",
    response_model=list[RiskAssessmentResponse],
)
async def assess_risks(
    clause_text: str,
    clause_type: str,
    jurisdiction_code: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Assess risks in a clause."""
    engine = get_clause_suggestion_engine(db)
    risks = await engine.assess_risks(
        clause_text=clause_text,
        clause_type=clause_type,
        jurisdiction_code=jurisdiction_code,
    )
    return risks


@router.post("/compare-clauses")
async def compare_clauses(
    data: ClauseCompareRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compare two versions of a clause."""
    engine = get_clause_suggestion_engine(db)
    result = await engine.compare_clauses(
        clause_type=data.clause_type,
        text_a=data.text_a,
        text_b=data.text_b,
        jurisdiction_code=data.jurisdiction_code,
    )
    return result


# --- E-Signature Endpoints ---

@router.post("/esignature/create-envelope")
async def create_envelope(
    data: ESignatureRequest,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Create an e-signature envelope for an agreement.

    The envelope carries the *rendered agreement PDF* (tenant-scoped), never
    a placeholder — a real DocuSign / Adobe Sign envelope must contain the
    document the parties actually agreed to (spec §21, §65).
    """
    from app.models.agreement import Agreement
    from app.services.agreement_renderer import render_agreement

    provider = resolve_esignature_provider()  # config-driven (spec 24.5)

    result = await db.execute(
        select(Agreement).where(
            Agreement.id == data.agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()
    if agreement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement not found",
        )

    try:
        rendered = await render_agreement(db, agreement_id=agreement.id, generate_pdf=True)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if not rendered.pdf:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF renderer unavailable; cannot create a signing envelope without the document",
        )

    document_bytes = rendered.pdf
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in agreement.title)[:80].strip() or "Agreement"
    document_name = f"{safe_title}.pdf"

    signers = [
        SignerInfo(
            name=s.get("name", ""),
            email=s.get("email", ""),
            role=s.get("role", "signer"),
            order=s.get("order", 1),
        )
        for s in data.signers
    ]

    result = await provider.create_envelope(
        document_bytes=document_bytes,
        document_name=document_name,
        signers=signers,
        subject=data.subject,
        message=data.message,
        agreement_id=data.agreement_id,
    )

    return {
        "envelope_id": result.envelope_id,
        "status": result.status,
        "signing_url": result.signing_url,
        "created_at": result.created_at.isoformat() if result.created_at else None,
        "document_name": document_name,
        "content_hash": rendered.content_hash,
    }


@router.get("/esignature/status/{envelope_id}")
async def get_envelope_status(
    envelope_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get e-signature envelope status."""
    provider = resolve_esignature_provider()
    return await provider.get_envelope_status(envelope_id)


@router.get("/esignature/signing-url/{envelope_id}")
async def get_signing_url(
    envelope_id: str,
    signer_email: str,
    current_user: User = Depends(get_current_user),
):
    """Get signing URL for a specific signer."""
    provider = resolve_esignature_provider()
    url = await provider.get_signing_url(envelope_id, signer_email)

    if not url:
        raise HTTPException(status_code=404, detail="Signing URL not found")

    return {"signing_url": url}


@router.post("/esignature/simulate-sign/{envelope_id}")
async def simulate_signing(
    envelope_id: str,
    signer_email: str,
    current_user: User = Depends(get_current_user),
):
    """Simulate a signer signing (for testing)."""
    provider = resolve_esignature_provider()

    if not hasattr(provider, "simulate_signing"):
        raise HTTPException(status_code=400, detail="Simulation not available")

    return await provider.simulate_signing(envelope_id, signer_email)


@router.post("/esignature/cancel/{envelope_id}")
async def cancel_envelope(
    envelope_id: str,
    current_user: User = Depends(get_current_user),
):
    """Cancel an e-signature envelope."""
    provider = resolve_esignature_provider()
    success = await provider.cancel_envelope(envelope_id)

    if not success:
        raise HTTPException(status_code=404, detail="Envelope not found")

    return {"cancelled": True}
