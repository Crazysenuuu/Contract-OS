"""Execution evidence API endpoints (spec 1.15 / 2.06).

Signature requests, signer records, execution requirements and the sealed
execution package with verifiable hashes.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.execution import (
    ExecutionRequirement,
    SignatureRequest,
)
from app.models.user import User
from app.services.execution_service import (
    check_execution_requirements,
    create_execution_requirement,
    create_signature_request,
    decline_signature_request,
    get_execution_package,
    list_execution_requirements,
    list_signature_requests,
    mark_request_sent,
    record_signer,
    seal_execution_package,
    satisfy_execution_requirement,
    serialize_package,
    serialize_requirement,
    serialize_request,
    verify_execution_package,
)
from app.services.signing_authority_service import evaluate_signing_authority

router = APIRouter(
    prefix="/agreements",
    tags=["execution"],
)


# --------------------------------------------------------------------------
# Signature requests
# --------------------------------------------------------------------------

class SignatureRequestCreate(BaseModel):
    name: str
    email: str
    version_id: uuid.UUID
    party_id: uuid.UUID | None = None
    role: str = "signer"
    signer_type: str = "external"
    expires_at: datetime | None = None
    metadata_json: dict | None = None


class SignatureRequestSend(BaseModel):
    pass


class SignatureRequestDecline(BaseModel):
    reason: str | None = None


class SignerCreate(BaseModel):
    name: str
    email: str
    consent_text: str
    identity_verified: bool = False
    identity_method: str | None = None


@router.post(
    "/{agreement_id}/signature-requests",
    status_code=status.HTTP_201_CREATED,
)
async def create_signature_request_endpoint(
    agreement_id: uuid.UUID,
    data: SignatureRequestCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a signature request for a specific person/version."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    request = await create_signature_request(
        db,
        tenant_id=org_id,
        agreement_id=agreement_id,
        version_id=data.version_id,
        name=data.name,
        email=data.email,
        party_id=data.party_id,
        role=data.role,
        signer_type=data.signer_type,
        expires_at=data.expires_at,
        created_by=current_user.id,
        metadata_json=data.metadata_json,
    )
    return serialize_request(request)


@router.get("/{agreement_id}/signature-requests")
async def list_signature_requests_endpoint(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    requests = await list_signature_requests(db, agreement_id)
    return [serialize_request(r) for r in requests]


async def _get_request_or_404(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    request_id: uuid.UUID,
) -> SignatureRequest:
    result = await db.execute(
        select(SignatureRequest).where(
            SignatureRequest.id == request_id,
            SignatureRequest.agreement_id == agreement_id,
        )
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signature request not found",
        )
    return req


@router.post("/{agreement_id}/signature-requests/{request_id}/send")
async def send_signature_request_endpoint(
    agreement_id: uuid.UUID,
    request_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    req = await _get_request_or_404(db, agreement_id, request_id)
    try:
        req = await mark_request_sent(
            db, org_id, req, actor_id=current_user.id
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return serialize_request(req)


@router.post("/{agreement_id}/signature-requests/{request_id}/decline")
async def decline_signature_request_endpoint(
    agreement_id: uuid.UUID,
    request_id: uuid.UUID,
    data: SignatureRequestDecline,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    req = await _get_request_or_404(db, agreement_id, request_id)
    try:
        req = await decline_signature_request(
            db, org_id, req, actor_id=current_user.id, reason=data.reason
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return serialize_request(req)


@router.post("/{agreement_id}/signature-requests/{request_id}/sign")
async def sign_signature_request_endpoint(
    agreement_id: uuid.UUID,
    request_id: uuid.UUID,
    data: SignerCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Record a signature for a signature request (internal user)."""
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    # Enforce signatory authority for internal signers on signature requests.
    authority = await evaluate_signing_authority(
        db,
        org_id=org_id,
        agreement=agreement,
        user_id=current_user.id,
    )
    if not authority.allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "signing_authority_denied",
                "reason": authority.reason,
                "message": authority.message,
                "required_approvals": authority.required_approvals,
            },
        )

    req = await _get_request_or_404(db, agreement_id, request_id)
    if req.status in ("signed", "declined", "cancelled"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot sign signature request in status: {req.status}",
        )
    record = await record_signer(
        db,
        tenant_id=org_id,
        signature_request=req,
        name=data.name or current_user.name,
        email=data.email or current_user.email,
        consent_text=data.consent_text,
        signer_type="internal",
        user_id=current_user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        identity_verified=data.identity_verified,
        identity_method=data.identity_method,
    )
    return {
        "signature_id": str(record.id),
        "signature_hash": record.signature_hash,
        "signed_at": record.signed_at.isoformat(),
        "request_status": req.status,
    }


# --------------------------------------------------------------------------
# Step-up authentication (OTP) for signer identity verification (spec 24.4)
# --------------------------------------------------------------------------

class OTPIssueRequest(BaseModel):
    channel: str = "email"


class OTPVerifyRequest(BaseModel):
    code: str


