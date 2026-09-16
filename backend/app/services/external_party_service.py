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
)
from app.services.agreement_versioning import get_latest_version

# Default validity of a guest review link (spec 24: time-bound access).
GUEST_LINK_TTL_DAYS = 30


def generate_access_token() -> str:
    """Generate a cryptographically secure access token."""
    return secrets.token_urlsafe(96)


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


async def start_id_verification(
    db: AsyncSession,
    external_party: ExternalParty,
) -> dict:
    """Begin ID verification for a guest: issue an email OTP challenge.

    The OTP infrastructure is keyed to signature requests, so guests get a
    synthetic request id derived from the external party id — the challenge
    row only needs a stable FK target, and the code is delivered to the
    party's signatory email either way.

    Returns the challenge payload (challenge_id + expiry). The raw code is
    only in the return value for the dev/log path, same as native signing.
    """
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
    return await issue_otp(
        db,
        organization_id=organization_id,
        signature_request_id=synthetic_request_id,
        channel="email",
        email=external_party.signatory_email,
    )


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
