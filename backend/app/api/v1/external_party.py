"""External party API endpoints.

Manage counterparty access via tokenized review links.
No account required for the other party.
"""

import hashlib
import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.domain.agreement_states import (
    COUNTERPARTY_ACTIVE_STATES,
    EDITABLE_STATES,
    AgreementStatus,
)
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement, AgreementVersion
from app.models.external_party import ExternalParty
from app.models.user import User
from app.services.agreement_versioning import get_latest_version, list_versions
from app.services.external_party_service import (
    accept_agreement,
    add_external_comment,
    capture_signature,
    complete_id_verification,
    create_external_party,
    get_external_parties_for_agreement,
    get_external_party_comments,
    record_session,
    reject_agreement,
    start_id_verification,
    validate_access_token,
)
from app.services.lifecycle_service import TransitionNotAllowed
from app.services.signing_completion import ExecutionBlocked, check_and_execute

router = APIRouter(tags=["external-party"])

# --- Internal endpoints (authenticated) ---


class ExternalPartyCreate(BaseModel):
    agreement_party_id: UUID
    company_name: str
    signatory_name: str
    signatory_email: str
    signatory_title: str | None = None
    can_comment: bool = True
    can_propose_changes: bool = True
    can_accept: bool = True
    can_sign: bool = True


class ExternalPartyResponse(BaseModel):
    id: UUID
    agreement_id: UUID
    company_name: str
    signatory_name: str
    signatory_email: str
    signatory_title: str | None
    status: str
    access_token: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- External endpoints (token-based auth) ---


class ReviewAgreementResponse(BaseModel):
    agreement_id: UUID
    title: str
    company_name: str
    signatory_name: str
    version_number: int
    content: str
    status: str


class CommentCreate(BaseModel):
    content: str
    clause_identifier: str | None = None


class CommentResponse(BaseModel):
    id: UUID
    clause_identifier: str | None
    content: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AcceptResponse(BaseModel):
    status: str
    message: str


class SignRequest(BaseModel):
    consent_text: str


class SignResponse(BaseModel):
    signature_id: UUID
    signed_at: str
    message: str


# --- Internal Endpoints ---


agreement_router = APIRouter(prefix="/agreements", tags=["external-party-internal"])


