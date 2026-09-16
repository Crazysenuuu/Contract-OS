"""Centralized entitlement enforcement (spec 1.24.16 / 1.24.38).

Feature access is centralized here rather than checked via ad-hoc
`if user.plan == ...` branches. The service evaluates Organization →
Subscription → Plan → Entitlement override → current usage before a feature
may be used. Endpoints are gated through the ``require_entitlement``
FastAPI dependency; authorization (agreement/domain access) and entitlement
are independent and BOTH must pass.

HTTP semantics (spec 1.24):
- 403 when the feature is not part of the plan / is disabled.
- 402 when the feature exists but the monthly usage limit is exhausted.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.tenant import get_current_organization_id
from app.services.billing_service import check_entitlement


class EntitlementService:
    """Service facade for plan/tenant entitlement evaluation."""

    @staticmethod
    async def require(
        db: AsyncSession,
        organization_id: uuid.UUID,
        feature_key: str,
    ) -> dict:
        """Return the entitlement when the feature is available (spec 1.24.16).

        Raises 403 when the feature is missing from the plan or disabled, or
        when the tenant has no active plan gating the feature.
        """
        entitlement = await check_entitlement(db, organization_id, feature_key)
        if not entitlement["enabled"] or entitlement["limit"] == 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Feature not available",
            )
        return entitlement

    @staticmethod
    async def check_usage(
        db: AsyncSession,
        organization_id: uuid.UUID,
        feature_key: str,
        quantity: int = 1,
    ) -> dict:
        """Verify headroom for quantity against the tenant's current usage."""
        entitlement = await EntitlementService.require(
            db, organization_id, feature_key
        )
        if entitlement["limit"] is not None and (
            entitlement["remaining"] or 0
        ) < quantity:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Usage limit reached",
            )
        return entitlement


def require_entitlement(
    feature_key: str,
    quantity: int = 1,
):
    """FastAPI dependency factory: gate an endpoint on an entitlement.

    Usage (spec 1.24.38):

        @router.post("/analyze")
        async def analyze(
            _: None = Depends(require_entitlement("ai_analyses")),
        ):
            ...
    """

    async def _dependency(
        org_id: uuid.UUID = Depends(get_current_organization_id),
        db: AsyncSession = Depends(get_db),
    ):
        return await EntitlementService.check_usage(
            db, org_id, feature_key, quantity
        )

    return _dependency