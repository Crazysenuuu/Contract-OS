"""External party service.

Manages the tokenized review flow for counterparty access.
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement, AgreementVersion
from app.models.external_party import (
    ExternalParty,
    ExternalPartyComment,
    ExternalPartySession,
    ExternalPartySignature,
    KycVerificationAttempt,
)
from app.services.agreement_versioning import get_latest_version

# Default validity of a guest review link (spec 24: time-bound access).
GUEST_LINK_TTL_DAYS = 30


def generate_access_token() -> str:
    """Generate a cryptographically secure access token."""
    return secrets.token_urlsafe(96)


async def create_share_link(
    db: AsyncSession,
    *,
    agreement_id: uuid.UUID,
    created_by: uuid.UUID,
    ttl_hours: int = 72,
    allow_comments: bool = True,
) -> ExternalParty:
    """Share-by-link for an agreement (spec §3.5.42).

    Creates a lightweight view-only guest whose party role is 'viewer' and
    whose capabilities are limited to commenting (optionally none). The link
    expires by construction — share links are always time-bound.
    """
    from sqlalchemy import select as _select
    from app.models.agreement_access import AgreementParty

    party = (
        await db.execute(
            _select(AgreementParty)
            .where(AgreementParty.agreement_id == agreement_id)
            .limit(1)
        )
    ).scalars().first()
    if party is None:
        raise ValueError("Agreement has no party rows to anchor a share link")

    link_party = ExternalParty(
        agreement_id=agreement_id,
        agreement_party_id=party.id,
        company_name="Shared link",
        signatory_name="Anonymous viewer",
        signatory_email=f"share-{secrets.token_hex(6)}@links.invalid",
        access_token=generate_access_token(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl_hours),
        status="invited",
        can_comment=allow_comments,
        can_propose_changes=False,
        can_accept=False,
        can_sign=False,
    )
    db.add(link_party)
    await db.flush()
    return link_party


async def create_evidence_request(
    db: AsyncSession,
    *,
    external_party: ExternalParty,
    title: str,
    description: str | None = None,
    due_in_days: int = 14,
) -> dict:
    """External evidence request (spec §3.20.28-30).

    The host asks the counterparty to upload evidence (insurance certificate,
    delivery note). The request is stored on the party's metadata and the
    upload itself goes through the normal authorized upload endpoint — the
    request grants nothing by itself.
    """
    requests = list((external_party.party_metadata or {}).get("evidence_requests", []))
    request = {
        "id": secrets.token_hex(8),
        "title": title,
        "description": description,
        "status": "pending",
        "due_at": (
            datetime.now(timezone.utc) + timedelta(days=due_in_days)
        ).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    requests.append(request)
    external_party.party_metadata = dict(external_party.party_metadata or {}) | {
        "evidence_requests": requests
    }
    await db.flush()
    return request


async def update_evidence_request_status(
    db: AsyncSession,
    *,
    external_party: ExternalParty,
    request_id: str,
    status: str,
) -> dict | None:
    """Advance an evidence request's state (§3.20.79)."""
    if status not in ("submitted", "verified", "rejected", "cancelled"):
        raise ValueError("status must be submitted|verified|rejected|cancelled")
    requests = list((external_party.party_metadata or {}).get("evidence_requests", []))
    for request in requests:
        if request.get("id") == request_id:
            request["status"] = status
            external_party.party_metadata = dict(external_party.party_metadata or {}) | {
                "evidence_requests": requests
            }
            await db.flush()
            return request
    return None