@router.post(
    "/{agreement_id}/signature-requests/{request_id}/otp/issue",
    status_code=status.HTTP_201_CREATED,
)
async def issue_signer_otp(
    agreement_id: uuid.UUID,
    request_id: uuid.UUID,
    data: OTPIssueRequest,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Issue a one-time code to the signer for step-up verification.

    The signer must present this code before their signature is recorded
    when the agreement requires identity_verification.
    """
    from app.services.otp_service import issue_otp

    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    req = await _get_request_or_404(db, agreement_id, request_id)
    if data.channel not in {"email", "sms"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="channel must be 'email' or 'sms'",
        )

    result = await issue_otp(
        db,
        organization_id=org_id,
        signature_request_id=request_id,
        channel=data.channel,
        email=req.email,
    )
    await db.commit()
    return result


@router.post(
    "/{agreement_id}/signature-requests/{request_id}/otp/verify",
    status_code=status.HTTP_200_OK,
)
async def verify_signer_otp(
    agreement_id: uuid.UUID,
    request_id: uuid.UUID,
    challenge_id: uuid.UUID,
    data: OTPVerifyRequest,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Verify the signer's one-time code."""
    from app.services.otp_service import OTPError, verify_otp

    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    await _get_request_or_404(db, agreement_id, request_id)

    try:
        challenge = await verify_otp(
            db,
            challenge_id=challenge_id,
            code=data.code,
        )
    except OTPError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    await db.commit()
    return {
        "verified": True,
        "challenge_id": str(challenge.id),
        "verified_at": challenge.verified_at.isoformat() if challenge.verified_at else None,
        "identity_method": "otp",
    }


# --------------------------------------------------------------------------
# Execution requirements
# --------------------------------------------------------------------------

class ExecutionRequirementCreate(BaseModel):
    requirement_type: str
    description: str
    severity: str = "required"
    metadata_json: dict | None = None


class RequirementSatisfy(BaseModel):
    status: str = "satisfied"
    metadata_json: dict | None = None


@router.get("/{agreement_id}/execution/requirements")
async def list_execution_requirements_endpoint(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    requirements = await list_execution_requirements(db, agreement_id)
    return [serialize_requirement(r) for r in requirements]


@router.get(
    "/{agreement_id}/execution/requirements/check",
    status_code=status.HTTP_200_OK,
)
async def check_execution_requirements_endpoint(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Compute execution readiness for the agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    return await check_execution_requirements(db, agreement_id)


@router.post(
    "/{agreement_id}/execution/requirements",
    status_code=status.HTTP_201_CREATED,
)
async def create_execution_requirement_endpoint(
    agreement_id: uuid.UUID,
    data: ExecutionRequirementCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Attach an execution requirement to an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    requirement = await create_execution_requirement(
        db,
        tenant_id=org_id,
        agreement_id=agreement_id,
        requirement_type=data.requirement_type,
        description=data.description,
        severity=data.severity,
        created_by=current_user.id,
        metadata_json=data.metadata_json,
    )
    return serialize_requirement(requirement)


@router.post(
    "/{agreement_id}/execution/requirements/{requirement_id}/satisfy",
)
async def satisfy_execution_requirement_endpoint(
    agreement_id: uuid.UUID,
    requirement_id: uuid.UUID,
    data: RequirementSatisfy,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    result = await db.execute(
        select(ExecutionRequirement).where(
            ExecutionRequirement.id == requirement_id,
            (
                (ExecutionRequirement.agreement_id == agreement_id)
                | (ExecutionRequirement.agreement_id.is_(None))
            ),
        )
    )
    requirement = result.scalar_one_or_none()
    if requirement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution requirement not found",
        )
    requirement = await satisfy_execution_requirement(
        db,
        tenant_id=org_id,
        requirement=requirement,
        actor_id=current_user.id,
        status_value=data.status,
        metadata_json=data.metadata_json,
    )
    return serialize_requirement(requirement)


# --------------------------------------------------------------------------
# Execution package
# --------------------------------------------------------------------------

class PackageSeal(BaseModel):
    version_id: uuid.UUID
    final_document_hash: str
    metadata_json: dict | None = None


@router.post("/{agreement_id}/execution/package/seal")
async def seal_execution_package_endpoint(
    agreement_id: uuid.UUID,
    data: PackageSeal,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Seal the execution package for the agreement's final version."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    try:
        package = await seal_execution_package(
            db,
            tenant_id=org_id,
            agreement_id=agreement_id,
            version_id=data.version_id,
            final_document_hash=data.final_document_hash,
            sealed_by=current_user.id,
            metadata_json=data.metadata_json,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return serialize_package(package)


@router.get("/{agreement_id}/execution/package")
async def get_execution_package_endpoint(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    package = await get_execution_package(db, agreement_id)
    if package is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No execution package for this agreement",
        )
    return serialize_package(package)


@router.post("/{agreement_id}/execution/package/verify")
async def verify_execution_package_endpoint(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Verify the sealed execution package's hashes."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    package = await get_execution_package(db, agreement_id)
    if package is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No execution package for this agreement",
        )
    return await verify_execution_package(db, package)