"""Approval API endpoints.

Manage multi-stage approval workflows for agreements.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement
from app.models.user import User
from app.services.approval_engine import (
    advance_stage,
    cancel_approval,
    create_approval_definition,
    get_approval_definition,
    get_approval_definitions,
    get_approval_for_agreement,
    get_approval_record,
    get_pending_approvals_for_user,
    record_decision,
    resolve_and_start_approval,
    start_approval,
)
from app.services.lifecycle_service import (
    TransitionNotAllowed,
    apply_transition,
)

router = APIRouter(tags=["approvals"])


# --- Schemas ---


class ApprovalStageCreate(BaseModel):
    name: str
    order: int
    required_role: str | None = None
    require_all_approvers: bool = False


class ApprovalDefinitionCreate(BaseModel):
    name: str
    description: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    stages: list[ApprovalStageCreate] | None = None


class ApprovalDefinitionResponse(BaseModel):
    id: UUID
    organization_id: UUID
    name: str
    description: str | None
    is_active: bool
    min_value: float | None
    max_value: float | None
    created_at: datetime | str

    model_config = {"from_attributes": True}


class StartApprovalRequest(BaseModel):
    agreement_id: UUID
    # Optional: when omitted, the dynamic rules engine (spec 24.2) resolves
    # the definition from the agreement's metadata; the DOA matrix is the
    # fallback. An explicit id always wins.
    definition_id: UUID | None = None
    approval_type: str = "legal_review"  # 'legal_review' | 'party_approval'


class ApprovalRecordResponse(BaseModel):
    id: UUID
    agreement_id: UUID
    definition_id: UUID
    current_stage_id: UUID | None
    status: str
    agreement_version_id: UUID | None
    approval_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ApprovalStartResponse(ApprovalRecordResponse):
    """Approval start result plus routing transparency (spec 24.2)."""

    # Which definition matched and why: {'source': 'explicit'|'rules_engine'
    # |'database'|'fallback', 'definition_id': ..., 'matched_rule': ...}
    routing: dict | None = None


class DecisionRequest(BaseModel):
    decision: str  # 'approved', 'rejected', 'escalated'
    comment: str | None = None
    # Step-up authentication (spec 2.05.18): a valid TOTP code is required
    # when the approver has MFA enabled. Approval is a legally binding
    # action, so it demands more than a bearer token.
    mfa_code: str | None = None


class DecisionResponse(BaseModel):
    id: UUID
    record_id: UUID
    stage_id: UUID
    user_id: UUID
    decision: str
    comment: str | None
    decided_at: datetime

    model_config = {"from_attributes": True}


# --- Definition Endpoints ---


definition_router = APIRouter(
    prefix="/approval-definitions",
    tags=["approval-definitions"],
)


@definition_router.get(
    "",
    response_model=list[ApprovalDefinitionResponse],
)
async def list_definitions(
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List all active approval definitions for the organization."""
    return await get_approval_definitions(db, org_id)