async def create_external_party(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    agreement_party_id: uuid.UUID,
    company_name: str,
    signatory_name: str,
    signatory_email: str,
    signatory_title: str | None = None,
    can_comment: bool = True,
    can_propose_changes: bool = True,
    can_accept: bool = True,
    can_sign: bool = True,
    expires_at: datetime | None = None,
) -> ExternalParty:
    """Create an external party with a tokenized access link.

    Args:
        db: Database session.
        agreement_id: The agreement to grant access to.
        agreement_party_id: Which party this external party represents.
        company_name: Company name.
        signatory_name: Name of the signatory.
        signatory_email: Email for notifications.
        signatory_title: Optional title/position.
        can_comment: Can add comments.
        can_propose_changes: Can propose changes.
        can_accept: Can accept the agreement.
        can_sign: Can sign the agreement.
        expires_at: When the review link expires. Defaults to today + the
            guest-link TTL so access is time-bound (spec 24).

    Returns:
        The created ExternalParty with access_token.
    """
    # Organization-level external policy is the upper bound (spec §3.20.51):
    # guest links cannot exist when external access is disabled, and the
    # link lifetime is clamped to the configured maximum (§3.20.77).
    from app.services.external_policy_service import (
        ExternalPolicyError,
        assert_external_access_allowed,
        clamp_link_expiry,
    )

    agreement_row = await db.get(Agreement, agreement_id)
    if agreement_row is None:
        raise ValueError("Agreement not found")
    try:
        policy = await assert_external_access_allowed(
            db, organization_id=agreement_row.organization_id
        )
    except ExternalPolicyError as exc:
        raise ValueError(str(exc)) from exc
    expires_at = clamp_link_expiry(policy, expires_at)

    external_party = ExternalParty(
        agreement_id=agreement_id,
        agreement_party_id=agreement_party_id,
        company_name=company_name,
        signatory_name=signatory_name,
        signatory_email=signatory_email,
        signatory_title=signatory_title,
        access_token=generate_access_token(),
        expires_at=expires_at
        or datetime.now(timezone.utc) + timedelta(days=GUEST_LINK_TTL_DAYS),
        status="pending",
        can_comment=can_comment,
        can_propose_changes=can_propose_changes,
        can_accept=can_accept,
        can_sign=can_sign,
    )
    db.add(external_party)
    await db.flush()

    return external_party


async def validate_access_token(
    db: AsyncSession,
    token: str,
) -> ExternalParty | None:
    """Validate an access token and return the external party.

    Refuses expired links (``expires_at`` in the past) so a stale guest link
    can no longer view, comment, accept, or sign (spec 24).

    Args:
        db: Database session.
        token: The access token to validate.

    Returns:
        ExternalParty if valid, None if invalid or expired.
    """
    result = await db.execute(
        select(ExternalParty).where(
            ExternalParty.access_token == token,
            ExternalParty.status.in_(["pending", "invited", "viewed"]),
        )
    )
    party = result.scalar_one_or_none()
    if party is None:
        return None
    if party.expires_at is not None and party.expires_at < datetime.now(timezone.utc):
        return None
    return party


