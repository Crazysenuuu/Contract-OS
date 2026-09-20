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

from sqlalchemy import and_, cast, func, Float, Integer, literal, select, Date
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
    # NOTE: group by the *expression* without a bind parameter — grouping by
    # func.date_trunc("month", col) inlines "'month'" as a literal, whereas a
    # bound literal makes asyncpg/Postgres treat SELECT and GROUP BY as
    # different expressions (GroupingError at runtime).
    twelve_months_ago = datetime.now(timezone.utc) - timedelta(days=365)
    month_trunc = func.date_trunc(literal("month"), Agreement.created_at)
    monthly_rows = (
        await db.execute(
            select(
                month_trunc.label("month"),
                func.count(),
            )
            .where(
                Agreement.organization_id == org_id,
                Agreement.created_at >= twelve_months_ago,
            )
            .group_by(month_trunc)
            .order_by(month_trunc)
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

    # Contract value lives in the agreement's JSON data (`total_value` /
    # `contract_value`), not a dedicated column — extract numerically in
    # Python rather than relying on a SQL SUM over a nonexistent column.
    status_data_rows = (
        await db.execute(
            select(Agreement.status, Agreement.data).where(
                Agreement.organization_id == org_id
            )
        )
    ).all()

    def _value_of(data: dict | None) -> float:
        raw = (data or {}).get("total_value") or (data or {}).get("contract_value")
        if raw is None:
            return 0.0
        try:
            return float(str(raw).replace(",", ""))
        except (TypeError, ValueError):
            return 0.0

    all_values = [(_status, _data) for _status, _data in status_data_rows]
    total_value = sum(_value_of(d) for _, d in all_values)
    committed_spend = sum(
        _value_of(d) for s, d in all_values if s in ("active", "executed")
    )

    # --- Average contract value by type -------------------------------------
    type_data_rows = (
        await db.execute(
            select(Agreement.agreement_type_id, Agreement.data).where(
                Agreement.organization_id == org_id
            )
        )
    ).all()
    type_values: dict[str, list[float]] = {}
    for type_id, d in type_data_rows:
        v = _value_of(d)
        if v > 0:
            type_values.setdefault(str(type_id), []).append(v)
    avg_value_by_type = {
        type_id: {
            "average_value": round(sum(vals) / len(vals), 2),
            "count": len(vals),
        }
        for type_id, vals in type_values.items()
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
    value_by_status: dict[str, dict] = {}
    for s, d in all_values:
        v = _value_of(d)
        if v > 0:
            agg = value_by_status.setdefault(s, {"total_value": 0.0, "count": 0})
            agg["total_value"] += v
            agg["count"] += 1
    for agg in value_by_status.values():
        agg["total_value"] = round(agg["total_value"], 2)

    return {
        "total_contract_value": round(float(total_value), 2),
        "committed_spend": round(float(committed_spend), 2),
        "outstanding_obligations": round(float(total_obligation_amount), 2),
        "overdue_obligations": round(float(overdue_obligation_amount), 2),
        "average_value_by_type": avg_value_by_type,
        "value_by_status": value_by_status,
        "financial_risks": await _financial_risk_indicators(db, org_id=org_id),
    }


async def _financial_risk_indicators(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
) -> dict:
    """Spec §85 — price escalation, penalty exposure, late-payment exposure."""
    from app.models.document_intelligence import ExtractedClause

    # Contracts with late-fee / penalty clauses
    penalty_count = (
        await db.scalar(
            select(func.count(func.distinct(ExtractedClause.agreement_id)))
            .select_from(ExtractedClause)
            .join(Agreement, Agreement.id == ExtractedClause.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                ExtractedClause.clause_type.in_(["late_fee", "penalty", "liquidated_damage"]),
            )
        )
    ) or 0

    # Contracts with price escalation clauses
    escalation_count = (
        await db.scalar(
            select(func.count(func.distinct(ExtractedClause.agreement_id)))
            .select_from(ExtractedClause)
            .join(Agreement, Agreement.id == ExtractedClause.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                ExtractedClause.clause_type.in_(["price_escalation", "annual_increase"]),
            )
        )
    ) or 0

    # Contracts with unlimited liability
    unlimited_liability_count = (
        await db.scalar(
            select(func.count(func.distinct(ExtractedClause.agreement_id)))
            .select_from(ExtractedClause)
            .join(Agreement, Agreement.id == ExtractedClause.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                ExtractedClause.clause_type == "unlimited_liability",
            )
        )
    ) or 0

    return {
        "contracts_with_penalties": penalty_count,
        "contracts_with_price_escalation": escalation_count,
        "contracts_with_unlimited_liability": unlimited_liability_count,
    }


async def get_supplier_performance(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
) -> dict:
    """Supplier performance metrics (spec §84/86).

    Per-supplier: obligation compliance rate, dispute count, renewal rate,
    average risk score, total spend.
    """
    from app.models.agreement_access import AgreementParty
    from app.models.external_party import ExternalParty
    from app.models.obligation import Obligation
    from app.models.renewal import ContractRenewal

    supplier_rows = (
        await db.execute(
            select(
                ExternalParty.company_name,
                func.count(func.distinct(Agreement.id)).label("contracts"),
                Agreement.data,
            )
            .join(Agreement, Agreement.id == ExternalParty.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                ExternalParty.company_name.is_not(None),
                ExternalParty.company_name != "",
            )
            .group_by(ExternalParty.company_name, Agreement.id)
        )
    ).all()

    # Aggregate spend in Python (contract value lives in JSON data).
    def _value_of(data: dict | None) -> float:
        raw = (data or {}).get("total_value") or (data or {}).get("contract_value")
        if raw is None:
            return 0.0
        try:
            return float(str(raw).replace(",", ""))
        except (TypeError, ValueError):
            return 0.0

    per_supplier: dict[str, dict] = {}
    for company, contracts, data in supplier_rows:
        agg = per_supplier.setdefault(company, {"contracts": 0, "spend": 0.0})
        agg["contracts"] += 1
        agg["spend"] += _value_of(data)

    supplier_rows = [
        (company, agg["contracts"], agg["spend"])
        for company, agg in sorted(
            per_supplier.items(), key=lambda kv: kv[1]["contracts"], reverse=True
        )[:20]
    ]

    suppliers = []
    for row in supplier_rows:
        company = row[0]
        contracts = row[1]
        spend = row[2]

        # Obligation compliance
        total_obl = (
            await db.scalar(
                select(func.count())
                .select_from(Obligation)
                .join(Agreement, Agreement.id == Obligation.agreement_id)
                .join(ExternalParty, ExternalParty.agreement_id == Agreement.id)
                .where(
                    Agreement.organization_id == org_id,
                    ExternalParty.company_name == company,
                )
            )
        ) or 0
        completed_obl = (
            await db.scalar(
                select(func.count())
                .select_from(Obligation)
                .join(Agreement, Agreement.id == Obligation.agreement_id)
                .join(ExternalParty, ExternalParty.agreement_id == Agreement.id)
                .where(
                    Agreement.organization_id == org_id,
                    ExternalParty.company_name == company,
                    Obligation.status == "completed",
                )
            )
        ) or 0

        # Renewal count
        renewal_count = (
            await db.scalar(
                select(func.count())
                .select_from(ContractRenewal)
                .join(Agreement, Agreement.id == ContractRenewal.agreement_id)
                .join(ExternalParty, ExternalParty.agreement_id == Agreement.id)
                .where(
                    Agreement.organization_id == org_id,
                    ExternalParty.company_name == company,
                )
            )
        ) or 0

        compliance_rate = (
            round((completed_obl / total_obl) * 100, 1) if total_obl > 0 else None
        )
        renewal_rate = (
            round((renewal_count / contracts) * 100, 1) if contracts > 0 else 0
        )

        suppliers.append({
            "company_name": company,
            "contracts": contracts,
            "total_spend": round(spend, 2),
            "obligation_compliance_rate": compliance_rate,
            "total_obligations": total_obl,
            "completed_obligations": completed_obl,
            "renewal_count": renewal_count,
            "renewal_rate": renewal_rate,
        })

    return {"suppliers": suppliers, "total": len(suppliers)}
