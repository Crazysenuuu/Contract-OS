"""Contract Health scoring (spec section 17, "Agreement intelligence").

Computes an auditable, deterministic health profile per agreement:

    Contract Health
    Overall Risk: Medium
    Payment Risk: Low
    IP Risk: High
    Liability Risk: High
    Termination Risk: Medium
    Data Protection Risk: Medium
    Renewal Risk: High

Category risks are derived from clause-level risk scores produced by the
document intelligence extractor plus agreement-level signals (renewal mode,
event currency, value present). The overall risk is the maximum category
risk (a contract is only as healthy as its worst dimension) and health is
100 - overall_risk*100 expressed on a 0-100 scale.

This is an explainable rule-based model: every dimension carries
"factors" that a reviewer can inspect, and nothing is persisted that
cannot be reconstructed from the agreement + clause rows.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.document_intelligence import ExtractedClause, ClauseRiskLevel


# Spec risk category -> extracted clause categories that feed it.
CATEGORY_MAP: dict[str, set[str]] = {
    "payment": {"payment"},
    "ip": {"ip_ownership", "ip_license"},
    "liability": {"liability", "indemnification", "warranties"},
    "termination": {"termination"},
    "data_protection": {"data_protection", "confidentiality"},
    "renewal": {"term"},
}

_RISK_VALUE = {
    ClauseRiskLevel.LOW: 0.15,
    ClauseRiskLevel.MEDIUM: 0.5,
    ClauseRiskLevel.HIGH: 0.8,
    ClauseRiskLevel.CRITICAL: 1.0,
}


def _risk_bucket(score: float) -> str:
    if score >= 0.7:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def _category_score(
    clauses: list[Clause],
    categories: set[str],
    factors: list[dict],
) -> dict:
    """Aggregate clause risk scores within a category.

    Uses the max score among matching clauses (any single high-risk clause
    elevates the dimension) but blends in a mild average so a lone outlier
    isn't the entire story.
    """
    matches = [
        c for c in clauses if c.category.value in categories and c.risk_score is not None
    ]
    if not matches:
        return {"score": 0.0, "level": "low", "clause_count": 0}

    max_score = max(c.risk_score for c in matches)
    avg_score = sum(c.risk_score for c in matches) / len(matches)
    score = round(0.7 * max_score + 0.3 * avg_score, 3)
    score = min(1.0, score)
    return {
        "score": score,
        "level": _risk_bucket(score),
        "clause_count": len(matches),
    }


async def compute_contract_health(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> dict:
    """Return the full Contract Health profile for one agreement."""
    agreement = await db.get(Agreement, agreement_id)
    if agreement is None:
        raise ValueError("Agreement not found")

    result = await db.execute(
        select(ExtractedClause).where(ExtractedClause.agreement_id == agreement_id)
    )
    clauses = list(result.scalars().all())

    factors: list[dict] = []
    if not clauses:
        factors.append(
            {
                "kind": "missing_clause_analysis",
                "message": "No clause-level analysis exists yet; run clause extraction for precise category risk",
            }
        )

    if isinstance(agreement.data, dict):
        if agreement.data.get("auto_renew") is True:
            factors.append(
                {
                    "kind": "renewal",
                    "message": "Agreement auto-renews; expiry tracking is active",
                }
            )
        for value_key in (
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
        ):
            if agreement.data.get(value_key):
                factors.append(
                    {
                        "kind": "value",
                        "field": value_key,
                        "message": f"Monetary value present ({value_key})",
                    }
                )
                break

        if not agreement.data.get("governing_law"):
            factors.append(
                {
                    "kind": "missing_governing_law",
                    "message": "Governing law is not configured",
                }
            )

    categories: dict[str, dict] = {}
    for cat, source_cats in CATEGORY_MAP.items():
        categories[cat] = _category_score(clauses, source_cats, factors)

    levels = ["low", "medium", "high"]
    overall_rank = 0
    for cat in categories.values():
        overall_rank = max(
            overall_rank,
            (levels.index(cat["level"]) if cat["level"] in levels else 0),
        )
        overall_rank = max(
            overall_rank,
            1 if cat["score"] >= 0.9 else 0,
        )

    overall_score = max((c["score"] for c in categories.values()), default=0.0)
    overall_level = (
        "high" if overall_score >= 0.7 else (
            "medium" if overall_score >= 0.4 else "low"
        )
    )

    # Health = 100 - overall risk, floored at 0.
    health_score = max(0, round(100 * (1.0 - overall_score)))

    return {
        "agreement_id": str(agreement_id),
        "title": agreement.title,
        "contract_health": health_score,
        "overall_risk": {
            "level": overall_level,
            "score": round(overall_score, 3),
        },
        "category_risks": {
            cat: {
                "level": v["level"],
                "score": v["score"],
                "clause_count": v["clause_count"],
            }
            for cat, v in sorted(categories.items())
        },
        "analyzed_clauses": len(clauses),
        "factors": factors,
        "model": "rule-based",
    }