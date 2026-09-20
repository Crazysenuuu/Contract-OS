"""Attribute-based access control engine (spec §52).

Extends RBAC with attribute-level checks:

- User department must match agreement department (if both set)
- Agreement value must be within user's approval limit
- User clearance level must meet document sensitivity

These are *advisory* checks layered on top of the RBAC permission
system — a denied ABAC check does not block if the user has a
system-level role (owner/admin).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.user import User


@dataclass
class ABACVerdict:
    allowed: bool
    reason: str
    confidence: float = 1.0  # 0-1, how confident we are in the check


async def check_department_match(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    agreement_id: uuid.UUID,
) -> ABACVerdict:
    """Check if the user's department matches the agreement's department.

    Spec §52: User.department = Finance AND Contract.department = Finance.
    """
    user = await db.get(User, user_id)
    agreement = await db.get(Agreement, agreement_id)

    if user is None or agreement is None:
        return ABACVerdict(allowed=False, reason="user or agreement not found")

    user_dept = getattr(user, "department", None)
    agreement_dept = (agreement.data or {}).get("department")

    # If either side has no department set, skip the check (pass-through)
    if not user_dept or not agreement_dept:
        return ABACVerdict(
            allowed=True,
            reason="department not set on one or both sides — check skipped",
            confidence=0.3,
        )

    if user_dept.lower() == agreement_dept.lower():
        return ABACVerdict(allowed=True, reason="departments match")

    return ABACVerdict(
        allowed=False,
        reason=f"user department '{user_dept}' ≠ agreement department '{agreement_dept}'",
    )


async def check_approval_limit(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    agreement_id: uuid.UUID,
) -> ABACVerdict:
    """Check if the agreement value is within the user's approval limit.

    Spec §52: Contract.value < user's approval limit.
    """
    user = await db.get(User, user_id)
    agreement = await db.get(Agreement, agreement_id)

    if user is None or agreement is None:
        return ABACVerdict(allowed=False, reason="user or agreement not found")

    limit = getattr(user, "approval_limit", None)
    # Contract value is stored in the agreement's JSON data, not a column.
    raw_value = (agreement.data or {}).get("total_value") or (agreement.data or {}).get(
        "contract_value"
    )
    try:
        value = float(str(raw_value).replace(",", "")) if raw_value is not None else None
    except (TypeError, ValueError):
        value = None

    if limit is None:
        return ABACVerdict(
            allowed=True,
            reason="no approval limit set on user — check skipped",
            confidence=0.2,
        )

    if value is None:
        return ABACVerdict(
            allowed=True,
            reason="no contract value set — check skipped",
            confidence=0.3,
        )

    if float(value) <= float(limit):
        return ABACVerdict(
            allowed=True,
            reason=f"contract value {value} ≤ approval limit {limit}",
        )

    return ABACVerdict(
        allowed=False,
        reason=f"contract value {value} exceeds approval limit {limit}",
    )


async def check_sensitivity_clearance(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    classification: str,
) -> ABACVerdict:
    """Check if the user's clearance level meets the document classification.

    Spec §56: RESTRICTED requires security-cleared users only.
    """
    user = await db.get(User, user_id)
    if user is None:
        return ABACVerdict(allowed=False, reason="user not found")

    clearance = getattr(user, "clearance_level", None) or "INTERNAL"
    from app.models.document import DocumentClassification

    if DocumentClassification.is_at_least(classification, DocumentClassification.RESTRICTED):
        if clearance != "security_cleared":
            return ABACVerdict(
                allowed=False,
                reason=f"RESTRICTED document requires security clearance, user has '{clearance}'",
            )

    if DocumentClassification.is_at_least(classification, DocumentClassification.HIGHLY_CONFIDENTIAL):
        if clearance not in ("security_cleared", "senior_management"):
            return ABACVerdict(
                allowed=False,
                reason="HIGHLY_CONFIDENTIAL document requires senior management or security clearance",
            )

    return ABACVerdict(allowed=True, reason="clearance sufficient")
