"""Signing authority enforcement service.

Wires the signatory-authority and DOA engines into the actual signing
paths. Spec 30: a signer may execute an agreement only if (a) they hold a
valid AuthorizedSignatory record whose authority scope/value covers the
agreement, or (b) the value exceeds their limit but the required DOA
approvals have already been completed for the agreement.

Enforcement is active only once an organization has configured at least one
authorized signatory (or a DB DOA approval definition). Orgs that have not
configured any signatories retain the previous behaviour.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agreement import Agreement
from app.models.approval import ApprovalDefinition, ApprovalRecord
from app.models.legal_entity import AuthorizedSignatory, LegalEntity
from app.services.doa_service import resolve_doa_matrix

# Question keys across the seeded agreement schemas that hold total value.
VALUE_KEYS = (
    "total_value",
    "service_fee",
    "development_fee",
    "subscription_fee",
    "consulting_fee",
    "sow_fee",
    "pricing",
    "capital_contribution",
    "compensation",
    "annual_salary",
    "amount",
    "investment_amount",
    "loan_amount",
)

def convert_amount(amount: float, from_currency: str, to_currency: str) -> float:
    """Convert between currencies using the configured FX table (spec §71)."""
    from app.services.currency_service import convert

    return convert(amount, from_currency, to_currency)


def resolve_agreement_financials(agreement: Agreement) -> tuple[float | None, str | None]:
    """Extract (value, currency) from an agreement's stored data.

    Value can live under any of the type-specific question keys; currency
    is read from the data answers first, falling back to the agreement row.
    """
    data = agreement.data or {}
    currency = data.get("currency") or data.get("currency_code") or agreement.currency
    for key in VALUE_KEYS:
        raw = data.get(key)
        if raw is None:
            continue
        if isinstance(raw, (int, float)) and raw > 0:
            return float(raw), currency or "LKR"
        if isinstance(raw, str):
            cleaned = raw.replace(",", "").replace(" ", "").strip()
            try:
                value = float(cleaned)
            except ValueError:
                continue
            if value > 0:
                return value, currency or "LKR"
    return None, currency


@dataclass
class SigningAuthorityResult:
    allowed: bool
    reason: str | None = None
    message: str = ""
    signatory_id: str | None = None
    authority_type: str | None = None
    authority_scope: str | None = None
    maximum_value: float | None = None
    currency: str | None = None
    required_approvals: list = field(default_factory=list)


async def _org_has_authority_configuration(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        select(AuthorizedSignatory.id)
        .join(LegalEntity, LegalEntity.id == AuthorizedSignatory.legal_entity_id)
        .where(
            LegalEntity.organization_id == org_id,
            AuthorizedSignatory.is_active.is_(True),
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return True

    result = await db.execute(
        select(ApprovalDefinition.id).where(
            ApprovalDefinition.organization_id == org_id,
            ApprovalDefinition.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none() is not None


async def _get_user_signatory(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AuthorizedSignatory | None:
    result = await db.execute(
        select(AuthorizedSignatory)
        .join(LegalEntity, LegalEntity.id == AuthorizedSignatory.legal_entity_id)
        .where(
            LegalEntity.organization_id == org_id,
            AuthorizedSignatory.user_id == user_id,
            AuthorizedSignatory.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def _approval_completed(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        select(ApprovalRecord.id)
        .where(
            ApprovalRecord.agreement_id == agreement_id,
            ApprovalRecord.status == "approved",
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def evaluate_signing_authority(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    agreement: Agreement,
    user_id: uuid.UUID,
) -> SigningAuthorityResult:
    """Evaluate whether ``user_id`` may sign ``agreement``.

    Returns an allowed result when the org has not configured any authority
    (backwards compatible), when the agreement has no financial value, when
    the user's signatory scope covers the value, or when the required DOA
    approvals have already been completed for the agreement.
    """
    if not await _org_has_authority_configuration(db, org_id):
        return SigningAuthorityResult(allowed=True, message="No signing authority configured")

    value, currency = resolve_agreement_financials(agreement)
    if value is None:
        return SigningAuthorityResult(
            allowed=True,
            message="Agreement has no financial value subject to authority limits",
        )

    signatory = await _get_user_signatory(db, org_id, user_id)
    if signatory is None:
        return SigningAuthorityResult(
            allowed=False,
            reason="user_not_authorized",
            message="User is not an authorized signatory for this organization",
        )

    now = datetime.now(timezone.utc)

    def _aware(dt):
        if dt is None:
            return None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    if (valid_from := _aware(signatory.valid_from)) and now < valid_from:
        return SigningAuthorityResult(
            allowed=False,
            reason="authority_not_effective",
            message=f"Signing authority effective from {signatory.valid_from}",
            signatory_id=str(signatory.id),
            authority_type=signatory.authority_type,
        )
    if (valid_until := _aware(signatory.valid_until)) and now > valid_until:
        return SigningAuthorityResult(
            allowed=False,
            reason="authority_expired",
            message=f"Signing authority expired on {signatory.valid_until}",
            signatory_id=str(signatory.id),
            authority_type=signatory.authority_type,
        )

    base = {
        "signatory_id": str(signatory.id),
        "authority_type": signatory.authority_type,
        "authority_scope": signatory.authority_scope,
        "maximum_value": float(signatory.maximum_value) if signatory.maximum_value is not None else None,
        "currency": signatory.currency,
    }

    if (signatory.authority_scope or "limited") == "unlimited":
        return SigningAuthorityResult(allowed=True, message="Unlimited signing authority", **base)

    if signatory.maximum_value is None:
        return SigningAuthorityResult(allowed=True, message="Limited authority without value cap", **base)

    converted = convert_amount(value, currency or "LKR", signatory.currency or "LKR")
    if converted <= float(signatory.maximum_value):
        return SigningAuthorityResult(allowed=True, message="Within signing authority limit", **base)

    if await _approval_completed(db, agreement.id):
        return SigningAuthorityResult(
            allowed=True,
            message="Value exceeds signatory limit but approvals are complete",
            **base,
        )

    matrix = await resolve_doa_matrix(
        db,
        organization_id=org_id,
        agreement_value=value,
        currency=currency or "LKR",
        agreement_type=getattr(getattr(agreement, "agreement_type", None), "key", None),
    )
    return SigningAuthorityResult(
        allowed=False,
        reason="exceeds_authority",
        message=(
            f"Agreement value {value:,.2f} {currency or 'LKR'} exceeds the signatory's "
            f"maximum of {signatory.maximum_value:,.2f} {signatory.currency or 'LKR'}; "
            "the required DOA approvals have not been completed"
        ),
        required_approvals=matrix["required_approvals"],
        **base,
    )


async def check_signing_authority(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    agreement: Agreement,
    user_id: uuid.UUID,
) -> SigningAuthorityResult:
    """Compatibility alias used by the sign endpoints."""
    return await evaluate_signing_authority(db, org_id=org_id, agreement=agreement, user_id=user_id)