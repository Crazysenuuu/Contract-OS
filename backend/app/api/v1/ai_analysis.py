"""AI analysis API endpoints.

Provide contract analysis, risk detection, and comparison.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement, AgreementVersion
from app.models.ai_analysis import (
    ContractComparison,
    ContractSummary,
    RiskFinding,
)
from app.models.user import User
from app.services.ai_service import AIService
from app.services.entitlement_service import require_entitlement

router = APIRouter(
    prefix="/agreements",
    tags=["ai-analysis"],
)

ai_service = AIService()


# --- Schemas ---


class AnalysisCoverage(BaseModel):
    """Honest accounting of how much of the contract the model read
    (spec 1.19.24) — absent only from legacy in-process results."""

    characters_total: int
    chunks: int
    chunks_analyzed: int
    chunks_failed: int
    truncated: bool
    note: str | None = None


class AnalysisResponse(BaseModel):
    summary: str
    key_terms: dict
    risks: list[dict]
    confidence: float
    coverage: AnalysisCoverage | None = None


class RiskFindingResponse(BaseModel):
    id: uuid.UUID
    category: str
    severity: str
    clause_identifier: str | None
    finding: str
    explanation: str | None
    recommendation: str | None
    confidence: float
    reviewer_status: str

    model_config = {"from_attributes": True}


class ComparisonResponse(BaseModel):
    summary: str
    changes_detected: int
    risk_changes: list[dict]
    detailed_changes: list[dict]
    coverage: dict | None = None


class ReviewStatusUpdate(BaseModel):
    reviewer_status: str  # 'accepted', 'rejected', 'mitigated'
    reviewer_notes: str | None = None


# --- Endpoints ---


@router.post(
    "/{agreement_id}/analyze",
    response_model=AnalysisResponse,
    status_code=status.HTTP_200_OK,
)
async def analyze_contract(
    agreement_id: uuid.UUID,
    _entitled: None = Depends(require_entitlement("ai_analyses")),
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Analyze a contract and return summary + risks.

    Uses AI to extract key terms and detect risks.
    """
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    # Get latest version
    result = await db.execute(
        select(AgreementVersion)
        .where(AgreementVersion.agreement_id == agreement_id)
        .order_by(AgreementVersion.version_number.desc())
        .limit(1)
    )
    version = result.scalar_one_or_none()

    if version is None or not version.content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No rendered content available for analysis",
        )

    # Run AI analysis
    analysis = await ai_service.analyze_contract(
        contract_text=version.content,
        agreement_type="mutual_nda",
    )

    # Store summary
    summary = ContractSummary(
        agreement_id=agreement_id,
        version_id=version.id,
        summary_text=analysis.summary,
        key_terms=analysis.key_terms,
        model_used=analysis.model_used,
        confidence=analysis.confidence,
    )
    db.add(summary)

    # Store risks
    for risk in analysis.risks:
        finding = RiskFinding(
            agreement_id=agreement_id,
            version_id=version.id,
            category=risk.category,
            severity=risk.severity,
            finding=risk.finding,
            explanation=risk.explanation,
            recommendation=risk.recommendation,
            confidence=risk.confidence,
        )
        db.add(finding)

    await db.flush()

    return AnalysisResponse(
        summary=analysis.summary,
        key_terms=analysis.key_terms,
        risks=[
            {
                "category": r.category,
                "severity": r.severity,
                "finding": r.finding,
                "explanation": r.explanation,
                "recommendation": r.recommendation,
                "confidence": r.confidence,
            }
            for r in analysis.risks
        ],
        confidence=analysis.confidence,
        coverage=analysis.coverage or None,
    )


