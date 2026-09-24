"""Signing API endpoints.

Handle internal party signing and auto-transition to EXECUTED.
"""

import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.rbac import require_permission
from app.dependencies.tenant import get_current_organization_id
from app.domain.agreement_states import SIGNABLE_STATES, AgreementStatus
from app.models.agreement import Agreement
from app.models.signature import InternalSignature
from app.models.user import User
from app.services.agreement_versioning import get_latest_version
from app.services.audit_service import record_event
from app.services.alerting_service import (
    Alert,
    AlertSeverity,
    get_alerting_service,
)
from app.services.escalation_service import EscalationLevel, get_escalation_service
from app.services.lifecycle_service import (
    TransitionNotAllowed,
    apply_transition,
)
from app.services.signing_authority_service import evaluate_signing_authority
from app.services.signing_completion import (
    ExecutionBlocked,
    check_and_execute,
    signature_progress,
)

router = APIRouter(
    prefix="/agreements",
    tags=["signing"],
)


class SignRequest(BaseModel):
    consent_text: str


class SignResponse(BaseModel):
    signature_id: uuid.UUID
    signed_at: str
    message: str
    agreement_status: str


class ExecutionResponse(BaseModel):
    status: str
    message: str
    document_hash: str | None = None


async def _record_audit_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agreement_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    actor_type: str,
    action: str,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    metadata: dict | None = None,
    ip_address: str | None = None,
):
    """Record an audit event."""
    return await record_event(
        db,
        tenant_id=tenant_id,
        agreement_id=agreement_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=metadata,
        ip_address=ip_address,
    )


async def _check_and_execute(
    db: AsyncSession,
    agreement: Agreement,
    org_id: uuid.UUID,
) -> bool:
    """Advance SIGNING → PARTIALLY_SIGNED / EXECUTED (spec §67).

    Delegates to the shared signing-completion engine so internal and
    external signatures are judged by the same "all required signers" rule.
    """
    try:
        return await check_and_execute(db, agreement=agreement, org_id=org_id)
    except ExecutionBlocked as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


def _send_esign_alert(
    org_id: str,
    agreement_id: str,
    action: str,
    actor: str,
    executed: bool = False,
    error: str | None = None,
):
    """Dispatch e-signature alerts via Slack/PagerDuty + escalation."""
    alerting = get_alerting_service()
    escalation = get_escalation_service()

    if error:
        severity = AlertSeverity.CRITICAL
        esc_level = EscalationLevel.CRITICAL
        title = f"E-Signature Failed: {action}"
        message = f"{actor} failed to {action} agreement {agreement_id[:8]}...: {error}"
    elif executed:
        severity = AlertSeverity.INFO
        esc_level = EscalationLevel.INFO
        title = f"Agreement Executed: {action}"
        message = f"{actor} signed and executed agreement {agreement_id[:8]}..."
    else:
        severity = AlertSeverity.INFO
        esc_level = EscalationLevel.INFO
        title = f"E-Signature: {action}"
        message = f"{actor} {action} agreement {agreement_id[:8]}..."

    alerting.send_alert(
        Alert(
            title=title,
            message=message,
            severity=severity,
            source="contractos-esignature",
            category="esignature",
            details={
                "agreement_id": agreement_id,
                "action": action,
                "actor": actor,
                "executed": executed,
            },
        )
    )

    if severity in (AlertSeverity.WARNING, AlertSeverity.CRITICAL):
        escalation.create_incident(
            organization_id=org_id,
            category="esignature",
            title=title,
            message=message,
            severity=esc_level,
            details={"agreement_id": agreement_id, "actor": actor},
        )


