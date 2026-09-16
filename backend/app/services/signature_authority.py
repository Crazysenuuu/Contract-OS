"""Signature authority engine for validating signing authority."""
from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, select

from app.models.legal_entity import LegalEntity, AuthorizedSignatory


class SignatureAuthorityEngine:
    """Engine for validating signature authority based on company policies."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def check_signing_authority(
        self,
        user_id: str,
        agreement_value: float,
        currency: str = "LKR",
        legal_entity_id: str = None
    ) -> Dict[str, Any]:
        """Check if a user has authority to sign an agreement."""
        # Get signatory record
        signatory = await self._get_active_signatory(user_id)

        if not signatory:
            return {
                "allowed": False,
                "reason": "user_not_authorized",
                "message": "User is not an authorized signatory",
            }

        # Check if signatory is active and valid
        now = datetime.utcnow()
        if signatory.valid_from and now < signatory.valid_from:
            return {
                "allowed": False,
                "reason": "authority_not_effective",
                "message": f"Signing authority effective from {signatory.valid_from}",
            }

        if signatory.valid_until and now > signatory.valid_until:
            return {
                "allowed": False,
                "reason": "authority_expired",
                "message": f"Signing authority expired on {signatory.valid_until}",
            }

        # Check authority scope
        if signatory.authority_scope == "unlimited":
            return {
                "allowed": True,
                "signatory_id": str(signatory.id),
                "authority_type": signatory.authority_type,
                "authority_scope": "unlimited",
            }

        # Check maximum value
        if signatory.maximum_value is not None:
            # Convert to common currency for comparison
            converted_value = self._convert_currency(agreement_value, currency, signatory.currency or "LKR")  # type: ignore[arg-type]

            if converted_value > signatory.maximum_value:
                # Need higher approval
                required_approvals = self._get_required_approvals(agreement_value, currency)
                return {
                    "allowed": False,
                    "reason": "exceeds_authority",
                    "message": f"Agreement value {agreement_value} {currency} exceeds maximum {signatory.maximum_value} {signatory.currency}",
                    "signatory_id": str(signatory.id),
                    "authority_type": signatory.authority_type,
                    "maximum_value": signatory.maximum_value,
                    "required_approvals": required_approvals,
                }

        return {
            "allowed": True,
            "signatory_id": str(signatory.id),
            "authority_type": signatory.authority_type,
            "authority_scope": signatory.authority_scope,
            "maximum_value": signatory.maximum_value,
        }

    async def get_signatory_for_agreement(
        self,
        agreement_value: float,
        currency: str = "LKR",
        legal_entity_id: str = None
    ) -> Optional[AuthorizedSignatory]:
        """Find appropriate signatory for an agreement value."""
        stmt = select(AuthorizedSignatory).where(
            AuthorizedSignatory.is_active == True,  # noqa: E712
        )

        if legal_entity_id:
            stmt = stmt.where(AuthorizedSignatory.legal_entity_id == legal_entity_id)

        result = await self.db.execute(stmt)
        signatories = result.scalars().all()
        now = datetime.utcnow()

        for signatory in signatories:
            # Check validity period
            if signatory.valid_from and now < signatory.valid_from:
                continue
            if signatory.valid_until and now > signatory.valid_until:
                continue

            # Check authority
            if signatory.authority_scope == "unlimited":
                return signatory

            if signatory.maximum_value is not None:
                converted_value = self._convert_currency(agreement_value, currency, signatory.currency or "LKR")  # type: ignore[arg-type]
                if converted_value <= signatory.maximum_value:
                    return signatory

        return None

    def get_required_approvals(
        self,
        agreement_value: float,
        currency: str = "LKR"
    ) -> List[Dict[str, Any]]:
        """Get required approvals based on agreement value."""
        approvals = []

        # Convert to LKR for comparison
        lkr_value = self._convert_to_lkr(agreement_value, currency)

        # Define thresholds
        thresholds = [
            {"min_value": 10000000, "approvals": ["ceo", "cfo", "legal"]},  # > 10M LKR
            {"min_value": 5000000, "approvals": ["director", "finance"]},   # > 5M LKR
            {"min_value": 1000000, "approvals": ["manager"]},               # > 1M LKR
        ]

        for threshold in thresholds:
            if lkr_value >= threshold["min_value"]:
                approvals = threshold["approvals"]
                break

        return [{"role": role, "required": True} for role in approvals]

    async def validate_all_signatories_signed(
        self,
        agreement_id: str,
        legal_entity_id: str
    ) -> Dict[str, Any]:
        """Validate that all required signatories have signed."""
        from app.models.signature import InternalSignature

        # Get required signatories for this legal entity
        result = await self.db.execute(
            select(AuthorizedSignatory).where(
                AuthorizedSignatory.legal_entity_id == legal_entity_id,
                AuthorizedSignatory.is_active == True,  # noqa: E712
            )
        )
        signatories = result.scalars().all()

        # Get actual signatures
        result = await self.db.execute(
            select(InternalSignature).where(
                InternalSignature.agreement_id == agreement_id,
            )
        )
        signatures = result.scalars().all()

        signed_user_ids = {str(sig.user_id) for sig in signatures}

        missing = []
        for signatory in signatories:
            if signatory.user_id and str(signatory.user_id) not in signed_user_ids:
                missing.append({
                    "signatory_id": str(signatory.id),
                    "name": signatory.name,
                    "title": signatory.title,
                    "authority_type": signatory.authority_type,
                })

        return {
            "all_signed": len(missing) == 0,
            "total_required": len(signatories),
            "total_signed": len(signatories) - len(missing),
            "missing_signatories": missing,
        }

    async def get_signing_authority_report(
        self,
        legal_entity_id: str
    ) -> Dict[str, Any]:
        """Get signing authority report for a legal entity."""
        result = await self.db.execute(
            select(AuthorizedSignatory).where(
                AuthorizedSignatory.legal_entity_id == legal_entity_id,
            )
        )
        signatories = result.scalars().all()

        active = [s for s in signatories if s.is_active]
        inactive = [s for s in signatories if not s.is_active]

        # Authority distribution
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
            "inactive_signatories": len(inactive),
            "authority_distribution": authority_dist,
            "unlimited_authority": len([s for s in active if s.authority_scope == "unlimited"]),
        }

    async def _get_active_signatory(self, user_id: str) -> Optional[AuthorizedSignatory]:
        """Fetch the active signatory record for a user."""
        try:
            user_uuid = UUID(str(user_id))
        except (TypeError, ValueError):
            return None
        result = await self.db.execute(
            select(AuthorizedSignatory).where(
                AuthorizedSignatory.user_id == user_uuid,
                AuthorizedSignatory.is_active == True,  # noqa: E712
            )
        )
        return result.scalars().first()

    def _convert_currency(
        self,
        amount: float,
        from_currency: str,
        to_currency: str
    ) -> float:
        """Convert amount between currencies (simplified)."""
        # Exchange rates (simplified - in production use real API)
        rates = {
            "LKR": 1.0,
            "USD": 300.0,
            "EUR": 330.0,
            "GBP": 380.0,
            "SGD": 225.0,
            "INR": 3.6,
        }

        from_rate = rates.get(from_currency, 1.0)
        to_rate = rates.get(to_currency, 1.0)

        # Convert to LKR first, then to target
        lkr_amount = amount * from_rate
        return lkr_amount / to_rate

    def _convert_to_lkr(self, amount: float, currency: str) -> float:
        """Convert amount to LKR."""
        return self._convert_currency(amount, currency, "LKR")

    def _get_required_approvals(
        self,
        agreement_value: float,
        currency: str
    ) -> List[Dict]:
        """Get required approvals for a given value."""
        lkr_value = self._convert_to_lkr(agreement_value, currency)

        if lkr_value >= 10000000:
            return [
                {"role": "ceo", "required": True},
                {"role": "cfo", "required": True},
                {"role": "legal", "required": True},
            ]
        elif lkr_value >= 5000000:
            return [
                {"role": "director", "required": True},
                {"role": "finance", "required": True},
            ]
        elif lkr_value >= 1000000:
            return [{"role": "manager", "required": True}]
        return []