async def record_session(
    db: AsyncSession,
    external_party_id: uuid.UUID,
    action: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> ExternalPartySession:
    """Record a session event for an external party.

    Args:
        db: Database session.
        external_party_id: The external party.
        action: Action performed ('viewed', 'commented', 'accepted', etc.)
        ip_address: Client IP address.
        user_agent: Client user agent string.

    Returns:
        The created session record.
    """
    session = ExternalPartySession(
        external_party_id=external_party_id,
        ip_address=ip_address,
        user_agent=user_agent,
        viewed_at=datetime.now(timezone.utc),
        action=action,
    )
    db.add(session)
    await db.flush()

    return session


async def add_external_comment(
    db: AsyncSession,
    external_party_id: uuid.UUID,
    content: str,
    clause_identifier: str | None = None,
) -> ExternalPartyComment:
    """Add a comment from an external party.

    Args:
        db: Database session.
        external_party_id: The external party.
        content: Comment content.
        clause_identifier: Optional clause to attach comment to.

    Returns:
        The created comment.
    """
    comment = ExternalPartyComment(
        external_party_id=external_party_id,
        clause_identifier=clause_identifier,
        content=content,
    )
    db.add(comment)
    await db.flush()

    return comment


def ensure_id_verified(external_party: ExternalParty) -> None:
    """Raise unless the guest has completed ID verification (spec 24.3).

    Only gates consequential actions (accept/sign); viewing and commenting
    stay frictionless so the tokenized link keeps its low-friction promise.
    """
    if external_party.requires_id_verification and not external_party.id_verified_at:
        raise PermissionError("ID verification required before this action")


class KYCFlowError(Exception):
    """Raised when the guest KYC flow is used incorrectly.

    Distinct from :class:`app.services.kyc_provider.KYCProviderError`
    (provider/network failures) so the API layer can map the two to
    different HTTP statuses: 400 for caller mistakes, 502 for provider
    outages.
    """


async def start_id_verification(
    db: AsyncSession,
    external_party: ExternalParty,
    *,
    return_url: str | None = None,
) -> dict:
    """Begin ID verification for a guest.

    Two flows (spec 24.3 / 24.4):

      - Default: email OTP challenge (frictionless, no provider dependency).
        The OTP infrastructure is keyed to signature requests, so guests get
        a synthetic request id derived from the external party id — the
        challenge row only needs a stable FK target, and the code is
        delivered to the party's signatory email either way.

      - When the tenant enabled a KYC provider (``KYC_PROVIDER`` set to a
        real provider and the party row flagged ``requires_kyc``), a
        provider verification session is created instead and the guest is
        redirected to the hosted document+selfie check. The party is only
        marked verified when the provider confirms (completion endpoint or
        webhook), never on session creation.

    Returns the challenge payload:

      - OTP flow: ``{method: 'otp_email', challenge_id, expires_at[, debug_code]}``
      - KYC flow: ``{method: 'kyc_provider', provider, session_id, url,
        expires_at, status}``

    The raw OTP code is only in the return value for the dev/log path, same
    as native signing.
    """
    if getattr(external_party, "requires_kyc", False):
        return await start_kyc_verification(
            db, external_party, return_url=return_url
        )

    from app.services.otp_service import issue_otp

    # external_party rows always belong to an agreement with an org; the
    # challenge's organization_id comes from the agreement's tenant.
    result = await db.execute(
        select(Agreement.organization_id).where(
            Agreement.id == external_party.agreement_id
        )
    )
    organization_id = result.scalar_one_or_none()
    if organization_id is None:
        raise ValueError("Agreement not found for external party")

    synthetic_request_id = external_party.id  # stable per-party key
    challenge = await issue_otp(
        db,
        organization_id=organization_id,
        signature_request_id=synthetic_request_id,
        channel="email",
        email=external_party.signatory_email,
    )
    return {"method": "otp_email", **challenge}


async def start_kyc_verification(
    db: AsyncSession,
    external_party: ExternalParty,
    *,
    return_url: str | None = None,
) -> dict:
    """Start a provider verification session for a guest (spec 24.4).

    Creates the provider session, records a KycVerificationAttempt journal
    row, and persists the session on the party. Verification itself only
    happens when the provider reports ``verified`` (completion endpoint or
    webhook) — never here.
    """
    from app.services.kyc_provider import (
        KYCProviderError,
        resolve_kyc_provider,
    )

    provider = resolve_kyc_provider()

    try:
        result = await provider.create_verification_session(
            external_party_id=external_party.id,
            signatory_name=external_party.signatory_name,
            signatory_email=external_party.signatory_email,
            return_url=return_url,
            agreement_id=external_party.agreement_id,
        )
    except KYCProviderError:
        raise

    external_party.kyc_provider = result.provider
    external_party.kyc_session_id = result.session_id
    external_party.kyc_session_status = result.status
    external_party.kyc_session_url = result.url
    external_party.kyc_session_expires_at = result.expires_at

    db.add(
        KycVerificationAttempt(
            external_party_id=external_party.id,
            provider=result.provider,
            session_id=result.session_id,
            status=result.status,
            details=dict(result.provider_metadata or {}),
        )
    )
    await db.flush()

    return {
        "method": "kyc_provider",
        "provider": result.provider,
        "session_id": result.session_id,
        "status": result.status,
        "url": result.url,
        "expires_at": result.expires_at.isoformat()
        if result.expires_at
        else None,
        # Mock provider only — lets dev/test complete the flow without the
        # hosted page, same spirit as the OTP debug_code.
        "debug_code": result.debug_code,
    }


async def complete_kyc_verification(
    db: AsyncSession,
    external_party: ExternalParty,
    *,
    submitted_code: str | None = None,
) -> dict:
    """Resolve a guest's provider verification session.

    Asks the provider for the session outcome (the mock uses
    ``submitted_code`` as the completion signal; real providers ignore it
    and report their own state). When the provider reports ``verified`` the
    party is marked ID-verified with method ``kyc_provider`` and the
    journal row is closed. Any other outcome leaves the party unverified.

    Returns ``{verified, status, method?, failure_reason?}``.
    """
    from app.services.kyc_provider import (
        KYCProviderError,
        resolve_kyc_provider,
    )

    if not external_party.kyc_session_id:
        raise KYCFlowError(
            "No provider verification session for this guest; "
            "start one via /verify-id/start first"
        )

    provider = resolve_kyc_provider()
    try:
        result = await provider.complete_verification(
            session_id=external_party.kyc_session_id,
            submitted_code=submitted_code,
        )
    except KYCProviderError:
        raise

    now = datetime.now(timezone.utc)

    attempt = (
        await db.execute(
            select(KycVerificationAttempt)
            .where(
                KycVerificationAttempt.external_party_id == external_party.id,
                KycVerificationAttempt.session_id
                == external_party.kyc_session_id,
                KycVerificationAttempt.status.notin_(
                    ["verified", "canceled"]
                ),
            )
            .order_by(KycVerificationAttempt.created_at.desc())
            .limit(1)
        )
    ).scalars().first()

    external_party.kyc_session_status = result.status

    if attempt is not None:
        attempt.status = result.status
        attempt.failure_reason = result.failure_reason
        attempt.details = result.details or attempt.details
        if result.verified:
            attempt.completed_at = now

    if result.verified:
        external_party.id_verified_at = now
        external_party.id_verification_method = "kyc_provider"
        return {
            "verified": True,
            "status": result.status,
            "method": "kyc_provider",
            "details": result.details,
        }

    return {
        "verified": False,
        "status": result.status,
        "failure_reason": result.failure_reason,
    }


async def apply_kyc_webhook_event(
    db: AsyncSession,
    external_party: ExternalParty,
    *,
    session_id: str,
    status: str,
    failure_reason: str | None = None,
    details: dict | None = None,
) -> dict:
    """Apply a provider-reported session outcome (webhook path).

    Idempotent: a session already terminal (verified/canceled) ignores
    further events for the same session id. Only ``verified`` marks the
    party; ``canceled``/``failed`` are journaled without verdicts.
    """
    if external_party.kyc_session_id and external_party.kyc_session_id != session_id:
        raise KYCFlowError(
            "Webhook session does not match the party's active session"
        )

    now = datetime.now(timezone.utc)
    already_terminal = external_party.kyc_session_status in (
        "verified",
        "canceled",
    )

    if already_terminal:
        return {"applied": False, "duplicate": True}

    external_party.kyc_session_status = status

    attempt = (
        await db.execute(
            select(KycVerificationAttempt)
            .where(
                KycVerificationAttempt.external_party_id == external_party.id,
                KycVerificationAttempt.session_id == session_id,
                KycVerificationAttempt.status.notin_(
                    ["verified", "canceled"]
                ),
            )
            .order_by(KycVerificationAttempt.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if attempt is not None:
        attempt.status = status
        attempt.failure_reason = failure_reason
        if details:
            attempt.details = details
        if status == "verified":
            attempt.completed_at = now

    applied = {}
    if status == "verified":
        external_party.id_verified_at = now
        external_party.id_verification_method = "kyc_provider"
        applied = {"verified": True, "method": "kyc_provider"}
    else:
        applied = {"verified": False, "status": status}

    return {"applied": True, **applied}


async def complete_id_verification(
    db: AsyncSession,
    external_party: ExternalParty,
    *,
    challenge_id: uuid.UUID,
    code: str,
) -> ExternalParty:
    """Verify a guest's OTP code and mark the party ID-verified."""
    from app.services.otp_service import verify_otp

    await verify_otp(
        db,
        challenge_id=challenge_id,
        code=code,
    )

    external_party.id_verified_at = datetime.now(timezone.utc)
    external_party.id_verification_method = "otp_email"
    await db.flush()
    return external_party


async def accept_agreement(
    db: AsyncSession,
    external_party: ExternalParty,
) -> ExternalParty:
    """Record external party acceptance.

    Args:
        db: Database session.
        external_party: The external party accepting.

    Returns:
        Updated ExternalParty.
    """
    ensure_id_verified(external_party)
    external_party.status = "accepted"
    await db.flush()

    return external_party


async def reject_agreement(
    db: AsyncSession,
    external_party: ExternalParty,
) -> ExternalParty:
    """Record external party rejection.

    Args:
        db: Database session.
        external_party: The external party rejecting.

    Returns:
        Updated ExternalParty.
    """
    external_party.status = "rejected"
    await db.flush()

    return external_party


async def capture_signature(
    db: AsyncSession,
    external_party: ExternalParty,
    agreement_id: uuid.UUID,
    version_id: uuid.UUID,
    consent_text: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> ExternalPartySignature:
    """Capture a signature from an external party.

    Args:
        db: Database session.
        external_party: The external party signing.
        agreement_id: The agreement being signed.
        version_id: The version being signed.
        consent_text: The consent disclosure shown.
        ip_address: Client IP address.
        user_agent: Client user agent string.

    Returns:
        The created signature record.
    """
    ensure_id_verified(external_party)

    # Get the version to compute signature hash
    version = await db.get(AgreementVersion, version_id)
    if not version:
        raise ValueError("Version not found")

    # Compute signature hash binding signer + document + timestamp
    timestamp_str = datetime.now(timezone.utc).isoformat()
    signature_input = (
        f"{external_party.access_token}:"
        f"{version.content_hash}:"
        f"{timestamp_str}"
    )

    import hashlib
    signature_hash = hashlib.sha256(signature_input.encode()).hexdigest()

    signature = ExternalPartySignature(
        external_party_id=external_party.id,
        agreement_id=agreement_id,
        version_id=version_id,
        signed_at=datetime.now(timezone.utc),
        ip_address=ip_address,
        user_agent=user_agent,
        consent_text=consent_text,
        signature_hash=signature_hash,
    )
    db.add(signature)

    # Update external party status
    external_party.status = "signed"

    await db.flush()

    return signature


async def get_external_party_comments(
    db: AsyncSession,
    external_party_id: uuid.UUID,
) -> list[ExternalPartyComment]:
    """Get all comments from an external party."""
    result = await db.execute(
        select(ExternalPartyComment)
        .where(ExternalPartyComment.external_party_id == external_party_id)
        .order_by(ExternalPartyComment.created_at)
    )
    return list(result.scalars().all())


async def get_external_parties_for_agreement(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> list[ExternalParty]:
    """Get all external parties for an agreement."""
    result = await db.execute(
        select(ExternalParty)
        .where(ExternalParty.agreement_id == agreement_id)
        .order_by(ExternalParty.created_at)
    )
    return list(result.scalars().all())