@router.post(
    "/{agreement_id}/sign",
    response_model=SignResponse,
)
async def sign_agreement(
    agreement_id: uuid.UUID,
    data: SignRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Internal user signs the agreement.

    Captures signature with evidence for audit trail.
    """
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        permission="agreement.sign",
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    if agreement.status not in SIGNABLE_STATES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot sign agreement in status: {agreement.status}",
        )

    # Enforce signatory authority (spec 30): the signer's authority scope must
    # cover the agreement value, or the required DOA approvals must be complete.
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

    # Get latest version
    version = await get_latest_version(db, agreement_id)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No version available to sign",
        )

    # Compute signature hash
    timestamp_str = datetime.now(timezone.utc).isoformat()
    signature_input = f"{current_user.id}:{version.content_hash}:{timestamp_str}"
    signature_hash = hashlib.sha256(signature_input.encode()).hexdigest()

    # Create signature record
    signature = InternalSignature(
        agreement_id=agreement_id,
        user_id=current_user.id,
        version_id=version.id,
        signed_at=datetime.now(timezone.utc),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        consent_text=data.consent_text,
        signature_hash=signature_hash,
    )
    db.add(signature)

    # Update agreement status
    if agreement.status not in (AgreementStatus.SIGNING, AgreementStatus.PARTIALLY_SIGNED):
        try:
            await apply_transition(
                db,
                agreement=agreement,
                action_key="sign",
                actor_id=current_user.id,
                org_id=org_id,
                actor_type="user",
                ip_address=request.client.host if request.client else None,
            )
        except TransitionNotAllowed as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    # Record audit event
    await _record_audit_event(
        db,
        tenant_id=org_id,
        agreement_id=agreement_id,
        actor_id=current_user.id,
        actor_type="user",
        action="SIGNED",
        resource_type="agreement",
        resource_id=agreement_id,
        metadata={
            "signature_hash": signature_hash,
            "version_number": version.version_number,
        },
        ip_address=request.client.host if request.client else None,
    )

    await db.flush()

    # Check if we should auto-execute
    executed = await _check_and_execute(db, agreement, org_id)

    # Dispatch e-signature alerts
    _send_esign_alert(
        org_id=str(org_id),
        agreement_id=str(agreement_id),
        action="signed",
        actor=current_user.email,
        executed=executed,
    )

    return SignResponse(
        signature_id=signature.id,
        signed_at=signature.signed_at.isoformat(),
        message="Agreement signed successfully"
        + (" and executed" if executed else ""),
        agreement_status=agreement.status,
    )


@router.post(
    "/{agreement_id}/send",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("agreement.send"))],
)
async def send_agreement(
    agreement_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Send agreement to counterparty.

    Transitions DRAFT/INTERNAL_REVIEW/APPROVED → SENT.
    """
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    # ABAC check (spec §52): department match + approval limit
    from app.services.abac_service import check_department_match, check_approval_limit

    dept_verdict = await check_department_match(db, user_id=current_user.id, agreement_id=agreement_id)
    limit_verdict = await check_approval_limit(db, user_id=current_user.id, agreement_id=agreement_id)

    # Advisory only — log but don't block (RBAC is the hard gate)
    abac_warnings = []
    if not dept_verdict.allowed:
        abac_warnings.append(dept_verdict.reason)
    if not limit_verdict.allowed:
        abac_warnings.append(limit_verdict.reason)

    if agreement.status not in (
        AgreementStatus.DRAFT,
        AgreementStatus.INTERNAL_REVIEW,
        AgreementStatus.APPROVED,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot send agreement in status: {agreement.status}",
        )

    previous_status = agreement.status
    try:
        await apply_transition(
            db,
            agreement=agreement,
            action_key="send",
            actor_id=current_user.id,
            org_id=org_id,
            actor_type="user",
            ip_address=request.client.host if request.client else None,
        )
    except TransitionNotAllowed as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Record audit event
    await _record_audit_event(
        db,
        tenant_id=org_id,
        agreement_id=agreement_id,
        actor_id=current_user.id,
        actor_type="user",
        action="SENT",
        resource_type="agreement",
        resource_id=agreement_id,
        metadata={"previous_status": previous_status},
    )

    await db.flush()

    # Alert on agreement sent
    _send_esign_alert(
        org_id=str(org_id),
        agreement_id=str(agreement_id),
        action="sent",
        actor=current_user.email,
    )

    return {"status": "sent", "message": "Agreement sent to counterparty"}


@router.get("/{agreement_id}/signature-progress")
async def get_signature_progress(
    agreement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Who has signed and who is still outstanding (spec §67)."""
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    progress = await signature_progress(db, agreement.id)
    return {"agreement_id": str(agreement.id), "status": agreement.status, **progress.to_dict()}
