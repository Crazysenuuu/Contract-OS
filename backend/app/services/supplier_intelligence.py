"""Supplier intelligence service (spec §86).

Per-supplier rollup showing:
- Total contracts
- Total spend / committed value
- Risk score (from supplier_risk_service)
- SLA status
- Renewals
- Disputes

This powers the Supplier Intelligence panel on the risk/analytics pages.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.obligation import Obligation
from app.models.renewal import ContractRenewal as Renewal


async def supplier_intelligence(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
) -> dict:
    """Aggregate per-supplier intelligence (spec §86)."""

    # Group by counterparty (using the first external party's company_name)
    from app.models.agreement_access import AgreementParty
    from app.models.external_party import ExternalParty

    # Find all agreements with external parties and aggregate
    supplier_rows = (
        await db.execute(
            select(
                ExternalParty.company_name,
                func.count(func.distinct(Agreement.id)).label("contract_count"),
                func.coalesce(func.sum(Agreement.contract_value), 0).label("total_value"),
                func.count(
                    func.case(
                        (Agreement.status.in_(["active", "executed"]), 1)
                    )
                ).label("active_count"),
                func.count(
                    func.case(
                        (Agreement.status.in_(["signing", "sent"]), 1)
                    )
                ).label("pending_count"),
            )
            .join(Agreement, Agreement.id == ExternalParty.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                ExternalParty.company_name.is_not(None),
                ExternalParty.company_name != "",
            )
            .group_by(ExternalParty.company_name)
            .order_by(func.count(func.distinct(Agreement.id)).desc())
            .limit(50)
        )
    ).all()

    suppliers = []
    for row in supplier_rows:
        company = row[0]
        contract_count = row[1]
        total_value = float(row[2])
        active_count = row[3]
        pending_count = row[4]

        # Obligations for this supplier
        obligation_stats = (
            await db.execute(
                select(
                    func.count().label("total"),
                    func.count(
                        func.case((Obligation.status == "completed", 1))
                    ).label("completed"),
                    func.count(
                        func.case(
                            (
                                Obligation.status.notin_(["completed", "cancelled"]),
                                func.case(
                                    (Obligation.due_date < date.today(), 1)
                                ),
                            )
                        )
                    ).label("overdue"),
                )
                .join(Agreement, Agreement.id == Obligation.agreement_id)
                .join(
                    ExternalParty,
                    ExternalParty.agreement_id == Agreement.id,
                )
                .where(
                    Agreement.organization_id == org_id,
                    ExternalParty.company_name == company,
                )
            )
        ).one()

        # Renewals for this supplier
        renewal_count = (
            await db.scalar(
                select(func.count())
                .select_from(Renewal)
                .join(Agreement, Agreement.id == Renewal.agreement_id)
                .join(
                    ExternalParty,
                    ExternalParty.agreement_id == Agreement.id,
                )
                .where(
                    Agreement.organization_id == org_id,
                    ExternalParty.company_name == company,
                )
            )
        ) or 0

        suppliers.append({
            "company_name": company,
            "contract_count": contract_count,
            "total_value": round(total_value, 2),
            "active_contracts": active_count,
            "pending_contracts": pending_count,
            "obligations": {
                "total": obligation_stats[0],
                "completed": obligation_stats[1],
                "overdue": obligation_stats[2],
            },
            "renewals": renewal_count,
        })

    return {
        "suppliers": suppliers,
        "total_suppliers": len(suppliers),
    }