@definition_router.post(
    "",
    response_model=ApprovalDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_definition(
    data: ApprovalDefinitionCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Create an approval workflow definition."""
    definition = await create_approval_definition(
        db=db,
        organization_id=org_id,
        name=data.name,
        description=data.description,
        min_value=data.min_value,
        max_value=data.max_value,
        stages=[s.model_dump() for s in data.stages] if data.stages else None,
    )
    return definition


@definition_router.get(
    "/{definition_id}",
    response_model=ApprovalDefinitionResponse,
)
async def get_definition(
    definition_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get an approval definition by ID."""
    definition = await get_approval_definition(db, definition_id)
    if definition is None or definition.organization_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval definition not found",
        )
    return definition


# --- Approval Record Endpoints ---


record_router = APIRouter(
    prefix="/agreements",
    tags=["approval-records"],
)


@record_router.post(
    "/{agreement_id}/approvals/start",
    response_model=ApprovalStartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_approval_endpoint(
    agreement_id: UUID,
    data: StartApprovalRequest,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Start an approval process for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    if data.approval_type not in ("legal_review", "party_approval"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown approval_type: {data.approval_type}",
        )

    try:
        record, resolution = await resolve_and_start_approval(
            db=db,
            agreement_id=agreement_id,
            organization_id=org_id,
            approval_type=data.approval_type,
            definition_id=data.definition_id,
        )
        # Routing transparency: which definition matched and why (rule
        # conditions or DOA matrix) — lets the UI show "Routed by: value
        # > 1M rule" instead of a black box.
        base = ApprovalRecordResponse.model_validate(record)
        return ApprovalStartResponse(**base.model_dump(mode="json"), routing=resolution)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@record_router.get(
    "/{agreement_id}/approvals",
    response_model=ApprovalRecordResponse,
)
async def get_approval_status(
    agreement_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get the current approval status for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    record = await get_approval_for_agreement(db, agreement_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active approval found",
        )

    return record


@record_router.post(
    "/{agreement_id}/approvals/{record_id}/decide",
    response_model=DecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def make_decision(
    agreement_id: UUID,
    record_id: UUID,
    data: DecisionRequest,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Make an approval/rejection decision."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    # Step-up authentication (spec 2.05.18): MFA-enabled approvers must
    # present a fresh TOTP code with the decision. The response advertises
    # the requirement via 403 + 'WWW-Authenticate: Bearer mfa_required' so
    # the frontend can render the step-up dialog.
    if current_user.mfa_enabled:
        if not data.mfa_code:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="MFA code required for approval decisions",
                headers={"WWW-Authenticate": 'Bearer error="mfa_required"'},
            )
        if not current_user.mfa_secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="MFA is enabled but no secret is configured",
            )
        import pyotp

        totp = pyotp.TOTP(current_user.mfa_secret)
        if not totp.verify(data.mfa_code, valid_window=1):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid MFA code",
                headers={"WWW-Authenticate": 'Bearer error="mfa_required"'},
            )

    record = await get_approval_record(db, record_id)
    if record is None or record.agreement_id != agreement_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval record not found",
        )

    # Version-bound approvals (spec 1.2): a decision is only valid while the
    # version the approval reviewed is still the current one. If a new
    # version has since become current the approval is stale and must be
    # restarted against the new terms.
    from app.models.agreement import AgreementVersion

    current_result = await db.execute(
        select(AgreementVersion).where(
            AgreementVersion.agreement_id == agreement_id,
            AgreementVersion.status == "current",
        )
    )
    current_version = current_result.scalar_one_or_none()
    if (
        record.agreement_version_id is not None
        and current_version is not None
        and record.agreement_version_id != current_version.id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agreement version changed since approval started; restart the approval",
        )

    if record.status != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot make decision on approval with status: {record.status}",
        )

    if record.current_stage_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No current stage to decide on",
        )

    # Record the decision
    decision = await record_decision(
        db=db,
        record_id=record_id,
        stage_id=record.current_stage_id,
        user_id=current_user.id,
        decision=data.decision,
        comment=data.comment,
    )

    # If rejected, cancel the approval
    if data.decision == "rejected":
        await cancel_approval(db, record)
    else:
        # Advance to next stage
        await advance_stage(db, record)

    # When every stage is approved, drive the agreement lifecycle forward:
    # draft -> internal_review -> approved. Spec 24: approvals gate signing.
    if record.status == "approved":
        result = await db.execute(select(Agreement).where(Agreement.id == agreement_id))
        agreement = result.scalar_one_or_none()
        if agreement is not None:
            try:
                if agreement.status == "draft":
                    await apply_transition(
                        db,
                        agreement=agreement,
                        action_key="submit",
                        actor_id=current_user.id,
                        org_id=org_id,
                        actor_type="user",
                    )
                await apply_transition(
                    db,
                    agreement=agreement,
                    action_key="approve",
                    actor_id=current_user.id,
                    org_id=org_id,
                    actor_type="user",
                )
            except TransitionNotAllowed as e:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Approvals complete but agreement could not be advanced: {e}",
                )

    return decision


@record_router.post(
    "/{agreement_id}/approvals/{record_id}/cancel",
    response_model=ApprovalRecordResponse,
)
async def cancel_approval_endpoint(
    agreement_id: UUID,
    record_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Cancel an approval process."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    record = await get_approval_record(db, record_id)
    if record is None or record.agreement_id != agreement_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval record not found",
        )

    return await cancel_approval(db, record)


# --- Pending Approvals ---


pending_router = APIRouter(
    prefix="/approvals",
    tags=["pending-approvals"],
)


@pending_router.get(
    "/pending",
)
async def list_pending_approvals(
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List all agreements pending approval for the current user."""
    return await get_pending_approvals_for_user(db, current_user.id, org_id)
