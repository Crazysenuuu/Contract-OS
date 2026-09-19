"""Supplier risk and financial-obligation analysis (spec §19 supplier risk,
§ financial obligations).

A rule-based, jurisdiction-aware risk engine over financial obligations in a
supply / finance agreement, mirroring the explainable scoring of
``contract_health_service``. Every finding carries the clause / answer field
it derives from, and the aggregate profile exposes both a supplier-side risk
and a financial-obligation (borrower / buyer) risk so counterparties can see
their own exposure before execution.

Nothing here is persisted by design: the profile is reconstructed from the
agreement + its stored questions on every call, so the analysis can never
drift from the rendered document.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.services.agreement_renderer import (
    get_template_questions,
    resolve_template_key,
)

_financial_keys = {
    "principal_amount", "interest_rate", "repayment_term_months",
    "repayment_schedule", "fees", "royalty_rate", "licence_fee",
    "payment_schedule", "late_fee_percentage", "collateral_description",
    "guarantee_mechanism", "payment_trigger", "unit_price", "escalation_rate",
}


@dataclass
class RiskFactor:
    """A single explainable risk finding."""

    kind: str
    message: str
    field: str | None = None
    level: str = "medium"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message, "field": self.field, "level": self.level}


@dataclass
class RiskProfile:
    """Aggregate supplier / financial-obligation risk."""

    agreement_id: uuid.UUID | None
    overall: str = "low"
    score: float = 0.0
    category_risks: dict[str, dict] = field(default_factory=dict)
    factors: list[RiskFactor] = field(default_factory=list)
    obligations: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "agreement_id": str(self.agreement_id) if self.agreement_id else None,
            "overall": self.overall,
            "score": self.score,
            "category_risks": self.category_risks,
            "factors": [f.to_dict() for f in self.factors],
            "obligations": self.obligations,
        }


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _risk_level(score: float) -> str:
    if score <= 0.25:
        return "low"
    if score <= 0.6:
        return "medium"
    return "high"


def _category_score(factors: list[RiskFactor], level_weights: dict[str, float]) -> float:
    """Aggregate per-category score: max level weighted, blended with count."""
    if not factors:
        return 0.0
    max_level = max(f.level for f in factors)
    count = len(factors)
    return min(1.0, level_weights.get(max_level, 0.5) + 0.12 * max(0, count - 1))


def analyze_supplier_risk(
    db: AsyncSession,
    agreement: Agreement,
    answers: dict | None = None,
) -> RiskProfile:
    """Compute the supplier / financial-obligation risk profile.

    ``answers`` defaults to the agreement's stored answers so callers that
    already hold the row (e.g. the API layer) avoid a redundant query.
    """
    data = dict(agreement.data or {})
    if answers:
        data.update({k: v for k, v in answers.items() if v not in (None, "")})

    factors: list[RiskFactor] = []

    # --- Financial obligation cluster ------------------------------------
    fin_factors: list[RiskFactor] = []

    late_fee = _num(data.get("late_fee_percentage"))
    interest_rate = _num(data.get("interest_rate"))
    credit = _num(data.get("credit_limit")) or _num(data.get("principal_amount"))

    if late_fee is not None and late_fee >= 25:
        fin_factors.append(
            RiskFactor(
                "aggressive_late_fee",
                f"Late fee of {late_fee}% is materially above the 1%-2%/month norm; "
                "review the penalty for enforceability under the governing law.",
                field="late_fee_percentage",
                level="high",
            )
        )
    # Independent of the late-fee check: an agreement can carry both an
    # aggressive penalty and a usurious rate, and each is its own finding.
    if interest_rate is not None and interest_rate >= 30:
        fin_factors.append(
            RiskFactor(
                "high_interest_rate",
                f"Interest rate of {interest_rate}% exceeds typical lending norms; "
                "verify usury / max-rate compliance in the governing jurisdiction.",
                field="interest_rate",
                level="high",
            )
        )

    collateral = data.get("collateral_description") or data.get("security_deposit")
    if (credit or 0) > 0 and not collateral:
        fin_factors.append(
            RiskFactor(
                "unsecured_obligation",
                "A financial obligation exists with no recorded collateral or deposit; "
                "confirm credit risk is acceptable or require security.",
                field="principal_amount",
                level="medium",
            )
        )

    guarantee = data.get("guarantee_mechanism") or data.get("guarantor_name")
    if guarantee:
        fin_factors.append(
            RiskFactor(
                "third_party_guarantee",
                "Obligation is backed by a third-party guarantee; verify the "
                "guarantor's creditworthiness and the guarantee's enforceability.",
                field="guarantee_mechanism",
                level="medium",
            )
        )

    currency = data.get("currency")
    if currency and currency not in {"USD", "EUR", "LKR"} and (credit or 0) > 0:
        fin_factors.append(
            RiskFactor(
                "foreign_currency_obligation",
                f"Obligation is denominated in {currency}; assess FX hedging and "
                "repayment exposure.",
                field="currency",
                level="medium",
            )
        )

    # --- Supplier / supply-chain cluster ---------------------------------
    supply_factors: list[RiskFactor] = []

    exclusivity = data.get("exclusivity") or data.get("license_exclusivity")
    if exclusivity and str(exclusivity).strip().lower() in {"yes", "exclusive"}:
        supply_factors.append(
            RiskFactor(
                "exclusive_supply",
                "Exclusive arrangement creates supplier dependence; consider a "
                "second-source clause and supply-continuity milestones.",
                field="exclusivity",
                level="medium",
            )
        )

    escalation = _num(data.get("escalation_rate"))
    if escalation is not None and escalation >= 5:
        supply_factors.append(
            RiskFactor(
                "price_escalation",
                f"Annual price escalation of {escalation}% exceeds inflation norms; "
                "cap or link escalation to a transparent index.",
                field="escalation_rate",
                level="medium",
            )
        )

    single_source = str(data.get("single_source_supplier") or "").strip().lower()
    if single_source in {"yes", "true"}:
        supply_factors.append(
            RiskFactor(
                "single_source_dependence",
                "Single-source supplier: map a validated substitute and define "
                "business-continuity obligations.",
                field="single_source_supplier",
                level="high",
            )
        )

    # --- Obligations summary --------------------------------------------
    obligations: dict = {}
    if credit:
        obligations["total_obligation"] = {
            "amount": credit,
            "currency": currency or "LKR",
            "field": "principal_amount",
        }
    if late_fee is not None or interest_rate is not None:
        obligations["cost_of_carry"] = {
            "late_fee_percentage": late_fee,
            "interest_rate": interest_rate,
        }

    cat = {
        "financial": _category_score(
            fin_factors, {"low": 0.15, "medium": 0.5, "high": 0.85}
        ),
        "supplier": _category_score(
            supply_factors, {"low": 0.15, "medium": 0.5, "high": 0.85}
        ),
    }

    factors.extend(fin_factors)
    factors.extend(supply_factors)

    score = round(min(1.0, 0.6 * cat["financial"] + 0.4 * cat["supplier"]), 3)

    return RiskProfile(
        agreement_id=getattr(agreement, "id", None),
        overall=_risk_level(score),
        score=score,
        category_risks={
            "financial_obligations": {
                "level": _risk_level(cat["financial"]),
                "score": cat["financial"],
            },
            "supplier_dependence": {
                "level": _risk_level(cat["supplier"]),
                "score": cat["supplier"],
            },
        },
        factors=factors,
        obligations=obligations,
    )


async def get_supplier_risk(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> RiskProfile:
    """Load an agreement and run the supplier / financial-obligation analysis."""
    agreement = await db.get(Agreement, agreement_id)
    if agreement is None:
        raise ValueError("Agreement not found")
    return analyze_supplier_risk(db, agreement)