"""Signature authority API endpoints (spec 24 / 30).

Endpoints are async and tenant-scoped: every entity operation verifies the
caller belongs to the legal entity's organization before returning or
writing signatory data, and all queries run through the async session
(no ``db.query`` calls on the AsyncSession).
"""
from typing import Optional, Set
from uuid import UUID
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user, get_user_org_ids
from app.models.legal_entity import AuthorizedSignatory, LegalEntity
from app.models.user import User
from app.services.doa_service import resolve_doa_matrix
from app.services.signing_authority_service import convert_amount

router = APIRouter(prefix="/signature-authority", tags=["Signature Authority"])

NOW = datetime.now(timezone.utc)


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class CheckAuthorityRequest(BaseModel):
    user_id: str
    agreement_value: float
    currency: str = "LKR"
    legal_entity_id: Optional[str] = None


class AddSignatoryRequest(BaseModel):
    legal_entity_id: str
    name: str
    title: Optional[str] = None
    email: Optional[str] = None
    authority_type: str
    authority_scope: str = "limited"
    maximum_value: Optional[float] = None
    currency: str = "LKR"


async def _user_org_ids(
    db: AsyncSession,
    user_id: UUID,
) -> Set[UUID]:
    """IDs of organizations the user is an active member of."""
    return await get_user_org_ids(db, user_id)


async def _require_org_access(
    db: AsyncSession,
    current_user: User,
    org_id: UUID,
) -> None:
    org_ids = await _user_org_ids(db, current_user.id)
    if org_id not in org_ids:
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this organization's signature authority",
        )


