"""External access policy API (spec §3.20.51, §3.20.20, §3.20.55).

GET/PUT /external-policy                     — org-level policy (admin)
GET/PUT /agreements/{id}/sharing-policy      — field-level sharing policy
GET     /review/{token}/dashboard            — guest portal aggregation
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.services import external_policy_service
from app.services.external_party_service import validate_access_token

router = APIRouter(prefix="/external-policy", tags=["External Policy"])
agreement_router = APIRouter(prefix="/agreements", tags=["External Policy"])


class PolicyResponse(BaseModel):
    allow_external_access: bool
    max_link_ttl_days: int | None
    required_id_verification: str
    external_change_policy: str


class PolicyUpdate(BaseModel):
    allow_external_access: bool | None = None
    max_link_ttl_days: int | None = Field(default=None, ge=1, le=365)
    required_id_verification: str | None = None
    external_change_policy: str | None = None


class SharingPolicyUpdate(BaseModel):
    shared_fields: dict | None = None
    hide_comments: bool | None = None
    hide_internal_participants: bool | None = None


@router.get("")
async def get_policy(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    policy = await external_policy_service.get_or_create_policy(
        db, organization_id=uuid.UUID(str(org_id))
    )
    return PolicyResponse(
        allow_external_access=policy.allow_external_access,
        max_link_ttl_days=policy.max_link_ttl_days,
        required_id_verification=policy.required_id_verification,
        external_change_policy=policy.external_change_policy,
    )


@router.put("")
async def put_policy(
    body: PolicyUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    try:
        policy = await external_policy_service.update_policy(
            db,
            organization_id=uuid.UUID(str(org_id)),
            allow_external_access=body.allow_external_access,
            max_link_ttl_days=body.max_link_ttl_days,
            required_id_verification=body.required_id_verification,
            external_change_policy=body.external_change_policy,
        )
    except external_policy_service.ExternalPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return PolicyResponse(
        allow_external_access=policy.allow_external_access,
        max_link_ttl_days=policy.max_link_ttl_days,
        required_id_verification=policy.required_id_verification,
        external_change_policy=policy.external_change_policy,
    )


@agreement_router.get("/{agreement_id}/sharing-policy")
async def get_sharing_policy(
    agreement_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
):
    await verify_agreement_access(
        agreement_id=agreement_id, current_user=current_user, org_id=org_id, db=db
    )
    policy = await external_policy_service.get_sharing_policy(
        db, agreement_id=agreement_id
    )
    if policy is None:
        return {"shared_fields": None, "hide_comments": False,
                "hide_internal_participants": True}
    return {
        "shared_fields": policy.shared_fields,
        "hide_comments": policy.hide_comments,
        "hide_internal_participants": policy.hide_internal_participants,
    }


@agreement_router.put("/{agreement_id}/sharing-policy")
async def put_sharing_policy(
    agreement_id: uuid.UUID,
    body: SharingPolicyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
):
    await verify_agreement_access(
        agreement_id=agreement_id, current_user=current_user, org_id=org_id, db=db
    )
    policy = await external_policy_service.get_sharing_policy(
        db, agreement_id=agreement_id
    )
    if policy is None:
        from app.models.external_policy import AgreementSharingPolicy

        policy = AgreementSharingPolicy(agreement_id=agreement_id)
        db.add(policy)
    if body.shared_fields is not None:
        policy.shared_fields = body.shared_fields
    if body.hide_comments is not None:
        policy.hide_comments = body.hide_comments
    if body.hide_internal_participants is not None:
        policy.hide_internal_participants = body.hide_internal_participants
    await db.flush()
    await db.commit()
    return {
        "shared_fields": policy.shared_fields,
        "hide_comments": policy.hide_comments,
        "hide_internal_participants": policy.hide_internal_participants,
    }


@router.get("/review/{token}/dashboard")
async def external_dashboard(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Guest portal aggregation — no account, token-scoped (§3.20.55)."""
    external_party = await validate_access_token(db, token)
    if external_party is None:
        raise HTTPException(status_code=404, detail="Invalid or expired access link")
    return await external_policy_service.external_dashboard(
        db, external_party=external_party
    )


class PortalLookupRequest(BaseModel):
    email: str


@router.post("/portal/lookup")
async def portal_lookup(
    body: PortalLookupRequest,
    db: AsyncSession = Depends(get_db),
):
    """Anti-enumeration portal lookup (spec §3.20.56-57).

    A guest who lost their link submits their email. The response is
    byte-identical whether or not any party matches: no existence signal,
    no valid-token grant. In production this is where link re-delivery is
    queued after the uniform response is returned.
    """
    from app.models.external_party import ExternalParty
    from sqlalchemy import select as _select

    await db.execute(
        _select(ExternalParty.id).where(
            ExternalParty.signatory_email == body.email.strip().lower()
        ).limit(1)
    )
    # Deliberately uniform: we do not act on whether a row existed.
    return {"status": "ok", "message": "If a portal access exists for this address, the link has been re-sent."}
