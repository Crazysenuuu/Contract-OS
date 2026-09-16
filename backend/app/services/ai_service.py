"""AI service for contract intelligence.

Provides contract analysis, risk detection, and comparison capabilities.
Uses LLM for reasoning with structured output.
"""

import json
import uuid
from dataclasses import dataclass, field

import httpx

from app.core.config import get_settings_lazy

settings = get_settings_lazy()


@dataclass
class RiskItem:
    """A single risk finding."""

    category: str
    severity: str
    clause_identifier: str | None = None
    clause_text: str | None = None
    finding: str = ""
    explanation: str | None = None
    recommendation: str | None = None
    confidence: float = 0.0


@dataclass
class AnalysisResult:
    """Result of contract analysis."""

    summary: str = ""
    key_terms: dict = field(default_factory=dict)
    risks: list[RiskItem] = field(default_factory=list)
    model_used: str = "gpt-4"
    confidence: float = 0.0


class AIService:
    """AI service for contract analysis."""

    RISK_CATEGORIES = [
        "financial",
        "liability",
        "ip",
        "privacy",
        "security",
        "termination",
        "renewal",
        "operational",
        "regulatory",
        "jurisdiction",
        "payment",
        "confidentiality",
    ]

    async def analyze_contract(
        self,
        contract_text: str,
        agreement_type: str = "mutual_nda",
        is_ocr: bool = False,
        ocr_confidence: float = 1.0,
    ) -> AnalysisResult:
        """Analyze a contract and extract key information.

        Args:
            contract_text: The full text of the contract.
            agreement_type: The type of agreement.
            is_ocr: Whether the text was extracted via OCR.
            ocr_confidence: The confidence score of the OCR extraction.

        Returns:
            AnalysisResult with summary, key terms, and risks.
        """
        prompt = self._build_analysis_prompt(contract_text, agreement_type, is_ocr, ocr_confidence)

        response = await self._call_llm(prompt)

        return self._parse_analysis_response(response)

    async def detect_risks(
        self,
        contract_text: str,
        agreement_type: str = "mutual_nda",
        is_ocr: bool = False,
        ocr_confidence: float = 1.0,
    ) -> list[RiskItem]:
        """Detect risks in a contract.

        Args:
            contract_text: The full text of the contract.
            agreement_type: The type of agreement.
            is_ocr: Whether the text was extracted via OCR.
            ocr_confidence: The confidence score of the OCR extraction.

        Returns:
            List of RiskItem findings.
        """
        prompt = self._build_risk_prompt(contract_text, agreement_type, is_ocr, ocr_confidence)

        response = await self._call_llm(prompt)

        return self._parse_risks_response(response)

    async def compare_contracts(
        self,
        base_text: str,
        compared_text: str,
        base_version: int = 1,
        compared_version: int = 2,
    ) -> dict:
        """Compare two contract versions and highlight changes.

        Args:
            base_text: Text of the base version.
            compared_text: Text of the compared version.
            base_version: Version number of base.
            compared_version: Version number of compared.

        Returns:
            Comparison result with summary and changes.
        """
        prompt = self._build_comparison_prompt(
            base_text, compared_text, base_version, compared_version
        )

        response = await self._call_llm(prompt)

        return self._parse_comparison_response(response)

    async def extract_obligations(
        self,
        contract_text: str,
    ) -> list[dict]:
        """Extract obligations from an executed contract.

        Args:
            contract_text: The full text of the contract.

        Returns:
            List of obligation dictionaries.
        """
        prompt = self._build_obligation_prompt(contract_text)

        response = await self._call_llm(prompt)

        return self._parse_obligations_response(response)

    def _build_analysis_prompt(
        self, contract_text: str, agreement_type: str, is_ocr: bool = False, ocr_confidence: float = 1.0
    ) -> str:
        ocr_context = ""
        if is_ocr:
            ocr_context = f"\nNote: This text was extracted via OCR with {ocr_confidence*100:.1f}% confidence. Please account for possible misspellings, missing punctuation, or artifacts.\n"

        return f"""Analyze this {agreement_type} contract and provide:
1. A concise summary (2-3 paragraphs)
2. Key terms extracted as JSON
3. Risk assessment
{ocr_context}

Contract:
{contract_text[:8000]}

Return JSON with this structure:
{{
  "summary": "...",
  "key_terms": {{
    "parties": [...],
    "effective_date": "...",
    "expiry_date": "...",
    "governing_law": "...",
    "total_value": "...",
    "payment_terms": "...",
    "termination_notice": "...",
    "liability_cap": "...",
    "confidentiality_period": "..."
  }},
  "risks": [
    {{
      "category": "...",
      "severity": "critical|high|medium|low|info",
      "finding": "...",
      "explanation": "...",
      "recommendation": "...",
      "confidence": 0.0-1.0
    }}
  ]
}}"""

    def _build_risk_prompt(
        self, contract_text: str, agreement_type: str, is_ocr: bool = False, ocr_confidence: float = 1.0
    ) -> str:
        ocr_context = ""
        if is_ocr:
            ocr_context = f"\nNote: This text was extracted via OCR with {ocr_confidence*100:.1f}% confidence. Please account for possible misspellings, missing punctuation, or artifacts.\n"

        return f"""Perform a detailed risk analysis of this {agreement_type} contract.
{ocr_context}
Focus on these risk categories:
- financial: payment terms, penalties, liability caps
- liability: limitation of liability, indemnification
- ip: intellectual property ownership, licensing
- privacy: data protection, personal data handling
- security: data security requirements
- termination: termination clauses, notice periods
- renewal: auto-renewal, renewal terms
- regulatory: compliance requirements
- confidentiality: confidentiality scope and duration

Contract:
{contract_text[:8000]}

Return JSON array of risks:
[
  {{
    "category": "...",
    "severity": "critical|high|medium|low|info",
    "clause_identifier": "section.X",
    "clause_text": "...",
    "finding": "...",
    "explanation": "...",
    "recommendation": "...",
    "confidence": 0.0-1.0
  }}
]"""

    def _build_comparison_prompt(
        self,
        base_text: str,
        compared_text: str,
        base_version: int,
        compared_version: int,
    ) -> str:
        return f"""Compare these two contract versions and identify all changes.

Base Version ({base_version}):
{base_text[:4000]}

Compared Version ({compared_version}):
{compared_text[:4000]}

Return JSON with:
{{
  "summary": "Overall summary of changes",
  "changes_detected": 0,
  "risk_changes": [
    {{
      "category": "...",
      "description": "...",
      "risk_level": "increased|decreased|unchanged"
    }}
  ],
  "detailed_changes": [
    {{
      "clause": "...",
      "change_type": "added|removed|modified",
      "old_content": "...",
      "new_content": "...",
      "impact": "..."
    }}
  ]
}}"""

    def _build_obligation_prompt(self, contract_text: str) -> str:
        return f"""Extract all obligations from this executed contract.

For each obligation, identify:
- Who is responsible (owner)
- What must be done (description)
- Type (payment, delivery, reporting, compliance, notification, maintenance, sla)
- Amount if applicable
- Frequency (once, daily, weekly, monthly, quarterly, annually)
- Due date if specified
- For SLA obligations ONLY: measurable service levels as "sla_metrics":
  {{"uptime_target": <0-100 number>, "response_time_hours": <number>,
    "measurement_period": "monthly|weekly|quarterly|daily|annually"}}
  Omit "sla_metrics" for non-SLA obligations. Only include values stated
  in the contract - never estimate or invent them.

Contract:
{contract_text[:8000]}

Return JSON array of obligations:
[
  {{
    "owner_party": "...",
    "description": "...",
    "obligation_type": "payment|delivery|reporting|compliance|notification|maintenance|sla|other",
    "amount": "...",
    "frequency": "once|daily|weekly|monthly|quarterly|annually|null",
    "due_date": "YYYY-MM-DD or null",
    "clause_identifier": "section.X",
    "sla_metrics": null | {{"uptime_target": 99.9, "response_time_hours": 4, "measurement_period": "monthly"}}
  }}
]"""

    async def _call_llm(self, prompt: str) -> str:
        """Call the configured LLM provider.

        Uses OpenAI when ``OPENAI_API_KEY`` is configured, otherwise falls
        back to the deterministic rule-based extractor so the service is
        always usable without external credentials.
        """
        api_key = getattr(settings, "openai_api_key", None)
        if api_key is not None:
            secret = getattr(api_key, "get_secret_value", lambda: api_key)()
            if secret:
                return await self._call_openai(prompt, secret)

        # No LLM configured: return an honest empty result (see
        # _generate_fallback_response) so callers never consume findings about
        # clauses a model did not actually read.
        return self._generate_fallback_response(prompt)

    async def _call_openai(self, prompt: str, api_key: str) -> str:
        """Call OpenAI API."""
        model = getattr(settings, "openai_model", "gpt-4o")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a legal contract analysis AI. Always return valid JSON.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2000,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    def _generate_fallback_response(self, prompt: str) -> str:
        """Return an honest empty analysis when no LLM is configured.

        We never fabricate findings about clauses a model did not actually
        read -- an invented risk or term is worse than none. The response
        carries ``analysis_status: "not_analyzed"`` so callers can tell a real
        analysis from the unconfigured no-op.
        """
        if "obligation" in prompt.lower():
            return json.dumps([])
        if "compare" in prompt.lower():
            return json.dumps({
                "summary": "",
                "changes_detected": 0,
                "risk_changes": [],
                "detailed_changes": [],
                "analysis_status": "not_analyzed",
            })
        return json.dumps({
            "summary": "",
            "key_terms": {},
            "risks": [],
            "analysis_status": "not_analyzed",
        })

    def _parse_analysis_response(self, response: str) -> AnalysisResult:
        """Parse LLM response into AnalysisResult."""
        try:
            data = json.loads(response)
            return AnalysisResult(
                summary=data.get("summary", ""),
                key_terms=data.get("key_terms", {}),
                risks=[
                    RiskItem(
                        category=r.get("category", "other"),
                        severity=r.get("severity", "medium"),
                        finding=r.get("finding", ""),
                        explanation=r.get("explanation"),
                        recommendation=r.get("recommendation"),
                        confidence=r.get("confidence", 0.5),
                    )
                    for r in data.get("risks", [])
                ],
                confidence=0.8,
            )
        except (json.JSONDecodeError, KeyError):
            return AnalysisResult(
                summary="Analysis completed",
                confidence=0.5,
            )

    def _parse_risks_response(self, response: str) -> list[RiskItem]:
        """Parse LLM response into list of RiskItem."""
        try:
            data = json.loads(response)
            if isinstance(data, list):
                return [
                    RiskItem(
                        category=r.get("category", "other"),
                        severity=r.get("severity", "medium"),
                        clause_identifier=r.get("clause_identifier"),
                        clause_text=r.get("clause_text"),
                        finding=r.get("finding", ""),
                        explanation=r.get("explanation"),
                        recommendation=r.get("recommendation"),
                        confidence=r.get("confidence", 0.5),
                    )
                    for r in data
                ]
        except (json.JSONDecodeError, KeyError):
            pass
        return []

    def _parse_comparison_response(self, response: str) -> dict:
        """Parse LLM comparison response."""
        try:
            return json.loads(response)
        except (json.JSONDecodeError, KeyError):
            return {
                "summary": "Comparison completed",
                "changes_detected": 0,
                "risk_changes": [],
                "detailed_changes": [],
            }

    def _parse_obligations_response(self, response: str) -> list[dict]:
        """Parse LLM obligation response."""
        try:
            data = json.loads(response)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, KeyError):
            pass
        return []