async def _get_entity_owned(
    db: AsyncSession,
    current_user: User,
    entity_id: str,
) -> LegalEntity:
    try:
        entity_uuid = UUID(str(entity_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Legal entity not found")
    entity = await db.get(LegalEntity, entity_uuid)
    if entity is None:
        raise HTTPException(status_code=404, detail="Legal entity not found")
    await _require_org_access(db, current_user, entity.organization_id)
    return entity


async def _get_signatory(
    db: AsyncSession,
    org_id: UUID,
    user_id: str,
) -> AuthorizedSignatory | None:
    result = await db.execute(
        select(AuthorizedSignatory)
        .join(LegalEntity, LegalEntity.id == AuthorizedSignatory.legal_entity_id)
        .where(
            LegalEntity.organization_id == org_id,
            AuthorizedSignatory.user_id == UUID(str(user_id)),
            AuthorizedSignatory.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


def _signatory_summary(s: AuthorizedSignatory) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "title": s.title,
        "email": s.email,
        "authority_type": s.authority_type,
        "authority_scope": s.authority_scope,
        "maximum_value": s.maximum_value,
        "currency": s.currency,
        "is_active": s.is_active,
        "valid_from": s.valid_from,
        "valid_until": s.valid_until,
        "verification_status": s.verification_status,
    }


@router.post("/check")
async def check_signing_authority(
    request: CheckAuthorityRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check if a user has authority to sign an agreement value."""
    org_ids = await _user_org_ids(db, current_user.id)
    if not org_ids:
        raise HTTPException(status_code=403, detail="User has no active organization")

    if request.legal_entity_id:
        entity = await _get_entity_owned(db, current_user, request.legal_entity_id)
        org_id = entity.organization_id
    else:
        org_id = next(iter(org_ids))

    signatory = await _get_signatory(db, org_id, request.user_id)
    if signatory is None:
        return {
            "allowed": False,
            "reason": "user_not_authorized",
            "message": "User is not an authorized signatory",
        }

    now = NOW

    if (valid_from := _aware(signatory.valid_from)) and now < valid_from:
        return {
            "allowed": False,
            "reason": "authority_not_effective",
            "message": f"Signing authority effective from {signatory.valid_from}",
            "signatory_id": str(signatory.id),
            "authority_type": signatory.authority_type,
        }

    if (valid_until := _aware(signatory.valid_until)) and now > valid_until:
        return {
            "allowed": False,
            "reason": "authority_expired",
            "message": f"Signing authority expired on {signatory.valid_until}",
            "signatory_id": str(signatory.id),
            "authority_type": signatory.authority_type,
        }

    base = {
        "signatory_id": str(signatory.id),
        "authority_type": signatory.authority_type,
        "authority_scope": signatory.authority_scope,
        "maximum_value": signatory.maximum_value,
        "currency": signatory.currency,
    }

    if (signatory.authority_scope or "limited") == "unlimited":
        return {"allowed": True, "message": "Unlimited signing authority", **base}

    if signatory.maximum_value is None:
        return {"allowed": True, "message": "Limited authority without value cap", **base}

    converted = convert_amount(
        request.agreement_value,
        request.currency or "LKR",
        signatory.currency or "LKR",
    )
    if converted <= float(signatory.maximum_value):
        return {"allowed": True, "message": "Within signing authority limit", **base}

    matrix = await resolve_doa_matrix(
        db,
        organization_id=org_id,
        agreement_value=request.agreement_value,
        currency=request.currency or "LKR",
    )
    return {
        "allowed": False,
        "reason": "exceeds_authority",
        "message": (
            f"Agreement value {request.agreement_value:,.2f} {request.currency} "
            f"exceeds maximum {signatory.maximum_value:,.2f} {signatory.currency}"
        ),
        "required_approvals": matrix["required_approvals"],
        **base,
    }


@router.get("/approvals-required")
async def get_required_approvals(
    agreement_value: float,
    currency: str = "LKR",
    org_id: Optional[str] = Query(default=None, alias="organization_id"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get required approvals based on agreement value.

    Consults the organization's DB-configured DOA matrix first (spec 24.2),
    falling back to the built-in threshold matrix when no rules exist.
    """
    target_org = org_id
    if not target_org:
        org_ids = await _user_org_ids(db, current_user.id)
        if org_ids:
            target_org = str(next(iter(org_ids)))

    if target_org:
        try:
            matrix = await resolve_doa_matrix(
                db,
                organization_id=UUID(str(target_org)),
                agreement_value=agreement_value,
                currency=currency,
            )
            return {
                "approvals": matrix["required_approvals"],
                "matrix_source": matrix["source"],
                "matrix_name": matrix["name"],
            }
        except Exception:
            pass  # Fall through to the built-in threshold matrix

    from app.services.signature_authority import SignatureAuthorityEngine

    # Builtin fallback path — the engine's DB session is unused by this
    # pure-computation method.
    engine = SignatureAuthorityEngine(db)
    approvals = await engine.get_required_approvals(agreement_value, currency)
    return {"approvals": approvals, "matrix_source": "builtin"}


@router.get("/entity/{entity_id}/report")
async def get_entity_signing_report(
    entity_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get signing authority report for a legal entity."""
    await _get_entity_owned(db, current_user, entity_id)

    result = await db.execute(
        select(AuthorizedSignatory)
        .join(LegalEntity, LegalEntity.id == AuthorizedSignatory.legal_entity_id)
        .where(LegalEntity.id == UUID(str(entity_id)))
    )
    signatories = result.scalars().all()

    active = [s for s in signatories if s.is_active]

    authority_dist = {}
    for s in signatories:
        auth_type = s.authority_type
        if auth_type not in authority_dist:
            authority_dist[auth_type] = {"count": 0, "total_capacity": 0}
        authority_dist[auth_type]["count"] += 1
        if s.maximum_value:
            authority_dist[auth_type]["total_capacity"] += s.maximum_value

    return {
        "total_signatories": len(signatories),
        "active_signatories": len(active),
        "inactive_signatories": len(signatories) - len(active),
        "authority_distribution": authority_dist,
        "unlimited_authority": len([s for s in active if s.authority_scope == "unlimited"]),
    }


@router.get("/entity/{entity_id}/signatories")
async def list_signatories(
    entity_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List signatories for a legal entity (org-scoped)."""
    await _get_entity_owned(db, current_user, entity_id)

    result = await db.execute(
        select(AuthorizedSignatory)
        .join(LegalEntity, LegalEntity.id == AuthorizedSignatory.legal_entity_id)
        .where(LegalEntity.id == UUID(str(entity_id)))
    )
    signatories = result.scalars().all()

    return [_signatory_summary(s) for s in signatories]


@router.post("/signatories")
async def add_signatory(
    request: AddSignatoryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add an authorized signatory to an org-owned legal entity."""
    try:
        entity_uuid = UUID(str(request.legal_entity_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Legal entity not found")

    entity = await db.get(LegalEntity, entity_uuid)
    if entity is None:
        raise HTTPException(status_code=404, detail="Legal entity not found")
    await _require_org_access(db, current_user, entity.organization_id)

    signatory = AuthorizedSignatory(
        legal_entity_id=entity_uuid,
        name=request.name,
        title=request.title,
        email=request.email,
        authority_type=request.authority_type,
        authority_scope=request.authority_scope,
        maximum_value=request.maximum_value,
        currency=request.currency,
        is_active=True,
    )
    db.add(signatory)
    await db.commit()

    return {
        "id": str(signatory.id),
        "name": signatory.name,
        "authority_type": signatory.authority_type,
    }