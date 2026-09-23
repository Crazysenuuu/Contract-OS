"""Per-organization agreement numbering (spec 2.01 §46).

The application generates a human-readable business reference:

    AGR-<org prefix>-<seq>          e.g. AGR-A1B2C3D4-00042

- The UUID stays the primary database identity; the reference is display and
  search only (spec: "Don't depend on it as the primary database identity").
- The org prefix is the first 8 hex chars of the organization UUID, which
  makes references globally distinctive without extra configuration.
- Sequences are strictly per-organization and gap-free under normal
  operation: concurrent creators serialize on the organization row lock,
  then read the committed MAX.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.organization import Organization


def format_agreement_number(organization_id: uuid.UUID, seq: int) -> str:
    """Deterministic reference format for an organization and sequence."""
    prefix = organization_id.hex[:8].upper()
    return f"AGR-{prefix}-{seq:05d}"


def parse_agreement_number(number: str) -> tuple[str, int] | None:
    """Returns (org_prefix, seq) for a valid reference, else None."""
    parts = number.split("-")
    if len(parts) != 3 or parts[0] != "AGR":
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None


async def next_agreement_number(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> str:
    """Allocate the next sequential reference for an organization.

    Must be called inside the same transaction that inserts the agreement so
    the number and the row commit atomically. The organization row is locked
    FOR UPDATE first, so concurrent creations block until the earlier
    transaction commits and then observe its MAX. The organization row is the
    serialization point because it always exists — unlike agreement rows,
    which may be absent for a brand-new org (and are deletable).
    """
    # Serialize per org: block concurrent allocators until we commit.
    await db.execute(
        select(Organization.id)
        .where(Organization.id == organization_id)
        .with_for_update()
    )

    result = await db.execute(
        select(func.max(Agreement.agreement_number)).where(
            Agreement.organization_id == organization_id,
            Agreement.agreement_number.isnot(None),
        )
    )
    current = result.scalar_one_or_none()

    seq = 1
    if current is not None:
        parsed = parse_agreement_number(current)
        if parsed is not None:
            seq = parsed[1] + 1

    return format_agreement_number(organization_id, seq)
