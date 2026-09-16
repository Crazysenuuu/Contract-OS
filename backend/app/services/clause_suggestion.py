"""
AI Clause Suggestion Engine.

Suggests appropriate clauses based on jurisdiction, agreement type, and risk profile.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.jurisdiction import Jurisdiction, JurisdictionClause

logger = logging.getLogger(__name__)


@dataclass
class ClauseSuggestion:
    """A suggested clause for the contract."""

    clause_type: str
    name: str
    text: str
    explanation: str
    risk_level: str
    is_mandatory: bool
    alternatives: list[dict] = field(default_factory=list)
    confidence: float = 0.9


@dataclass
class RiskAssessment:
    """Detailed risk assessment for a clause."""

    category: str
    severity: str
    score: float  # 0-100
    description: str
    mitigation: str
    jurisdiction_impact: Optional[str] = None


class ClauseSuggestionEngine:
    """AI-powered clause suggestion engine."""

    # Risk categories for NDA
    NDA_RISK_CATEGORIES = {
        "confidentiality_scope": {
            "description": "Scope of confidential information",
            "recommended_scope": "specific",
            "high_risk_patterns": ["all information", "everything", "any and all"],
        },
        "confidentiality_period": {
            "description": "Duration of confidentiality obligation",
            "recommended_min_years": 1,
            "recommended_max_years": 5,
        },
        "permitted_disclosures": {
            "description": "Exceptions to confidentiality",
            "standard_exceptions": [
                "publicly available",
                "independently developed",
                "required by law",
                "approved in writing",
            ],
        },
        "return_of_materials": {
            "description": "Obligation to return or destroy materials",
            "should_include": ["return", "destroy", "certify"],
        },
        "remedies": {
            "description": "Remedies for breach",
            "should_include": ["injunctive_relief", "damages"],
            "high_risk_patterns": ["sole remedy", "limited to"],
        },
        "governing_law": {
            "description": "Applicable law",
            "risk_if_missing": "high",
        },
        "dispute_resolution": {
            "description": "How disputes are resolved",
            "options": ["arbitration", "mediation", "litigation"],
        },
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    async def suggest_clauses(
        self,
        jurisdiction_code: str,
        agreement_type: str = "mutual_nda",
        risk_tolerance: str = "medium",
    ) -> list[ClauseSuggestion]:
        """
        Suggest clauses based on jurisdiction and agreement type.

        Args:
            jurisdiction_code: ISO country code (e.g., "LK", "SG")
            agreement_type: Type of agreement
            risk_tolerance: "low", "medium", "high"

        Returns:
            List of clause suggestions
        """
        # Get jurisdiction
        jurisdiction = await self._get_jurisdiction(jurisdiction_code)

        # Get jurisdiction-specific clauses
        jurisdiction_clauses = await self._get_jurisdiction_clauses(
            jurisdiction.id if jurisdiction else None,
            agreement_type,
        )

        suggestions = []

        # Add jurisdiction-specific clauses
        for clause in jurisdiction_clauses:
            suggestions.append(ClauseSuggestion(
                clause_type=clause.clause_type,
                name=clause.name,
                text=clause.standard_text,
                explanation=f"Standard {jurisdiction.name if jurisdiction else 'international'} clause for {clause.clause_type}",
                risk_level=clause.risk_level,
                is_mandatory=clause.is_mandatory,
                alternatives=clause.alternative_texts or [],
                confidence=0.95 if clause.is_mandatory else 0.85,
            ))

        # Add standard NDA clauses if not already covered
        covered_types = {s.clause_type for s in suggestions}

        if "governing_law" not in covered_types:
            suggestions.append(self._suggest_governing_law(jurisdiction_code))

        if "dispute_resolution" not in covered_types:
            suggestions.append(self._suggest_dispute_resolution(jurisdiction_code))

        if "confidentiality" not in covered_types:
            suggestions.append(self._suggest_confidentiality(jurisdiction_code))

        if "termination" not in covered_types:
            suggestions.append(self._suggest_termination(jurisdiction_code))

        return suggestions

    async def assess_risks(
        self,
        clause_text: str,
        clause_type: str,
        jurisdiction_code: Optional[str] = None,
    ) -> list[RiskAssessment]:
        """
        Assess risks in a clause.

        Returns detailed risk assessments.
        """
        risks = []

        if clause_type == "confidentiality":
            risks.extend(self._assess_confidentiality_risks(clause_text))
        elif clause_type == "limitation_of_liability":
            risks.extend(self._assess_liability_risks(clause_text))
        elif clause_type == "termination":
            risks.extend(self._assess_termination_risks(clause_text))
        elif clause_type == "governing_law":
            risks.extend(self._assess_governing_law_risks(clause_text, jurisdiction_code))

        return risks

    async def compare_clauses(
        self,
        clause_type: str,
        text_a: str,
        text_b: str,
        jurisdiction_code: Optional[str] = None,
    ) -> dict:
        """Compare two versions of a clause and provide analysis."""
        risks_a = await self.assess_risks(text_a, clause_type, jurisdiction_code)
        risks_b = await self.assess_risks(text_b, clause_type, jurisdiction_code)

        score_a = sum(r.score for r in risks_a) / max(len(risks_a), 1)
        score_b = sum(r.score for r in risks_b) / max(len(risks_b), 1)

        return {
            "clause_type": clause_type,
            "version_a": {
                "risk_score": round(score_a, 1),
                "risks": [{"category": r.category, "severity": r.severity} for r in risks_a],
            },
            "version_b": {
                "risk_score": round(score_b, 1),
                "risks": [{"category": r.category, "severity": r.severity} for r in risks_b],
            },
            "recommendation": "version_a" if score_a < score_b else "version_b",
            "difference": abs(score_a - score_b),
        }

    async def _get_jurisdiction(self, code: str) -> Optional[Jurisdiction]:
        """Get jurisdiction by code."""
        result = await self.db.execute(
            select(Jurisdiction).where(
                Jurisdiction.code == code,
                Jurisdiction.is_active == True,
            )
        )
        return result.scalar_one_or_none()

    async def _get_jurisdiction_clauses(
        self,
        jurisdiction_id: Optional[UUID],
        agreement_type: str,
    ) -> list[JurisdictionClause]:
        """Get standard clauses for a jurisdiction."""
        if not jurisdiction_id:
            return []

        result = await self.db.execute(
            select(JurisdictionClause).where(
                JurisdictionClause.jurisdiction_id == jurisdiction_id,
            )
        )
        clauses = result.scalars().all()

        # Filter by agreement type
        filtered = []
        for clause in clauses:
            if clause.applies_to_types is None:
                filtered.append(clause)
            elif agreement_type in clause.applies_to_types or "all" in clause.applies_to_types:
                filtered.append(clause)

        return filtered

    def _suggest_governing_law(self, jurisdiction_code: str) -> ClauseSuggestion:
        """Suggest governing law clause."""
        jurisdiction_names = {
            "LK": "Sri Lanka",
            "SG": "Singapore",
            "US": "United States",
            "GB": "England and Wales",
            "IN": "India",
        }

        jurisdiction_name = jurisdiction_names.get(jurisdiction_code, "the agreed jurisdiction")

        return ClauseSuggestion(
            clause_type="governing_law",
            name="Governing Law",
            text=f"This Agreement shall be governed by and construed in accordance with the laws of {jurisdiction_name}.",
            explanation=f"Specifies that {jurisdiction_name} law governs the agreement",
            risk_level="high",
            is_mandatory=True,
            confidence=0.95,
        )

    def _suggest_dispute_resolution(self, jurisdiction_code: str) -> ClauseSuggestion:
        """Suggest dispute resolution clause."""
        arbitration_options = {
            "SG": "Singapore International Arbitration Centre (SIAC)",
            "LK": "Arbitration Centre of the Bar Association of Sri Lanka",
            "US": "American Arbitration Association (AAA)",
            "GB": "London Court of International Arbitration (LCIA)",
        }

        institution = arbitration_options.get(jurisdiction_code, "mutually agreed arbitral institution")

        return ClauseSuggestion(
            clause_type="dispute_resolution",
            name="Dispute Resolution",
            text=f"Any dispute arising out of or in connection with this Agreement shall be referred to and finally resolved by arbitration administered by the {institution} in accordance with the rules of the {institution} for the time being in force.",
            explanation=f"Disputes resolved through arbitration via {institution}",
            risk_level="medium",
            is_mandatory=True,
            confidence=0.9,
        )

    def _suggest_confidentiality(self, jurisdiction_code: str) -> ClauseSuggestion:
        """Suggest confidentiality clause."""
        return ClauseSuggestion(
            clause_type="confidentiality",
            name="Confidentiality",
            text="""Each party agrees to maintain the confidentiality of all Confidential Information received from the other party and shall not disclose such information to any third party without the prior written consent of the disclosing party. The receiving party shall use the Confidential Information solely for the purpose of evaluating and/or performing its obligations under this Agreement.""",
            explanation="Standard mutual confidentiality obligation",
            risk_level="medium",
            is_mandatory=False,
            confidence=0.85,
        )

    def _suggest_termination(self, jurisdiction_code: str) -> ClauseSuggestion:
        """Suggest termination clause."""
        return ClauseSuggestion(
            clause_type="termination",
            name="Termination",
            text="""Either party may terminate this Agreement by giving thirty (30) days prior written notice to the other party. The obligations of confidentiality shall survive termination of this Agreement for a period of two (2) years.""",
            explanation="Standard termination with survival of confidentiality",
            risk_level="low",
            is_mandatory=False,
            confidence=0.8,
        )

    def _assess_confidentiality_risks(self, text: str) -> list[RiskAssessment]:
        """Assess risks in confidentiality clause."""
        risks = []
        text_lower = text.lower()

        # Check for overly broad scope
        if any(pattern in text_lower for pattern in ["all information", "everything", "any and all"]):
            risks.append(RiskAssessment(
                category="scope",
                severity="high",
                score=75,
                description="Overly broad confidentiality scope",
                mitigation="Define confidential information more specifically",
            ))

        # Check for unlimited duration
        if "perpetual" in text_lower or "indefinite" in text_lower:
            risks.append(RiskAssessment(
                category="duration",
                severity="medium",
                score=50,
                description="Perpetual confidentiality obligation may be unenforceable",
                mitigation="Set a reasonable time limit (2-5 years)",
            ))

        # Check for lack of exceptions
        if not any(exc in text_lower for exc in ["publicly available", "independently developed", "required by law"]):
            risks.append(RiskAssessment(
                category="exceptions",
                severity="medium",
                score=45,
                description="No standard exceptions to confidentiality",
                mitigation="Add standard exceptions for publicly available info, independent development, and legal requirements",
            ))

        return risks

    def _assess_liability_risks(self, text: str) -> list[RiskAssessment]:
        """Assess risks in limitation of liability clause."""
        risks = []
        text_lower = text.lower()

        if "unlimited" in text_lower or "no limitation" in text_lower:
            risks.append(RiskAssessment(
                category="liability_cap",
                severity="critical",
                score=90,
                description="No limitation of liability",
                mitigation="Add a reasonable liability cap",
            ))

        if "sole remedy" in text_lower:
            risks.append(RiskAssessment(
                category="remedies",
                severity="high",
                score=70,
                description="Limited remedies may be insufficient",
                mitigation="Include additional remedies beyond damages",
            ))

        return risks

    def _assess_termination_risks(self, text: str) -> list[RiskAssessment]:
        """Assess risks in termination clause."""
        risks = []
        text_lower = text.lower()

        if "immediate" in text_lower and "without cause" in text_lower:
            risks.append(RiskAssessment(
                category="termination_rights",
                severity="medium",
                score=55,
                description="Immediate termination without cause may be problematic",
                mitigation="Require notice period or cause for termination",
            ))

        if "survive" not in text_lower and "confidentiality" in text_lower:
            risks.append(RiskAssessment(
                category="survival",
                severity="high",
                score=65,
                description="Confidentiality may not survive termination",
                mitigation="Explicitly state that confidentiality obligations survive",
            ))

        return risks

    def _assess_governing_law_risks(self, text: str, jurisdiction_code: Optional[str]) -> list[RiskAssessment]:
        """Assess risks in governing law clause."""
        risks = []

        if not jurisdiction_code:
            risks.append(RiskAssessment(
                category="jurisdiction_match",
                severity="medium",
                score=40,
                description="No jurisdiction context available for comparison",
                mitigation="Ensure governing law matches expected jurisdiction",
            ))

        return risks


# Singleton instance factory
def get_clause_suggestion_engine(db: AsyncSession) -> ClauseSuggestionEngine:
    """Get clause suggestion engine instance."""
    return ClauseSuggestionEngine(db)