@router.post(
    "/{agreement_id}/risks",
    response_model=list[RiskFindingResponse],
    status_code=status.HTTP_200_OK,
)
async def detect_risks(
    agreement_id: uuid.UUID,
    _entitled: None = Depends(require_entitlement("ai_analyses")),
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Detect risks in a contract.

    Returns detailed risk findings with severity and recommendations.
    """
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    # Get latest version
    result = await db.execute(
        select(AgreementVersion)
        .where(AgreementVersion.agreement_id == agreement_id)
        .order_by(AgreementVersion.version_number.desc())
        .limit(1)
    )
    version = result.scalar_one_or_none()

    if version is None or not version.content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No rendered content available for risk analysis",
        )

    # Run AI risk detection
    risks = await ai_service.detect_risks(
        contract_text=version.content,
        agreement_type="mutual_nda",
    )

    # Store findings
    findings = []
    for risk in risks:
        finding = RiskFinding(
            agreement_id=agreement_id,
            version_id=version.id,
            category=risk.category,
            severity=risk.severity,
            clause_identifier=risk.clause_identifier,
            clause_text=risk.clause_text,
            finding=risk.finding,
            explanation=risk.explanation,
            recommendation=risk.recommendation,
            confidence=risk.confidence,
        )
        db.add(finding)
        findings.append(finding)

    await db.flush()

    return findings


@router.get(
    "/{agreement_id}/risks",
    response_model=list[RiskFindingResponse],
)
async def list_risks(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List all risk findings for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    result = await db.execute(
        select(RiskFinding)
        .where(RiskFinding.agreement_id == agreement_id)
        .order_by(RiskFinding.created_at.desc())
    )
    return result.scalars().all()


@router.patch(
    "/{agreement_id}/risks/{risk_id}",
    response_model=RiskFindingResponse,
)
async def update_risk_status(
    agreement_id: uuid.UUID,
    risk_id: uuid.UUID,
    data: ReviewStatusUpdate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Update the review status of a risk finding."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    result = await db.execute(
        select(RiskFinding).where(
            RiskFinding.id == risk_id,
            RiskFinding.agreement_id == agreement_id,
        )
    )
    finding = result.scalar_one_or_none()

    if finding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Risk finding not found",
        )

    finding.reviewer_status = data.reviewer_status
    finding.reviewer_notes = data.reviewer_notes

    await db.flush()
    await db.refresh(finding)

    return finding


@router.post(
    "/{agreement_id}/compare",
    response_model=ComparisonResponse,
    status_code=status.HTTP_200_OK,
)
async def compare_versions(
    agreement_id: uuid.UUID,
    base_version: int,
    compared_version: int,
    _entitled: None = Depends(require_entitlement("ai_analyses")),
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Compare two contract versions using AI.

    Provides detailed change analysis and risk impact assessment.
    """
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    # Get versions
    base_result = await db.execute(
        select(AgreementVersion).where(
            AgreementVersion.agreement_id == agreement_id,
            AgreementVersion.version_number == base_version,
        )
    )
    base = base_result.scalar_one_or_none()

    compared_result = await db.execute(
        select(AgreementVersion).where(
            AgreementVersion.agreement_id == agreement_id,
            AgreementVersion.version_number == compared_version,
        )
    )
    compared = compared_result.scalar_one_or_none()

    if base is None or compared is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or both versions not found",
        )

    # Run AI comparison
    comparison = await ai_service.compare_contracts(
        base_text=base.content,
        compared_text=compared.content,
        base_version=base_version,
        compared_version=compared_version,
    )

    # Store comparison
    record = ContractComparison(
        agreement_id=agreement_id,
        base_version_id=base.id,
        compared_version_id=compared.id,
        summary=comparison.get("summary", ""),
        changes_detected=comparison.get("changes_detected", 0),
        risk_changes=comparison.get("risk_changes"),
        detailed_changes=comparison.get("detailed_changes"),
    )
    db.add(record)
    await db.flush()

    return ComparisonResponse(
        summary=comparison.get("summary", ""),
        changes_detected=comparison.get("changes_detected", 0),
        risk_changes=comparison.get("risk_changes", []),
        detailed_changes=comparison.get("detailed_changes", []),
        coverage=comparison.get("coverage"),
    )