@agreement_router.get(
    "/{agreement_id}/external-parties",
    response_model=list[ExternalPartyResponse],
)
async def list_external_parties(
    agreement_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List all external parties for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    parties = await get_external_parties_for_agreement(db, agreement_id)
    return parties


@agreement_router.post(
    "/{agreement_id}/external-parties",
    response_model=ExternalPartyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_external_party_endpoint(
    agreement_id: UUID,
    data: ExternalPartyCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Add an external party with tokenized review link.

    Returns the access_token that should be embedded in the review link.
    """
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    if agreement.status not in EDITABLE_STATES | {AgreementStatus.SENT, AgreementStatus.VIEWED}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot add external parties in current agreement status",
        )

    party = await create_external_party(
        db=db,
        agreement_id=agreement_id,
        agreement_party_id=data.agreement_party_id,
        company_name=data.company_name,
        signatory_name=data.signatory_name,
        signatory_email=data.signatory_email,
        signatory_title=data.signatory_title,
        can_comment=data.can_comment,
        can_propose_changes=data.can_propose_changes,
        can_accept=data.can_accept,
        can_sign=data.can_sign,
    )

    return party


# --- External Endpoints (Token-Based) ---


@router.get(
    "/review/{token}",
    response_model=ReviewAgreementResponse,
)
async def review_agreement(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Fetch agreement for counterparty view.

    Transitions status from SENT → VIEWED if applicable.
    """
    external_party = await validate_access_token(db, token)
    if external_party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired access link",
        )

    # Record view session
    await record_session(
        db=db,
        external_party_id=external_party.id,
        action="viewed",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    # Update status to viewed
    if external_party.status in ("pending", "invited"):
        external_party.status = "viewed"
        await db.flush()

    # Drive the agreement lifecycle to VIEWED so the counterparty's open of
    # the shared document is reflected in the authoritative state machine.
    agreement = await db.get(Agreement, external_party.agreement_id)
    if agreement is not None and agreement.status in COUNTERPARTY_ACTIVE_STATES:
        try:
            from app.services.workflow_engine import WorkflowEngine
            from app.models.user import User

            await WorkflowEngine().transition(
                db,
                agreement_id=agreement.id,
                action_key="counterparty_views",
                actor_id=uuid.UUID(int=0),
                actor_type="external",
                tenant_id=agreement.organization_id or uuid.UUID(int=0),
                metadata={"source": "external_review"},
            )
        except HTTPException:
            # A view is non-destructive; if the workflow rejects the move we
            # do not fail the read.
            pass

    # Get the latest version
    version = await get_latest_version(db, external_party.agreement_id)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No versions available for review",
        )

    # Get agreement details
    agreement = await db.get(Agreement, external_party.agreement_id)
    if agreement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agreement not found",
        )

    return ReviewAgreementResponse(
        agreement_id=agreement.id,
        title=agreement.title,
        company_name=external_party.company_name,
        signatory_name=external_party.signatory_name,
        version_number=version.version_number,
        content=version.content,
        status=external_party.status,
    )


@router.get(
    "/review/{token}/versions",
)
async def list_versions_external(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """List available versions for external party review."""
    external_party = await validate_access_token(db, token)
    if external_party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired access link",
        )

    versions = await list_versions(db, external_party.agreement_id)

    return [
        {
            "version_number": v.version_number,
            "status": v.status,
            "content_hash": v.content_hash,
            "created_at": v.created_at.isoformat(),
        }
        for v in versions
    ]


@router.post(
    "/review/{token}/comment",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_comment_external(
    token: str,
    data: CommentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Add a comment from external party."""
    external_party = await validate_access_token(db, token)
    if external_party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired access link",
        )

    if not external_party.can_comment:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Commenting is not enabled for this review",
        )

    # Record session
    await record_session(
        db=db,
        external_party_id=external_party.id,
        action="commented",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    comment = await add_external_comment(
        db=db,
        external_party_id=external_party.id,
        content=data.content,
        clause_identifier=data.clause_identifier,
    )

    return comment


@router.get(
    "/review/{token}/comments",
    response_model=list[CommentResponse],
)
async def list_comments_external(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """List comments from this external party."""
    external_party = await validate_access_token(db, token)
    if external_party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired access link",
        )

    comments = await get_external_party_comments(db, external_party.id)
    return comments


class IDVerificationStartResponse(BaseModel):
    challenge_id: str
    expires_at: str
    debug_code: str | None = None  # dev/log path only, same as native signing


class IDVerificationCompleteRequest(BaseModel):
    challenge_id: str
    code: str


@router.post(
    "/review/{token}/verify-id/start",
    response_model=IDVerificationStartResponse,
)
async def start_id_verification_endpoint(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Begin guest ID verification: email an OTP to the signatory."""
    external_party = await validate_access_token(db, token)
    if external_party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired access link",
        )

    try:
        challenge = await start_id_verification(db, external_party)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    await db.commit()
    return challenge


@router.post("/review/{token}/verify-id/complete")
async def complete_id_verification_endpoint(
    token: str,
    data: IDVerificationCompleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Complete guest ID verification with the emailed OTP code."""
    external_party = await validate_access_token(db, token)
    if external_party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired access link",
        )

    try:
        challenge_id = uuid.UUID(data.challenge_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed challenge id",
        )

    try:
        await complete_id_verification(
            db,
            external_party,
            challenge_id=challenge_id,
            code=data.code,
        )
    except Exception as e:
        await db.rollback()
        code = str(e)
        if "expired" in code.lower() or "attempt" in code.lower() or "invalid" in code.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=code,
            )
        raise
    await db.commit()
    return {"verified": True, "method": "otp_email"}


@router.post(
    "/review/{token}/accept",
    response_model=AcceptResponse,
)
async def accept_external(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """External party accepts the agreement."""
    external_party = await validate_access_token(db, token)
    if external_party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired access link",
        )

    if not external_party.can_accept:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceptance is not enabled for this review",
        )

    if external_party.requires_id_verification and not external_party.id_verified_at:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ID verification required before accepting",
            headers={"X-ID-Verification-Required": "true"},
        )

    # Record session
    await record_session(
        db=db,
        external_party_id=external_party.id,
        action="accepted",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    await accept_agreement(db, external_party)

    return AcceptResponse(
        status="accepted",
        message="Agreement accepted successfully",
    )


@router.post(
    "/review/{token}/reject",
    response_model=AcceptResponse,
)
async def reject_external(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """External party rejects the agreement."""
    external_party = await validate_access_token(db, token)
    if external_party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired access link",
        )

    # Record session
    await record_session(
        db=db,
        external_party_id=external_party.id,
        action="rejected",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    await reject_agreement(db, external_party)

    return AcceptResponse(
        status="rejected",
        message="Agreement rejected",
    )


@router.post(
    "/review/{token}/sign",
    response_model=SignResponse,
)
async def sign_external(
    token: str,
    data: SignRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """External party signs the agreement.

    Captures signature with evidence for audit trail.
    """
    external_party = await validate_access_token(db, token)
    if external_party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired access link",
        )

    if not external_party.can_sign:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signing is not enabled for this review",
        )

    if external_party.requires_id_verification and not external_party.id_verified_at:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ID verification required before signing",
            headers={"X-ID-Verification-Required": "true"},
        )

    # Get latest version
    version = await get_latest_version(db, external_party.agreement_id)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No version available to sign",
        )

    # Record session
    await record_session(
        db=db,
        external_party_id=external_party.id,
        action="signed",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    # Capture signature
    signature = await capture_signature(
        db=db,
        external_party=external_party,
        agreement_id=external_party.agreement_id,
        version_id=version.id,
        consent_text=data.consent_text,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    # Spec §67: EXECUTED only once *all* required signers have completed.
    # External signatures must drive the same completion engine as internal
    # ones, otherwise a counterparty-last signing order never executes.
    executed = False
    agreement = await db.get(Agreement, external_party.agreement_id)
    if agreement is not None:
        try:
            executed = await check_and_execute(
                db, agreement=agreement, org_id=agreement.organization_id
            )
        except (ExecutionBlocked, TransitionNotAllowed):
            # The signature itself is valid and recorded; a lifecycle
            # refusal must not be surfaced to the guest signer. Anything
            # else is a programming error and should propagate.
            executed = False

    return SignResponse(
        signature_id=signature.id,
        signed_at=signature.signed_at.isoformat(),
        message="Agreement signed successfully" + (" and executed" if executed else ""),
    )
