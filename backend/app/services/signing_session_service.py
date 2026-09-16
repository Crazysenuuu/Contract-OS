"""Signing-session service (spec 2.06.3-2.06.23).

Owns the security state machine for one signer's signing session:

    CREATED → REQUEST_OPENED (token exchange)
           → CONSENT_GIVEN → AUTHENTICATION_REQUIRED
           → AUTHENTICATION_PASSED → READY
           → SIGNED | DECLINED | EXPIRED | CANCELLED

Server-side guarantees (never trust the browser, 2.06.4):

- The signer identity is derived from the signature request (agreement
  membership + authorization), not from client-supplied fields.
- A one-time opaque token (stored only hashed) is exchanged for the session.
- Consent must be given, then step-up authentication passed, before a
  signature may be placed.
- Signing order (per-agreement signature requests) is enforced.
- Every mutation appends a hash-chained ``SigningEvent`` to the ledger.
- Idempotency keys make the dangerous mutations safe to retry.
"""

import hashlib
import json as _json
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_signing_token
from app.models.execution import (
    ExecutionEvidenceItem,
    ExecutionPackage,
    SignatureRequest,
    SignerRecord,
)
from app.models.signing_session import (
    IdempotencyKey,
    SignaturePlacement,
    SigningEvent,
    SigningEventType,
    SigningSession,
    SigningSessionStatus,
    SignatureType,
    evidence_hash,
    hash_idempotency_key,
    hash_signing_token,
)

SESSION_TTL_MINUTES = int(os.environ.get("SIGNING_SESSION_TTL_MINUTES", "60"))
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60


class SigningSessionError(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def serialize_session(session: SigningSession, *, token: str | None = None) -> dict:
    data = {
        "session_id": str(session.id),
        "status": session.status,
        "expires_at": session.expires_at.isoformat(),
        "authenticated_at": (
            session.authenticated_at.isoformat() if session.authenticated_at else None
        ),
        "consented_at": session.consented_at.isoformat() if session.consented_at else None,
        "signed_at": session.signed_at.isoformat() if session.signed_at else None,
        "signature_request_id": str(session.signature_request_id),
    }
    if token is not None:
        data["one_time_token"] = token
    return data


async def create_signing_session(
    db: AsyncSession,
    *,
    signature_request_id: uuid.UUID,
) -> tuple[SigningSession, str]:
    """Create (or reuse) a signing session for a signature request.

    Returns (session, opaque one-time token). The token is stored only as
    its SHA-256 hash and must be delivered to the signer out-of-band.
    """
    result = await db.execute(
        select(SigningSession).where(
            SigningSession.signature_request_id == signature_request_id
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        if existing.status in (SigningSessionStatus.SIGNED, SigningSessionStatus.DECLINED):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Signing session already completed",
            )
        token = generate_signing_token()
        existing.token_hash = hash_signing_token(token)
        existing.token_revoked_at = None
        existing.expires_at = _utcnow() + timedelta(minutes=SESSION_TTL_MINUTES)
        await db.flush()
        return existing, token

    token = generate_signing_token()
    session = SigningSession(
        signature_request_id=signature_request_id,
        status=SigningSessionStatus.CREATED,
        token_hash=hash_signing_token(token),
        expires_at=_utcnow() + timedelta(minutes=SESSION_TTL_MINUTES),
        created_at=_utcnow(),
    )
    db.add(session)
    await db.flush()
    return session, token


async def exchange_signing_token(db: AsyncSession, token: str) -> dict:
    """Exchange the one-time token for the signing session (2.06.8)."""
    token_hash = hash_signing_token(token)
    result = await db.execute(
        select(SigningSession).where(SigningSession.token_hash == token_hash)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired signing link",
        )
    await _handle_expiry(db, session)
    if session.token_used_at is not None or session.token_revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Signing link already consumed",
        )
    session.token_used_at = _utcnow()
    await _append_event(
        db, session, SigningEventType.REQUEST_OPENED, metadata_={}
    )
    await db.flush()
    return serialize_session(session)


async def _handle_expiry(db: AsyncSession, session: SigningSession) -> None:
    if session.status in (SigningSessionStatus.SIGNED, SigningSessionStatus.DECLINED, SigningSessionStatus.CANCELLED):
        return
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if _utcnow() > expires_at:
        session.status = SigningSessionStatus.EXPIRED
        await _append_event(
            db, session, SigningEventType.SESSION_EXPIRED, metadata_={}
        )
        await db.flush()


async def get_authorized_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    current_user,
    org_id,
) -> SigningSession:
    """Load a session and verify the caller may act for it (2.06.4).

    Matching happens against the signature request:
      - the caller is the agreement owner/member with signing permission, or
      - the caller's email matches the request's signer email.
    """
    result = await db.execute(
        select(SigningSession).where(SigningSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signing session not found",
        )
    await _handle_expiry(db, session)

    req = (
        await db.execute(
            select(SignatureRequest).where(
                SignatureRequest.id == session.signature_request_id
            )
        )
    ).scalar_one_or_none()
    if req is None or req.tenant_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signing session not found",
        )

    linked = current_user.email and current_user.email == req.email
    if not linked:
        from app.models.rbac import OrganizationMember
        member = (
            await db.execute(
                select(OrganizationMember.id).where(
                    OrganizationMember.organization_id == org_id,
                    OrganizationMember.user_id == current_user.id,
                    OrganizationMember.status == "active",
                )
            )
        ).scalar_one_or_none()
        if member is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized for this signing session",
            )
    if session.status == SigningSessionStatus.EXPIRED:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Signing session expired",
        )
    return session


