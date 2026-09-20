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

    # Find all agreements with external parties and aggregate.
    # Contract value lives in Agreement.data JSON, so aggregate in Python.
    supplier_rows = (
        await db.execute(
            select(
                ExternalParty.company_name,
                Agreement.id,
                Agreement.status,
                Agreement.data,
            )
            .join(Agreement, Agreement.id == ExternalParty.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                ExternalParty.company_name.is_not(None),
                ExternalParty.company_name != "",
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

    per_supplier: dict[str, dict] = {}
    for company, _aid, status, data in supplier_rows:
        agg = per_supplier.setdefault(
            company,
            {"contracts": 0, "value": 0.0, "active": 0, "pending": 0},
        )
        agg["contracts"] += 1
        agg["value"] += _value_of(data)
        if status in ("active", "executed"):
            agg["active"] += 1
        elif status in ("signing", "sent"):
            agg["pending"] += 1

    ordered = sorted(
        per_supplier.items(), key=lambda kv: kv[1]["contracts"], reverse=True
    )[:50]

    suppliers = []
    for company, agg in ordered:
        contract_count = agg["contracts"]
        total_value = agg["value"]
        active_count = agg["active"]
        pending_count = agg["pending"]

        # Obligations for this supplier
        obl_rows = (
            await db.execute(
                select(Obligation.status, Obligation.due_date)
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
        ).all()
        total_obl = len(obl_rows)
        completed_obl = sum(1 for s, _ in obl_rows if s == "completed")
        overdue_obl = sum(
            1
            for s, due in obl_rows
            if s not in ("completed", "cancelled")
            and due is not None
            and due < date.today()
        )

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
                "total": total_obl,
                "completed": completed_obl,
                "overdue": overdue_obl,
            },
            "renewals": renewal_count,
        })

    return {
        "suppliers": suppliers,
        "total_suppliers": len(suppliers),
    }
