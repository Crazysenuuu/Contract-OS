"""
Company Policy & Compliance API Endpoints.

Manage company policies and run compliance checks on contracts.
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.company_policy import (
    CompanyPolicy,
    ComplianceReport,
    PolicyViolation,
)
from app.models.user import User
from app.services.compliance_service import ComplianceService

router = APIRouter(prefix="/policies", tags=["Policies"])


# --- Schemas ---

class PolicyCreate(BaseModel):
    name: str
    description: Optional[str] = None
    category: str
    clause_type: str  # 'required', 'prohibited', 'standard', 'minimum', 'maximum'
    standard_text: Optional[str] = None
    keywords: Optional[list[str]] = None
    rules: Optional[dict] = None
    severity_if_missing: str = "medium"
    is_active: bool = True
    applies_to_types: Optional[list[str]] = None
    priority: int = 50


class PolicyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    clause_type: Optional[str] = None
    standard_text: Optional[str] = None
    keywords: Optional[list[str]] = None
    rules: Optional[dict] = None
    severity_if_missing: Optional[str] = None
    is_active: Optional[bool] = None
    applies_to_types: Optional[list[str]] = None
    priority: Optional[int] = None


class PolicyResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: Optional[str]
    category: str
    clause_type: str
    standard_text: Optional[str]
    keywords: Optional[list]
    rules: Optional[dict]
    severity_if_missing: str
    is_active: bool
    applies_to_types: Optional[list]
    priority: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ViolationStatusUpdate(BaseModel):
    reviewer_status: str  # 'accepted', 'rejected', 'mitigated', 'exempt'
    reviewer_notes: Optional[str] = None


class ViolationResponse(BaseModel):
    id: uuid.UUID
    agreement_id: uuid.UUID
    policy_id: uuid.UUID
    violation_type: str
    description: str
    found_text: Optional[str]
    expected_text: Optional[str]
    found_value: Optional[str]
    expected_value: Optional[str]
    severity: str
    confidence: float
    reviewer_status: str
    reviewer_notes: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class ComplianceReportResponse(BaseModel):
    id: uuid.UUID
    agreement_id: uuid.UUID
    version_id: Optional[uuid.UUID]
    total_policies_checked: int
    violations_found: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    compliance_score: float
    summary: Optional[str]
    checked_by: Optional[uuid.UUID]
    created_at: datetime

    model_config = {"from_attributes": True}


class ComplianceCheckResult(BaseModel):
    report_id: str
    compliance_score: float
    policies_checked: int
    violations_found: int
    critical: int
    high: int
    medium: int
    low: int
    summary: str
    violations: list[dict]


# --- Policy CRUD ---

@router.get("", response_model=list[PolicyResponse])
async def list_policies(
    category: Optional[str] = None,
    is_active: Optional[bool] = None,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List company policies."""
    query = select(CompanyPolicy).where(
        CompanyPolicy.organization_id == org_id
    )

    if category:
        query = query.where(CompanyPolicy.category == category)
    if is_active is not None:
        query = query.where(CompanyPolicy.is_active == is_active)

    query = query.order_by(CompanyPolicy.priority.desc(), CompanyPolicy.name)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=PolicyResponse, status_code=status.HTTP_201_CREATED)
async def create_policy(
    data: PolicyCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a new company policy."""
    policy = CompanyPolicy(
        organization_id=org_id,
        name=data.name,
        description=data.description,
        category=data.category,
        clause_type=data.clause_type,
        standard_text=data.standard_text,
        keywords=data.keywords,
        rules=data.rules,
        severity_if_missing=data.severity_if_missing,
        is_active=data.is_active,
        applies_to_types=data.applies_to_types,
        priority=data.priority,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy


@router.get("/{policy_id}", response_model=PolicyResponse)
async def get_policy(
    policy_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific policy."""
    result = await db.execute(
        select(CompanyPolicy).where(
            CompanyPolicy.id == policy_id,
            CompanyPolicy.organization_id == org_id,
        )
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


@router.patch("/{policy_id}", response_model=PolicyResponse)
async def update_policy(
    policy_id: uuid.UUID,
    data: PolicyUpdate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Update a policy."""
    result = await db.execute(
        select(CompanyPolicy).where(
            CompanyPolicy.id == policy_id,
            CompanyPolicy.organization_id == org_id,
        )
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(policy, field, value)

    await db.commit()
    await db.refresh(policy)
    return policy


@router.delete("/{policy_id}")
async def delete_policy(
    policy_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Delete a policy."""
    result = await db.execute(
        select(CompanyPolicy).where(
            CompanyPolicy.id == policy_id,
            CompanyPolicy.organization_id == org_id,
        )
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    await db.delete(policy)
    await db.commit()
    return {"deleted": True}


# --- Compliance Check ---

agreement_router = APIRouter(prefix="/agreements", tags=["Compliance"])


@agreement_router.post(
    "/{agreement_id}/compliance/check",
    response_model=ComplianceCheckResult,
)
async def run_compliance_check(
    agreement_id: uuid.UUID,
    version_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Run compliance check on an agreement against all active policies."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    service = ComplianceService(db)
    try:
        result = await service.check_compliance(
            agreement_id=agreement_id,
            organization_id=org_id,
            version_id=version_id,
            checked_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()
    return result


@agreement_router.get(
    "/{agreement_id}/compliance/reports",
    response_model=list[ComplianceReportResponse],
)
async def list_compliance_reports(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List compliance reports for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    service = ComplianceService(db)
    return await service.get_reports(agreement_id)


@agreement_router.get(
    "/{agreement_id}/compliance/violations",
    response_model=list[ViolationResponse],
)
async def list_compliance_violations(
    agreement_id: uuid.UUID,
    severity: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List compliance violations for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    service = ComplianceService(db)
    return await service.get_violations(agreement_id, severity)


@agreement_router.patch(
    "/{agreement_id}/compliance/violations/{violation_id}",
    response_model=ViolationResponse,
)
async def update_violation_status(
    agreement_id: uuid.UUID,
    violation_id: uuid.UUID,
    data: ViolationStatusUpdate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Update the review status of a compliance violation."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    service = ComplianceService(db)
    violation = await service.update_violation_status(
        violation_id=violation_id,
        status=data.reviewer_status,
        notes=data.reviewer_notes,
        reviewed_by=current_user.id,
    )

    if not violation:
        raise HTTPException(status_code=404, detail="Violation not found")

    await db.commit()
    return violation
