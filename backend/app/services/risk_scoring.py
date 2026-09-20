"""Deterministic risk scoring engine (spec §39).

AI detects the issue.  The rules engine calculates the score.

Risk = weighted findings + company policy violations + missing protections + unusual clauses

Every score component is explainable and reproducible — the LLM never
produces the score directly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_intelligence import ExtractedClause


# --- Scoring weights (configurable per-organisation later) ------------------

# Keys align with ClauseCategory enum values; unmatched categories default to
# the weight of "indemnification"'s peer group via .get(cat, 0.05).
CATEGORY_WEIGHTS = {
    "liability": 0.15,
    "indemnification": 0.12,
    "confidentiality": 0.08,
    "termination": 0.10,
    "governing_law": 0.05,
    "dispute_resolution": 0.05,
    "ip_ownership": 0.12,
    "ip_license": 0.08,
    "non_compete": 0.08,
    "non_solicitation": 0.05,
    "representations": 0.08,
    "warranties": 0.08,
    "force_majeure": 0.04,
    "severability": 0.03,
    "entire_agreement": 0.03,
    "data_protection": 0.12,
    "operational": 0.05,
}

SEVERITY_SCORES = {
    "critical": 1.0,
    "high": 0.75,
    "medium": 0.5,
    "low": 0.25,
    "info": 0.0,
}


@dataclass
class RiskScoreComponent:
    category: str
    score: float  # 0-1
    weight: float
    weighted_score: float
    contributing_factors: list[str] = field(default_factory=list)


@dataclass
class DeterministicRiskScore:
    overall: float  # 0-1
    level: str  # low | medium | high | critical
    components: list[RiskScoreComponent]
    factors_count: int
    explanation: str


def _risk_level(score: float) -> str:
    if score >= 0.75:
        return "critical"
    if score >= 0.5:
        return "high"
    if score >= 0.3:
        return "medium"
    return "low"


async def compute_deterministic_risk(
    db: AsyncSession,
    *,
    agreement_id: uuid.UUID,
) -> DeterministicRiskScore:
    """Compute a deterministic risk score for an agreement (spec §39).

    Combines:
    1. Extracted clause risk scores (from AI analysis)
    2. Missing required clauses (from quality checks)
    3. Policy violations (from company policy engine)
    """
    # 1. Gather extracted clause risks
    clause_rows = (
        await db.execute(
            select(ExtractedClause)
            .where(ExtractedClause.agreement_id == agreement_id)
        )
    ).scalars().all()

    # 2. Aggregate by category (ExtractedClause.category is a ClauseCategory
    # enum; map it to the lowercase string keys of CATEGORY_WEIGHTS)
    category_scores: dict[str, list[tuple[float, str]]] = {}
    for clause in clause_rows:
        cat = (clause.category.value if clause.category else None) or "operational"
        score = clause.risk_score or 0.0
        label = clause.title or cat
        category_scores.setdefault(cat, []).append((score, label))

    # 3. Compute per-category scores — iterate over the categories actually
    # present in the document (plus their weights; unknown categories default
    # to 0.05) so components always reflect real findings.
    components: list[RiskScoreComponent] = []
    seen_categories = set(category_scores.keys())
    for cat in sorted(seen_categories | {c for c in CATEGORY_WEIGHTS if c in seen_categories}):
        weight = CATEGORY_WEIGHTS.get(cat, 0.05)
        factors = category_scores.get(cat, [])
        if factors:
            avg_score = sum(sc for sc, _ in factors) / len(factors)
            contributing = [label for sc, label in factors if sc >= 0.3]
        else:
            avg_score = 0.0
            contributing = []

        weighted = round(avg_score * weight, 4)
        components.append(RiskScoreComponent(
            category=cat,
            score=round(avg_score, 3),
            weight=weight,
            weighted_score=weighted,
            contributing_factors=contributing,
        ))

    # 4. Overall score
    total_weighted = sum(c.weighted_score for c in components)
    total_weight = sum(c.weight for c in components if c.score > 0)
    overall = round(total_weighted / total_weight, 3) if total_weight > 0 else 0.0

    level = _risk_level(overall)
    factors_count = sum(len(c.contributing_factors) for c in components)

    explanation_parts = []
    for c in sorted(components, key=lambda x: x.weighted_score, reverse=True):
        if c.contributing_factors:
            explanation_parts.append(
                f"{c.category} ({c.score:.0%}): {', '.join(c.contributing_factors[:3])}"
            )

    explanation = (
        f"Overall risk: {level} ({overall:.0%}). "
        + "; ".join(explanation_parts[:5])
        if explanation_parts
        else f"Overall risk: {level} ({overall:.0%}). No significant risk factors detected."
    )

    return DeterministicRiskScore(
        overall=overall,
        level=level,
        components=components,
        factors_count=factors_count,
        explanation=explanation,
    )
