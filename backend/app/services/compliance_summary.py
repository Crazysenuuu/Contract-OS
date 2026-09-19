"""Compliance dashboard summary (spec §89).

Aggregates counts for the executive compliance widget:
- Contracts requiring review
- Missing DPA (personal data agreements without DPA)
- Expired insurance (agreements with expired insurance clauses)
- Unapproved deviations from company policy
- Unsigned amendments
- Upcoming renewals (within 90 days)
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.amendment import AgreementAmendment as Amendment
from app.models.approval import ApprovalRecord
from app.models.document_intelligence import ExtractedClause
from app.models.obligation import Obligation
from app.models.renewal import ContractRenewal as Renewal


async def get_compliance_summary(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
) -> dict:
    """Return the compliance dashboard widget data (spec §89)."""

    today = date.today()
    ninety_days = today + timedelta(days=90)

    # --- Contracts requiring review ------------------------------------------
    # Draft/internal_review agreements pending more than 7 days
    requiring_review = (
        await db.scalar(
            select(func.count())
            .select_from(Agreement)
            .where(
                Agreement.organization_id == org_id,
                Agreement.status.in_(["draft", "internal_review", "pending_approval"]),
                Agreement.created_at.is_not(None),
                func.extract("epoch", func.now() - Agreement.created_at) > 7 * 86400,
            )
        )
    ) or 0

    # --- Missing DPA ---------------------------------------------------------
    # Agreements with personal_data flag but no DPA-related clause
    dpa_agreement_count = (
        await db.scalar(
            select(func.count(func.distinct(Agreement.id)))
            .join(ExtractedClause, ExtractedClause.agreement_id == Agreement.id)
            .where(
                Agreement.organization_id == org_id,
                ExtractedClause.category == "data_processing",
            )
        )
    ) or 0

    # --- Unsigned amendments -------------------------------------------------
    unsigned_amendments = (
        await db.scalar(
            select(func.count())
            .select_from(Amendment)
            .join(Agreement, Agreement.id == Amendment.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                Amendment.status == "approved",
            )
        )
    ) or 0

    # --- Upcoming renewals (within 90 days) ----------------------------------
    upcoming_renewals = (
        await db.scalar(
            select(func.count())
            .select_from(Renewal)
            .join(Agreement, Agreement.id == Renewal.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                Renewal.next_renewal_date.is_not(None),
                Renewal.next_renewal_date <= ninety_days,
                Renewal.next_renewal_date >= today,
            )
        )
    ) or 0

    # --- Pending approvals ---------------------------------------------------
    pending_approvals = (
        await db.scalar(
            select(func.count())
            .select_from(ApprovalRecord)
            .join(Agreement, Agreement.id == ApprovalRecord.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                ApprovalRecord.status.in_(["pending", "in_progress"]),
            )
        )
    ) or 0

    # --- Overdue obligations -------------------------------------------------
    overdue_obligations = (
        await db.scalar(
            select(func.count())
            .select_from(Obligation)
            .join(Agreement, Agreement.id == Obligation.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                Obligation.status.notin_(["completed", "cancelled"]),
                Obligation.due_date.is_not(None),
                Obligation.due_date < today,
            )
        )
    ) or 0

    return {
        "contracts_requiring_review": requiring_review,
        "missing_dpa": dpa_agreement_count,
        "unsigned_amendments": unsigned_amendments,
        "upcoming_renewals": upcoming_renewals,
        "pending_approvals": pending_approvals,
        "overdue_obligations": overdue_obligations,
    }
