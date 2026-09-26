"""External access policy service (spec §3.20.20-22, §3.20.51, §3.20.55).

The organization policy is the trust boundary's upper bound: guest links can
only be created when external access is enabled, lifetimes are clamped to the
configured maximum, and field-level sharing policies decide which agreement
answers may leave the workspace. The external dashboard aggregates a guest's
view strictly through their grants.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.external_party import ExternalParty
from app.models.external_policy import (
    AgreementSharingPolicy,
    ExternalWorkspacePolicy,
)


class ExternalPolicyError(Exception):
    """Raised when an action violates the organization's external policy."""


async def get_or_create_policy(
    db: AsyncSession, *, organization_id: uuid.UUID
) -> ExternalWorkspacePolicy:
    """Load the org policy, creating the permissive-by-default singleton
    on first touch so existing workspaces keep working."""
    policy = (
        await db.execute(
            select(ExternalWorkspacePolicy).where(
                ExternalWorkspacePolicy.organization_id == organization_id
            )
        )
    ).scalar_one_or_none()
    if policy is None:
        policy = ExternalWorkspacePolicy(organization_id=organization_id)
        db.add(policy)
        await db.flush()
    return policy


async def assert_external_access_allowed(
    db: AsyncSession, *, organization_id: uuid.UUID
) -> ExternalWorkspacePolicy:
    """Gate every guest-link creation behind the org policy (§3.20.51)."""
    policy = await get_or_create_policy(db, organization_id=organization_id)
    if not policy.allow_external_access:
        raise ExternalPolicyError(
            "External access is disabled for this organization"
        )
    return policy


def clamp_link_expiry(
    policy: ExternalWorkspacePolicy, expires_at: datetime | None
) -> datetime | None:
    """Clamp a requested link expiry to the policy maximum (§3.20.77).

    A requested expiry beyond the cap is pulled back; a missing expiry gets
    the cap when one is configured.
    """
    if policy.max_link_ttl_days is None:
        return expires_at
    cap = datetime.now(timezone.utc) + timedelta(days=policy.max_link_ttl_days)
    if expires_at is None:
        return cap
    return min(expires_at, cap)


async def get_sharing_policy(
    db: AsyncSession, *, agreement_id: uuid.UUID
) -> AgreementSharingPolicy | None:
    """Field-level sharing policy for one agreement (§3.20.20)."""
    return (
        await db.execute(
            select(AgreementSharingPolicy).where(
                AgreementSharingPolicy.agreement_id == agreement_id
            )
        )
    ).scalar_one_or_none()


def filter_answers_for_external(
    answers: dict | None, sharing_policy: AgreementSharingPolicy | None
) -> dict:
    """Apply the field-level sharing policy to an answers payload.

    No policy -> everything already reviewed for sharing by the host is
    returned unchanged. A policy with ``shared_fields`` reduces the payload
    to exactly those keys — narrowing only, never widening.
    """
    if sharing_policy is None:
        return dict(answers or {})
    allowed = (sharing_policy.shared_fields or {}).get("keys")
    if not isinstance(allowed, list):
        return dict(answers or {})
    allowed_set = set(allowed)
    return {k: v for k, v in (answers or {}).items() if k in allowed_set}


async def external_dashboard(
    db: AsyncSession, *, external_party: ExternalParty
) -> dict:
    """Aggregate the guest's portal view from their grant (§3.20.55).

    The guest sees only the agreement their party row points at, in the
    states their party row allows — no workspace-wide data ever leaks.
    """
    agreement = await db.get(Agreement, external_party.agreement_id)

    open_comments = (
        await db.scalar(
            select(func.count())
            .select_from(ExternalParty)
            .where(ExternalParty.agreement_id == external_party.agreement_id)
        )
    ) or 0

    return {
        "agreement": {
            "id": str(external_party.agreement_id),
            "title": agreement.title if agreement else None,
            "status": agreement.status if agreement else None,
            "governing_law": agreement.governing_law if agreement else None,
        },
        "party_status": external_party.status,
        "capabilities": {
            "can_comment": external_party.can_comment,
            "can_propose_changes": external_party.can_propose_changes,
            "can_accept": external_party.can_accept,
            "can_sign": external_party.can_sign,
        },
        "requires_id_verification": external_party.requires_id_verification,
        "id_verified": external_party.id_verified_at is not None,
        "expires_at": (
            external_party.expires_at.isoformat()
            if external_party.expires_at
            else None
        ),
        "party_count_on_agreement": open_comments,
    }


async def update_policy(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    allow_external_access: bool | None = None,
    max_link_ttl_days: int | None = None,
    required_id_verification: str | None = None,
    external_change_policy: str | None = None,
) -> ExternalWorkspacePolicy:
    """Admin update path for the org-level external policy."""
    policy = await get_or_create_policy(db, organization_id=organization_id)
    if allow_external_access is not None:
        policy.allow_external_access = allow_external_access
    if max_link_ttl_days is not None:
        policy.max_link_ttl_days = max_link_ttl_days
    if required_id_verification is not None:
        if required_id_verification not in ("none", "otp_email", "kyc_provider"):
            raise ExternalPolicyError(
                "required_id_verification must be none|otp_email|kyc_provider"
            )
        policy.required_id_verification = required_id_verification
    if external_change_policy is not None:
        if external_change_policy not in ("disabled", "review", "auto_accept"):
            raise ExternalPolicyError(
                "external_change_policy must be disabled|review|auto_accept"
            )
        policy.external_change_policy = external_change_policy
    await db.flush()
    return policy
