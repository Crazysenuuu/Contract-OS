"""Signing-session API (spec 2.06.26).

Full surface:
    POST /api/v1/signing/sessions                        (initiate, returns one-time token)
    POST /api/v1/signing/sessions/exchange               (token -> session)
    GET  /api/v1/signing/sessions/{id}
    GET  /api/v1/signing/sessions/{id}/document
    POST /api/v1/signing/sessions/{id}/consent
    POST /api/v1/signing/sessions/{id}/authenticate
    GET  /api/v1/signing/sessions/{id}/fields
    POST /api/v1/signing/sessions/{id}/signature
    POST /api/v1/signing/sessions/{id}/decline
    GET  /api/v1/signing/sessions/{id}/events
    GET  /api/v1/signing/sessions/{id}/completion
"""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.execution import SignatureRequest
from app.models.user import User
from app.services import document_storage
from app.services.agreement_renderer import render_agreement
from app.services.signing_session_service import (
    authenticate_session,
    completion_payload,
    create_signing_session,
    decline_signature,
    default_placements,
    exchange_signing_token,
    get_authorized_session,
    get_idempotent_result,
    give_consent,
    save_idempotent_result,
    serialize_session,
    session_events,
    submit_signature,
)

router = APIRouter(
    prefix="/signing",
    tags=["signing-sessions"],
)


class SessionStartRequest(BaseModel):
    signature_request_id: uuid.UUID


class TokenExchangeRequest(BaseModel):
    token: str


class AuthenticateRequest(BaseModel):
    challenge_id: uuid.UUID
    code: str


class DeclineRequest(BaseModel):
    reason: str | None = None


class SignatureSubmitRequest(BaseModel):
    placement_id: uuid.UUID
    signature_type: str
    signature_payload: str | dict = ""


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def start_signing_session(
    body: SessionStartRequest,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a signing session for a signature request; returns the
    one-time token to deliver to the signer (2.06.6)."""
    req = (
        await db.execute(
            select(SignatureRequest).where(
                SignatureRequest.id == body.signature_request_id,
                SignatureRequest.tenant_id == org_id,
            )
        )
    ).scalar_one_or_none()
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signature request not found",
        )
    if req.status not in ("pending", "sent"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot start signing session for request in status {req.status}",
        )
    session, token = await create_signing_session(
        db, signature_request_id=req.id
    )
    await db.flush()
    return serialize_session(session, token=token)


@router.post("/sessions/exchange")
async def exchange_token_endpoint(
    body: TokenExchangeRequest,
    idempotency_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Exchange the one-time token for session access (2.06.8)."""
    method, path = "POST", "/api/v1/signing/sessions/exchange"
    cached = await get_idempotent_result(
        db, key=idempotency_key, method=method, path=path
    )
    if cached is not None:
        return cached
    result = await exchange_signing_token(db, body.token)
    await save_idempotent_result(
        db, key=idempotency_key, method=method, path=path, response_json=result
    )
    await db.flush()
    return result


async def _resolve_session(
    session_id: uuid.UUID, org_id, current_user, db
):
    return await get_authorized_session(
        db,
        session_id=session_id,
        current_user=current_user,
        org_id=org_id,
    )


@router.get("/sessions/{session_id}")
async def get_session_endpoint(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    session = await _resolve_session(session_id, org_id, current_user, db)
    req = (
        await db.execute(
            select(SignatureRequest).where(
                SignatureRequest.id == session.signature_request_id
            )
        )
    ).scalar_one()
    return {
        **serialize_session(session),
        "signer": {"name": req.name, "email": req.email},
        "agreement_id": str(req.agreement_id),
    }


@router.get("/sessions/{session_id}/document")
async def get_signing_document(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Short-lived document download URL for the signer to review (2.06.10)."""
    session = await _resolve_session(session_id, org_id, current_user, db)
    req = (
        await db.execute(
            select(SignatureRequest).where(
                SignatureRequest.id == session.signature_request_id
            )
        )
    ).scalar_one()
    render_result = await render_agreement(
        db, agreement_id=req.agreement_id, generate_pdf=True
    )
    content_ref = document_storage.build_content_ref(
        org_id=org_id, agreement_id=req.agreement_id, doc_type="signing_review"
    )
    if render_result.pdf:
        document_storage.store_blob(content_ref, render_result.pdf)
    elif render_result.html:
        document_storage.store_blob(content_ref, render_result.html.encode("utf-8"))
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No signed document available yet",
        )
    token = document_storage.create_download_token(
        content_ref=content_ref,
        user_id=current_user.id,
        org_id=org_id,
    )
    return {
        "url": f"/api/v1/repository/download?token={token}",
        "expires_in": document_storage.DOWNLOAD_TOKEN_TTL,
        "document_hash": render_result.content_hash,
    }


@router.post("/sessions/{session_id}/consent")
async def consent_endpoint(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    session = await _resolve_session(session_id, org_id, current_user, db)
    session = await give_consent(db, session)
    await db.flush()
    return serialize_session(session)


@router.post("/sessions/{session_id}/authenticate")
async def authenticate_endpoint(
    session_id: uuid.UUID,
    body: AuthenticateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Step-up authentication (2.06.12): verify the signer's one-time code."""
    session = await _resolve_session(session_id, org_id, current_user, db)
    session = await authenticate_session(
        db,
        session,
        challenge_id=body.challenge_id,
        code=body.code,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.flush()
    return serialize_session(session)


@router.get("/sessions/{session_id}/fields")
async def signing_fields_endpoint(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    session = await _resolve_session(session_id, org_id, current_user, db)
    return {"placements": await default_placements(db, session)}


@router.post("/sessions/{session_id}/signature")
async def signature_endpoint(
    session_id: uuid.UUID,
    body: SignatureSubmitRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None),
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Submit a signature instruction; all server-side checks run here."""
    method, path = "POST", f"/api/v1/signing/sessions/{session_id}/signature"
    cached = await get_idempotent_result(
        db, key=idempotency_key, method=method, path=path
    )
    if cached is not None:
        return cached
    session = await _resolve_session(session_id, org_id, current_user, db)
    validate_body(body)
    result = await submit_signature(
        db,
        session,
        placement_id=body.placement_id,
        signature_type=body.signature_type,
        signature_payload=body.signature_payload,
        current_user=current_user,
        org_id=org_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await save_idempotent_result(
        db, key=idempotency_key, method=method, path=path, response_json=result
    )
    await db.flush()
    return result


@router.post("/sessions/{session_id}/decline")
async def decline_endpoint(
    session_id: uuid.UUID,
    body: DeclineRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    session = await _resolve_session(session_id, org_id, current_user, db)
    session = await decline_signature(
        db,
        session,
        reason=body.reason,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.flush()
    return serialize_session(session)


@router.get("/sessions/{session_id}/events")
async def signing_events_endpoint(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    session = await _resolve_session(session_id, org_id, current_user, db)
    return {"events": await session_events(db, session)}


@router.get("/sessions/{session_id}/completion")
async def completion_endpoint(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    session = await _resolve_session(session_id, org_id, current_user, db)
    return await completion_payload(db, session)


def validate_body(body: SignatureSubmitRequest) -> None:
    from app.models.signing_session import SignatureType

    try:
        SignatureType(body.signature_type.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported signature type: {body.signature_type}",
        )