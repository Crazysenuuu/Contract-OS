"""Contract analytics service (spec §84-85).

Deterministic aggregation queries over agreement + obligation + approval data
for the executive analytics dashboard.  All functions are pure DB reads
scoped to one organisation — no AI, no LLM, no magic numbers.

Metrics covered (spec §84):
- Contract volume by status / type / month
- Cycle time (created → executed)
- Approval time
- Signature time
- Renewal rate / termination rate
- Obligation compliance rate
- Risk distribution (from extracted clauses)

Financial analytics (spec §85):
- Total contract value
- Committed spend (active agreements)
- Payment obligation amounts
- Average contract value by type
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, cast, func, Float, Integer, select, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement, AgreementVersion
from app.models.approval import ApprovalRecord, ApprovalDecision
from app.models.document_intelligence import ExtractedClause
from app.models.lifecycle import AgreementState
from app.models.obligation import Obligation
from app.models.renewal import ContractRenewal as Renewal


async def get_executive_analytics(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
) -> dict:
    """Return the full executive analytics payload (spec §84)."""

    # --- Volume by status ---------------------------------------------------
    status_rows = (
        await db.execute(
            select(Agreement.status, func.count())
            .where(Agreement.organization_id == org_id)
            .group_by(Agreement.status)
        )
    ).all()
    volume_by_status = {row[0]: row[1] for row in status_rows}

    # --- Volume by agreement type -------------------------------------------
    type_rows = (
        await db.execute(
            select(
                Agreement.agreement_type_id,
                func.count(),
            )
            .where(Agreement.organization_id == org_id)
            .group_by(Agreement.agreement_type_id)
        )
    ).all()
    volume_by_type = {str(row[0]): row[1] for row in type_rows}

    # --- Monthly creation trend (last 12 months) ---------------------------
    twelve_months_ago = datetime.now(timezone.utc) - timedelta(days=365)
    monthly_rows = (
        await db.execute(
            select(
                func.date_trunc("month", Agreement.created_at).label("month"),
                func.count(),
            )
            .where(
                Agreement.organization_id == org_id,
                Agreement.created_at >= twelve_months_ago,
            )
            .group_by(func.date_trunc("month", Agreement.created_at))
            .order_by(func.date_trunc("month", Agreement.created_at))
        )
    ).all()
    monthly_trend = [
        {"month": row[0].isoformat() if row[0] else None, "count": row[1]}
        for row in monthly_rows
    ]

    # --- Cycle time: created → executed (spec §84) -------------------------
    executed_agreements = (
        await db.scalars(
            select(Agreement)
            .where(
                Agreement.organization_id == org_id,
                Agreement.status.in_(["executed", "active", "terminated", "expired"]),
                Agreement.execution_date.is_not(None),
                Agreement.created_at.is_not(None),
            )
        )
    ).all()

    cycle_times = []
    for a in executed_agreements:
        if a.execution_date and a.created_at:
            delta = (a.execution_date - a.created_at.replace(tzinfo=None)).days
            if delta >= 0:
                cycle_times.append(delta)

    avg_cycle_days = (
        round(sum(cycle_times) / len(cycle_times), 1) if cycle_times else 0
    )
    median_cycle_days = (
        sorted(cycle_times)[len(cycle_times) // 2] if cycle_times else 0
    )

    # --- Approval time: created_at → approved_at on ApprovalRecord ----------
    approval_decisions = (
        await db.scalars(
            select(ApprovalDecision)
            .join(ApprovalRecord, ApprovalRecord.id == ApprovalDecision.record_id)
            .join(Agreement, Agreement.id == ApprovalRecord.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                ApprovalDecision.decision == "approved",
                ApprovalDecision.decided_at.is_not(None),
            )
        )
    ).all()

    approval_times = []
    for ad in approval_decisions:
        if ad.decided_at and ad.created_at:
            delta = (ad.decided_at - ad.created_at.replace(tzinfo=None)).days
            if delta >= 0:
                approval_times.append(delta)

    avg_approval_days = (
        round(sum(approval_times) / len(approval_times), 1)
        if approval_times
        else 0
    )

    # --- Renewal rate (spec §84) --------------------------------------------
    total_active = volume_by_status.get("active", 0) + volume_by_status.get(
        "executed", 0
    )
    renewal_count = (
        await db.scalar(
            select(func.count())
            .select_from(Renewal)
            .join(Agreement, Agreement.id == Renewal.agreement_id)
            .where(Agreement.organization_id == org_id)
        )
    ) or 0
    renewal_rate = (
        round((renewal_count / total_active) * 100, 1) if total_active > 0 else 0
    )

    # --- Termination rate ---------------------------------------------------
    terminated_count = volume_by_status.get("terminated", 0)
    termination_rate = (
        round(
            (terminated_count / (total_active + terminated_count)) * 100, 1
        )
        if (total_active + terminated_count) > 0
        else 0
    )

    # --- Obligation compliance rate (spec §84) ------------------------------
    total_obligations = (
        await db.scalar(
            select(func.count())
            .select_from(Obligation)
            .join(Agreement, Agreement.id == Obligation.agreement_id)
            .where(Agreement.organization_id == org_id)
        )
    ) or 0
    completed_obligations = (
        await db.scalar(
            select(func.count())
            .select_from(Obligation)
            .join(Agreement, Agreement.id == Obligation.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                Obligation.status == "completed",
            )
        )
    ) or 0
    obligation_compliance_rate = (
        round((completed_obligations / total_obligations) * 100, 1)
        if total_obligations > 0
        else 0
    )

    overdue_obligations = (
        await db.scalar(
            select(func.count())
            .select_from(Obligation)
            .join(Agreement, Agreement.id == Obligation.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                Obligation.status.notin_(["completed", "cancelled"]),
                Obligation.due_date.is_not(None),
                Obligation.due_date < date.today(),
            )
        )
    ) or 0

    # --- Risk distribution (spec §84) ---------------------------------------
    risk_rows = (
        await db.execute(
            select(
                ExtractedClause.risk_score,
                func.count(),
            )
            .join(Agreement, Agreement.id == ExtractedClause.agreement_id)
            .where(Agreement.organization_id == org_id)
            .group_by(ExtractedClause.risk_score)
        )
    ).all()

    risk_distribution = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for score, count in risk_rows:
        if score is None:
            continue
        if score >= 0.7:
            risk_distribution["critical"] += count
        elif score >= 0.5:
            risk_distribution["high"] += count
        elif score >= 0.3:
            risk_distribution["medium"] += count
        else:
            risk_distribution["low"] += count

    return {
        "volume": {
            "total": sum(volume_by_status.values()),
            "by_status": volume_by_status,
            "by_type": volume_by_type,
            "monthly_trend": monthly_trend,
        },
        "cycle_time": {
            "average_days": avg_cycle_days,
            "median_days": median_cycle_days,
            "sample_size": len(cycle_times),
        },
        "approval_time": {
            "average_days": avg_approval_days,
            "sample_size": len(approval_times),
        },
        "rates": {
            "renewal_rate": renewal_rate,
            "termination_rate": termination_rate,
            "obligation_compliance_rate": obligation_compliance_rate,
            "execution_rate": (
                round(
                    (
                        (volume_by_status.get("executed", 0) + volume_by_status.get("active", 0))
                        / sum(volume_by_status.values())
                    )
                    * 100,
                    1,
                )
                if sum(volume_by_status.values()) > 0
                else 0
            ),
        },
        "obligations": {
            "total": total_obligations,
            "completed": completed_obligations,
            "overdue": overdue_obligations,
        },
        "risk": risk_distribution,
    }


async def get_financial_analytics(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
) -> dict:
    """Financial contract analytics (spec §85).

    Returns committed spend, payment obligations, and value-by-type.
    """

    # --- Total contract value across all agreements -------------------------
    total_value = (
        await db.scalar(
            select(func.coalesce(func.sum(Agreement.contract_value), 0))
            .where(
                Agreement.organization_id == org_id,
                Agreement.contract_value.is_not(None),
            )
        )
    ) or 0

    # --- Committed spend (active + executed) --------------------------------
    committed_spend = (
        await db.scalar(
            select(func.coalesce(func.sum(Agreement.contract_value), 0))
            .where(
                Agreement.organization_id == org_id,
                Agreement.status.in_(["active", "executed"]),
                Agreement.contract_value.is_not(None),
            )
        )
    ) or 0

    # --- Average contract value by type -------------------------------------
    avg_value_rows = (
        await db.execute(
            select(
                Agreement.agreement_type_id,
                func.avg(Agreement.contract_value),
                func.count(),
            )
            .where(
                Agreement.organization_id == org_id,
                Agreement.contract_value.is_not(None),
            )
            .group_by(Agreement.agreement_type_id)
        )
    ).all()
    avg_value_by_type = {
        str(row[0]): {
            "average_value": round(float(row[1]), 2) if row[1] else 0,
            "count": row[2],
        }
        for row in avg_value_rows
    }

    # --- Outstanding payment obligations ------------------------------------
    # Obligation.amount is a string; sum numerically in Python for safety.
    obligation_amounts = (
        await db.scalars(
            select(Obligation.amount)
            .join(Agreement, Agreement.id == Obligation.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                Obligation.status.notin_("completed", "cancelled", "waived"),
                Obligation.amount.is_not(None),
            )
        )
    ).all()

    def _safe_float(v: str | None) -> float:
        try:
            return float(str(v).replace(",", ""))
        except (ValueError, TypeError):
            return 0.0

    total_obligation_amount = sum(_safe_float(a) for a in obligation_amounts)

    overdue_amounts = (
        await db.scalars(
            select(Obligation.amount)
            .join(Agreement, Agreement.id == Obligation.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                Obligation.status.notin_("completed", "cancelled", "waived"),
                Obligation.amount.is_not(None),
                Obligation.due_date.is_not(None),
                Obligation.due_date < date.today(),
            )
        )
    ).all()
    overdue_obligation_amount = sum(_safe_float(a) for a in overdue_amounts)

    # --- Value by status (risk exposure) ------------------------------------
    value_by_status_rows = (
        await db.execute(
            select(
                Agreement.status,
                func.coalesce(func.sum(Agreement.contract_value), 0),
                func.count(),
            )
            .where(
                Agreement.organization_id == org_id,
                Agreement.contract_value.is_not(None),
            )
            .group_by(Agreement.status)
        )
    ).all()
    value_by_status = {
        row[0]: {
            "total_value": round(float(row[1]), 2),
            "count": row[2],
        }
        for row in value_by_status_rows
    }

    return {
        "total_contract_value": round(float(total_value), 2),
        "committed_spend": round(float(committed_spend), 2),
        "outstanding_obligations": round(float(total_obligation_amount), 2),
        "overdue_obligations": round(float(overdue_obligation_amount), 2),
        "average_value_by_type": avg_value_by_type,
        "value_by_status": value_by_status,
    }
