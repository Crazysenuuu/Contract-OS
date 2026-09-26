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
from app.models.agreement import Agreement, AgreementVersion
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
    get_signoff_readiness,
    lock_for_version_change,
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
        # Spec §24.2: submitting to the approval chain is a real state change.
        # The agreement leaves Draft so the lifecycle column reflects that an
        # approval is pending; a rejection reverts it to Draft, forcing the
        # amended agreement to restart the cycle.
        if record.status in ("pending", "in_progress"):
            agreement_result = await db.execute(
                select(Agreement).where(Agreement.id == agreement_id)
            )
            agreement = agreement_result.scalar_one_or_none()
            if agreement is not None and agreement.status in (
                "draft",
                "internal_review",
                "pending_approval",
            ):
                try:
                    await apply_transition(
                        db,
                        agreement=agreement,
                        action_key="submit_for_approval",
                        actor_id=current_user.id,
                        org_id=org_id,
                        actor_type="user",
                        metadata_json={"via": "approval_start"},
                    )
                except TransitionNotAllowed as e:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Approval started but agreement could not be moved to pending approval: {e}",
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

    # If rejected, cancel the approval and revert the agreement to Draft.
    # Spec §24.2: "If an agreement is rejected or redlined during the
    # approval chain, it must revert to the Draft state, requiring the
    # approval cycle to restart once amended."
    if data.decision == "rejected":
        await cancel_approval(db, record)
        result = await db.execute(select(Agreement).where(Agreement.id == agreement_id))
        agreement = result.scalar_one_or_none()
        if agreement is not None:
            try:
                if agreement.status == "pending_approval":
                    await apply_transition(
                        db,
                        agreement=agreement,
                        action_key="reject",
                        actor_id=current_user.id,
                        org_id=org_id,
                        actor_type="user",
                        metadata_json={"via": "approval_reject"},
                    )
                elif agreement.status == "internal_review":
                    await apply_transition(
                        db,
                        agreement=agreement,
                        action_key="reopen",
                        actor_id=current_user.id,
                        org_id=org_id,
                        actor_type="user",
                        metadata_json={"via": "approval_reject"},
                    )
                elif agreement.status == "draft":
                    pass  # already in Draft; nothing to revert
                else:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"Cannot revert agreement to Draft from status "
                            f"'{agreement.status}' after rejection"
                        ),
                    )
            except TransitionNotAllowed as e:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Approval rejected but agreement could not be reverted to Draft: {e}",
                )
    else:
        # Advance to next stage
        await advance_stage(db, record)

    # When every stage is approved, drive the agreement lifecycle forward:
    # draft -> internal_review -> approved (or pending_approval -> approved).
    # Spec 24: approvals gate signing.
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
                elif agreement.status in ("pending_approval", "internal_review"):
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


# --------------------------------------------------------------------------
# Approval context (spec 2.05 §1-§7): read-only workspace payload. The
# client never decides whether the user may approve — this endpoint
# aggregates agreement, current version, viewer capability, legal-review
# state, workflow step, and negotiation change counts in one response.
# --------------------------------------------------------------------------