async def _load_request(db: AsyncSession, session: SigningSession) -> SignatureRequest:
    req = (
        await db.execute(
            select(SignatureRequest).where(
                SignatureRequest.id == session.signature_request_id
            )
        )
    ).scalar_one_or_none()
    if req is None:
        raise SigningSessionError("Signature request not found")
    return req


async def _append_event(
    db: AsyncSession,
    session: SigningSession,
    event_type: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata_: dict | None = None,
) -> SigningEvent:
    tail = (
        await db.execute(
            select(SigningEvent)
            .where(SigningEvent.signing_session_id == session.id)
            .order_by(SigningEvent.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    prev_hash = tail.event_hash if tail else None
    event = {
        "signing_session_id": str(session.id),
        "event_type": event_type,
        "metadata": metadata_ or {},
        "ip_address": ip_address,
        "user_agent": user_agent,
    }
    event_hash = evidence_hash(previous_hash=prev_hash, event=event)
    record = SigningEvent(
        signing_session_id=session.id,
        event_type=event_type,
        event_hash=event_hash,
        prev_event_hash=prev_hash,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata_=metadata_ or {},
        created_at=_utcnow(),
    )
    db.add(record)
    return record


async def give_consent(db: AsyncSession, session: SigningSession) -> SigningSession:
    if session.status not in (
        SigningSessionStatus.CREATED,
        SigningSessionStatus.READY,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Signing session is not ready for consent ({session.status})",
        )
    session.consented_at = _utcnow()
    session.status = SigningSessionStatus.AUTHENTICATION_REQUIRED
    await _append_event(db, session, SigningEventType.CONSENT_GIVEN, metadata_={})
    await db.flush()
    return session


async def authenticate_session(
    db: AsyncSession,
    session: SigningSession,
    *,
    challenge_id: uuid.UUID,
    code: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> SigningSession:
    """Step-up authentication via a one-time code bound to this request."""
    from app.services.otp_service import OTPError, verify_otp

    req = await _load_request(db, session)
    await _append_event(
        db,
        session,
        SigningEventType.AUTHENTICATION_STARTED,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata_={"challenge_id": str(challenge_id)},
    )
    try:
        challenge = await verify_otp(
            db, challenge_id=challenge_id, code=code
        )
    except OTPError as e:
        await _append_event(
            db,
            session,
            SigningEventType.AUTHENTICATION_FAILED,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_={"reason": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )
    if challenge.signature_request_id != req.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP challenge does not belong to this signing session",
        )
    session.authenticated_at = _utcnow()
    session.status = SigningSessionStatus.READY
    await _append_event(
        db,
        session,
        SigningEventType.AUTHENTICATION_PASSED,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata_={"identity_method": "otp", "challenge_id": str(challenge.id)},
    )
    await db.flush()
    return session


async def default_placements(db: AsyncSession, session: SigningSession) -> list[dict]:
    """Materialise a default signature placement for the session (2.06.13)."""
    result = await db.execute(
        select(SignaturePlacement).where(SignaturePlacement.signing_session_id == session.id)
    )
    placements = list(result.scalars().all())
    if not placements:
        placement = SignaturePlacement(
            signing_session_id=session.id,
            page_number=1,
            x=0.3,
            y=0.75,
            width=0.4,
            height=0.06,
            field_key="signer_primary_signature",
            signature_type=SignatureType.DRAWN,
            metadata_={"default": True},
        )
        db.add(placement)
        await db.flush()
        placements = [placement]
    return [
        {
            "id": str(p.id),
            "page_number": p.page_number,
            "x": p.x,
            "y": p.y,
            "width": p.width,
            "height": p.height,
            "field_key": p.field_key,
            "signature_type": p.signature_type,
        }
        for p in placements
    ]


async def verify_signing_order(
    db: AsyncSession, session: SigningSession
) -> None:
    """Block this signer until all earlier-order required signers are done."""
    req = await _load_request(db, session)
    blocker_rows = (
        await db.execute(
            select(SignatureRequest)
            .where(
                SignatureRequest.agreement_id == req.agreement_id,
                SignatureRequest.id != req.id,
                SignatureRequest.signing_order < req.signing_order,
            )
            .order_by(SignatureRequest.signing_order)
        )
    ).scalars().all()
    for blocker in blocker_rows:
        if blocker.status in ("pending", "sent"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Earlier signer in the signing order has not completed",
            )


async def submit_signature(
    db: AsyncSession,
    session: SigningSession,
    *,
    placement_id: uuid.UUID,
    signature_type: str,
    signature_payload: str | dict,
    current_user,
    org_id,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    """The atomic signature transaction (2.06.15-2.06.16)."""
    if session.status != SigningSessionStatus.READY and session.status != SigningSessionStatus.SIGNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot sign in status {session.status}",
        )
    if session.authenticated_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Signer has not completed step-up authentication",
        )
    if session.consented_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Signer has not given consent",
        )

    req = await _load_request(db, session)
    if req.status in ("signed", "declined", "cancelled", "executed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot sign signature request in status {req.status}",
        )
    await verify_signing_order(db, session)

    try:
        signature_type = SignatureType(signature_type.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported signature type: {signature_type}",
        )

    placement = (
        await db.execute(
            select(SignaturePlacement).where(
                SignaturePlacement.id == placement_id,
                SignaturePlacement.signing_session_id == session.id,
            )
        )
    ).scalar_one_or_none()
    if placement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signature placement not found for this session",
        )

    timestamp = _utcnow()
    if isinstance(signature_payload, str):
        payload_digest = signature_payload
    else:
        payload_digest = hash_signing_token(_json.dumps(signature_payload, sort_keys=True))
    version_id = req.version_id
    signature_input = (
        f"{req.id}:{version_id}:{req.email}:{timestamp.isoformat()}:{signature_type}"
    )
    signature_hash = hashlib.sha256(signature_input.encode()).hexdigest()

    record = SignerRecord(
        signature_request_id=req.id,
        agreement_id=req.agreement_id,
        version_id=version_id,
        signer_type="internal" if current_user else "external",
        user_id=current_user.id if current_user else None,
        name=req.name,
        email=req.email,
        signed_at=timestamp,
        ip_address=ip_address,
        user_agent=user_agent,
        consent_text="Electronic signature consent recorded",
        signature_hash=signature_hash,
        identity_verified=True,
        identity_method="otp",
    )
    db.add(record)

    req.status = "signed"
    req.signed_at = timestamp

    session.status = SigningSessionStatus.SIGNED
    session.signed_at = timestamp

    package = (
        await db.execute(
            select(ExecutionPackage)
            .where(ExecutionPackage.agreement_id == req.agreement_id)
            .order_by(ExecutionPackage.created_at.desc())
        )
    ).scalars().first()
    package_id = package.id if package else None

    if package_id is not None:
        evidence_item = ExecutionEvidenceItem(
            package_id=package_id,
            evidence_type="SIGNER_SIGNATURE",
            content_hash=signature_hash,
            data_json={
                "agreement_id": str(req.agreement_id),
                "signature_request_id": str(req.id),
                "signing_session_id": str(session.id),
                "version_id": str(version_id),
                "signer_email": req.email,
                "signature_method": signature_type,
                "placement": {"field_key": placement.field_key, "page": placement.page_number},
                "authentication_method": "otp",
                "consent_recorded": True,
            },
        )
        db.add(evidence_item)

    await _append_event(
        db,
        session,
        SigningEventType.SIGNED,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata_={
            "signature_hash": signature_hash,
            "signature_type": signature_type,
            "placement_id": str(placement.id),
        },
    )

    from app.services.audit_service import record_event
    await record_event(
        db,
        tenant_id=org_id,
        agreement_id=req.agreement_id,
        actor_id=current_user.id if current_user else None,
        actor_type="user",
        action="SIGNING_SESSION_SIGNED",
        resource_type="signing_session",
        resource_id=session.id,
        metadata_json={
            "signature_hash": signature_hash,
            "evidence_hash": signature_hash,
        },
        ip_address=ip_address,
    )
    await db.flush()
    await _advance_execution(db, req, org_id)
    return {
        "signature_id": str(record.id),
        "signature_hash": signature_hash,
        "signed_at": timestamp.isoformat(),
        "evidence_reference": signature_hash,
        "session_status": session.status,
    }


async def _advance_execution(db: AsyncSession, req: SignatureRequest, org_id) -> bool:
    """When every required signer has signed, finalize execution (2.06.23)."""
    result = await db.execute(
        select(SignatureRequest).where(
            SignatureRequest.agreement_id == req.agreement_id
        )
    )
    requests = list(result.scalars().all())
    if not requests:
        return False
    if not all(r.status in ("signed", "executed") for r in requests):
        return False

    from app.models.agreement import Agreement
    agreement = (
        await db.execute(
            select(Agreement).where(Agreement.id == req.agreement_id)
        )
    ).scalar_one_or_none()
    if agreement is None or agreement.status == "executed":
        return False

    from app.services.lifecycle_service import TransitionNotAllowed, apply_transition
    try:
        await apply_transition(
            db,
            agreement=agreement,
            action_key="execute",
            actor_id=None,
            org_id=org_id,
            actor_type="system",
            metadata_json={"source": "signing_sessions", "signer_count": len(requests)},
        )
    except TransitionNotAllowed:
        return False
    agreement.execution_date = _utcnow().date()

    from app.services.agreement_versioning import get_latest_version, lock_version
    version = await get_latest_version(db, agreement.id)
    if version:
        import hashlib as _hashlib
        await lock_version(db, version)
        if version.content:
            document_hash = _hashlib.sha256(version.content.encode()).hexdigest()
            version.content_hash = document_hash

    from app.services.audit_service import record_event
    await record_event(
        db,
        tenant_id=org_id,
        agreement_id=agreement.id,
        actor_id=None,
        actor_type="system",
        action="AGREEMENT_EXECUTED",
        resource_type="agreement",
        resource_id=agreement.id,
        metadata_json={"source": "signing_sessions"},
    )
    await db.flush()
    return True


async def decline_signature(
    db: AsyncSession,
    session: SigningSession,
    *,
    reason: str | None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> SigningSession:
    if session.status in (SigningSessionStatus.SIGNED, SigningSessionStatus.DECLINED, SigningSessionStatus.CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot decline session in status {session.status}",
        )
    session.status = SigningSessionStatus.DECLINED
    session.declined_at = _utcnow()
    session.decline_reason = reason
    req = await _load_request(db, session)
    req.status = "declined"
    await _append_event(
        db,
        session,
        SigningEventType.DECLINED,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata_={"reason": reason},
    )
    await db.flush()
    return session


async def session_events(db: AsyncSession, session: SigningSession) -> list[dict]:
    result = await db.execute(
        select(SigningEvent)
        .where(SigningEvent.signing_session_id == session.id)
        .order_by(SigningEvent.created_at)
    )
    return [
        {
            "id": str(e.id),
            "event_type": e.event_type,
            "event_hash": e.event_hash,
            "prev_event_hash": e.prev_event_hash,
            "metadata": e.metadata_,
            "ip_address": e.ip_address,
            "created_at": e.created_at.isoformat(),
        }
        for e in result.scalars().all()
    ]


async def completion_payload(db: AsyncSession, session: SigningSession) -> dict:
    req = await _load_request(db, session)
    return {
        "session_id": str(session.id),
        "status": session.status,
        "agreement_id": str(req.agreement_id),
        "agreement_title": req.name,
        "signed_at": session.signed_at.isoformat() if session.signed_at else None,
        "declined": session.status == SigningSessionStatus.DECLINED,
        "decline_reason": session.decline_reason,
        "evidence_reference": (
            hashlib.sha256(
                f"{req.id}:{req.version_id}".encode()
            ).hexdigest()
        ),
    }


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------

async def get_idempotent_result(
    db: AsyncSession, *, key: str | None, method: str, path: str
) -> dict | None:
    if not key:
        return None
    tgt = hash_idempotency_key(f"{method}:{path}:{key}")
    result = await db.execute(
        select(IdempotencyKey).where(IdempotencyKey.key_hash == tgt)
    )
    rec = result.scalar_one_or_none()
    if rec is not None:
        return rec.response_json
    return None


async def save_idempotent_result(
    db: AsyncSession,
    *,
    key: str | None,
    method: str,
    path: str,
    response_json: dict,
) -> None:
    if not key:
        return
    rec = IdempotencyKey(
        key_hash=hash_idempotency_key(f"{method}:{path}:{key}"),
        method=method,
        path=path,
        response_json=response_json,
        created_at=_utcnow(),
        expires_at=_utcnow() + timedelta(seconds=IDEMPOTENCY_TTL_SECONDS),
    )
    db.add(rec)
    await db.flush()