@record_router.get(
    "/{agreement_id}/approval-context",
)
async def get_approval_context(
    agreement_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Build the executive approval workspace context (spec 2.05)."""
    from app.models.agreement_access import AgreementParticipant, AgreementParty
    from app.models.approval import ApprovalDecision, ApprovalRecord
    from app.models.legal_entity import LegalEntity
    from app.models.negotiation import AgreementChange, AgreementChangeItem

    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    agreement = (
        await db.execute(select(Agreement).where(Agreement.id == agreement_id))
    ).scalar_one_or_none()
    if agreement is None:
        raise HTTPException(status_code=404, detail="Agreement not found")

    # Viewer capability: explicit AgreementParticipant binding wins; the
    # agreement creator counts as the owning organization's authorized
    # representative for the org party.
    participant = (
        await db.scalar(
            select(AgreementParticipant).where(
                AgreementParticipant.agreement_id == agreement_id,
                AgreementParticipant.user_id == current_user.id,
                AgreementParticipant.status == "active",
            )
        )
    )
    viewer_party_id: UUID | None = None
    viewer_role = "reviewer"
    can_approve = False
    if participant is not None:
        viewer_party_id = participant.agreement_party_id
        viewer_role = participant.participant_role
        can_approve = participant.can_approve
    elif agreement.created_by == current_user.id:
        org_party = (
            await db.scalar(
                select(AgreementParty.id)
                .join(LegalEntity, LegalEntity.id == AgreementParty.legal_entity_id)
                .where(
                    AgreementParty.agreement_id == agreement_id,
                    LegalEntity.organization_id == org_id,
                )
            )
        )
        viewer_party_id = org_party
        viewer_role = "authorized_representative"
        can_approve = org_party is not None

    # Current version (the exact terms under review — spec 2.05 §4 §5).
    version = (
        await db.scalar(
            select(AgreementVersion).where(
                AgreementVersion.agreement_id == agreement_id,
                AgreementVersion.status == "current",
            )
        )
    )

    # Legal review: any legal_review record already fully approved.
    legal_review = {"status": "pending", "confirmed_by": None, "confirmed_at": None}
    legal_record = (
        await db.scalar(
            select(ApprovalRecord).where(
                ApprovalRecord.agreement_id == agreement_id,
                ApprovalRecord.approval_type == "legal_review",
                ApprovalRecord.status.in_(("approved", "completed")),
            )
        )
    )
    if legal_record is not None:
        confirm_decision = (
            await db.scalar(
                select(ApprovalDecision)
                .where(ApprovalDecision.record_id == legal_record.id)
                .order_by(ApprovalDecision.decided_at.desc())
                .limit(1)
            )
        )
        legal_review = {
            "status": "confirmed",
            "confirmed_by": str(confirm_decision.user_id) if confirm_decision else None,
            "confirmed_at": (
                confirm_decision.decided_at.isoformat() if confirm_decision else None
            ),
        }

    # Workflow step label from the lifecycle state.
    step_by_status = {
        "draft": "drafting",
        "internal_review": "legal_review",
        "pending_approval": "party_a_confirmation",
        "negotiation": "party_a_confirmation",
        "approved": "confirmed",
        "sent": "released",
        "ready_for_signature": "released",
        "signing": "released",
    }

    # Negotiation change counts (spec 2.05 §1): aggregate accepted items.
    change_rows = (
        await db.execute(
            select(AgreementChangeItem.change_type)
            .join(AgreementChange, AgreementChange.id == AgreementChangeItem.change_id)
            .where(
                AgreementChange.agreement_id == agreement_id,
                AgreementChange.status == "accepted",
                AgreementChangeItem.status == "accepted",
            )
        )
    ).scalars().all()
    counts = {"added": 0, "modified": 0, "removed": 0, "replace": 0}
    for ct in change_rows:
        key = {"add": "added", "modify": "modified", "remove": "removed"}.get(ct)
        if key is not None:
            counts[key] += 1
        else:
            counts["replace"] += 1

    return {
        "agreement": {
            "id": str(agreement.id),
            "title": agreement.title,
            "status": agreement.status,
        },
        "version": (
            {
                "id": str(version.id),
                "version_number": version.version_number,
                "content_hash": version.content_hash,
            }
            if version is not None
            else None
        ),
        "viewer": {
            "member_id": str(current_user.id),
            "party_id": str(viewer_party_id) if viewer_party_id else None,
            "role": viewer_role,
            "can_approve": can_approve,
        },
        "legal_review": legal_review,
        "workflow": {
            "current_step": step_by_status.get(agreement.status, agreement.status),
        },
        "changes": {k: v for k, v in counts.items() if k != "replace" or v > 0},
    }


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


# --- Sign-off readiness & version locks (spec §3.6.54-56) ---


@record_router.get("/agreements/{agreement_id}/signoff")
async def signoff_readiness(
    agreement_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Sign-off checklist: deterministic gates that must all pass before
    the agreement may proceed to signing (spec §3.6.55)."""
    await verify_agreement_access(
        agreement_id=agreement_id, current_user=current_user, org_id=org_id, db=db
    )
    try:
        return await get_signoff_readiness(db, agreement_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@record_router.post("/agreements/{agreement_id}/version-lock")
async def inspect_version_locks(
    agreement_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Apply/inspect the version-lock state for in-flight approvals.

    Called after a version change: locks decisions on superseded terms.
    Idempotent — re-locking only refreshes timestamps of unlocked records.
    """
    await verify_agreement_access(
        agreement_id=agreement_id, current_user=current_user, org_id=org_id, db=db
    )
    locked = await lock_for_version_change(db, agreement_id)
    await db.commit()
    return {"agreement_id": str(agreement_id), "locked_records": locked}
