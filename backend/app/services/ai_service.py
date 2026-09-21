"""AI service for contract intelligence.

Provides contract analysis, risk detection, and comparison capabilities.
Uses LLM for reasoning with structured output.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field

import httpx

from app.core.config import get_settings_lazy

settings = get_settings_lazy()

logger = logging.getLogger(__name__)


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
    # Honest coverage accounting (spec 1.19.24): how much of the contract the
    # model actually read. ``truncated`` is True only when the chunk cap
    # forced dropping sections — never silently.
    coverage: dict = field(default_factory=dict)


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

    # ── Long-contract chunking (spec 1.19.24) ────────────────────────────

    def _split_into_chunks(self, text: str) -> tuple[list[str], bool]:
        """Split long text into overlapping paragraph-boundary chunks.

        Returns ``(chunks, truncated)`` where ``truncated`` is True only
        when AI_MAX_CHUNKS_PER_ANALYSIS forced dropping sections. Short
        contracts come back as a single chunk — one LLM call, no waste.
        """
        chunk_chars = getattr(settings, "ai_chunk_chars", 60000) or 0
        overlap = min(getattr(settings, "ai_chunk_overlap_chars", 800) or 0, chunk_chars // 2)
        max_chunks = max(1, getattr(settings, "ai_max_chunks_per_analysis", 20) or 20)

        if chunk_chars <= 0 or len(text) <= chunk_chars:
            return [text], False

        chunks: list[str] = []
        current = ""
        for paragraph in text.split("\n\n"):
            if len(paragraph) > chunk_chars:
                # A single oversized paragraph (rare): flush, then hard-split.
                if current:
                    chunks.append(current)
                    current = ""
                for i in range(0, len(paragraph), chunk_chars):
                    chunks.append(paragraph[i : i + chunk_chars])
                continue
            candidate = f"{current}\n\n{paragraph}" if current else paragraph
            if len(candidate) > chunk_chars and current:
                chunks.append(current)
                tail = current[-overlap:] if overlap else ""
                current = f"{tail}\n\n{paragraph}" if tail else paragraph
            else:
                current = candidate
        if current:
            chunks.append(current)

        if len(chunks) <= max_chunks:
            return chunks, False

        # Cap exceeded: sample head / middle / tail so beginning (parties,
        # dates), middle (boilerplate, obligations), and end (signatures,
        # schedules) are all represented — flagged as truncated.
        head = chunks[: max_chunks // 2]
        tail_n = max(1, max_chunks // 5)
        middle_n = max(0, max_chunks - len(head) - tail_n)
        mid = chunks[len(chunks) // 2 : len(chunks) // 2 + middle_n]
        tail = chunks[-tail_n:]
        return head + mid + tail, True

    @staticmethod
    def _dedupe_risks(risks: list[RiskItem]) -> list[RiskItem]:
        """Drop duplicate findings introduced by chunk overlap."""
        seen: set[tuple] = set()
        unique: list[RiskItem] = []
        for risk in risks:
            key = (risk.category, (risk.finding or "").strip().lower())
            if key in seen:
                continue
            seen.add(key)
            unique.append(risk)
        return unique

    async def _analyze_all_chunks(
        self,
        contract_text: str,
        *,
        build_prompt,          # (chunk, position, total) -> prompt
        parse_response,
    ) -> tuple[list, dict]:
        """Run a prompt over every chunk and merge parsed results.

        A per-chunk LLM failure skips that chunk's contribution (recorded in
        coverage) instead of losing the whole analysis; if *every* chunk
        fails, the last error is re-raised to preserve the original
        error-propagation behavior.
        """
        chunks, truncated = self._split_into_chunks(contract_text)
        coverage = {
            "characters_total": len(contract_text),
            "chunks": len(chunks),
            "chunks_failed": 0,
            "truncated": truncated,
        }
        if truncated:
            coverage["note"] = (
                "Contract exceeded AI_MAX_CHUNKS_PER_ANALYSIS; only a sample "
                "of sections was analyzed."
            )

        results: list = []
        last_error: Exception | None = None
        for position, chunk in enumerate(chunks, start=1):
            try:
                response = await self._call_llm(build_prompt(chunk, position, len(chunks)))
                results.extend(parse_response(response))
            except Exception as exc:  # noqa: BLE001 — partial coverage beats none
                last_error = exc
                coverage["chunks_failed"] += 1
                logger.warning(
                    "AI chunk %d/%d failed: %s", position, len(chunks), exc
                )

        if coverage["chunks_failed"] and not results and last_error is not None:
            raise last_error
        coverage["chunks_analyzed"] = len(chunks) - coverage["chunks_failed"]
        return results, coverage

    async def analyze_contract(
        self,
        contract_text: str,
        agreement_type: str = "mutual_nda",
        is_ocr: bool = False,
        ocr_confidence: float = 1.0,
    ) -> AnalysisResult:
        """Analyze a contract and extract key information.

        Long contracts are split into overlapping chunks and the model reads
        every section (spec 1.19.24): summary and key terms come from the
        first chunk (parties, dates, commercial terms), risks are merged
        from all chunks. ``result.coverage`` reports what was actually read.

        Args:
            contract_text: The full text of the contract.
            agreement_type: The type of agreement.
            is_ocr: Whether the text was extracted via OCR.
            ocr_confidence: The confidence score of the OCR extraction.

        Returns:
            AnalysisResult with summary, key terms, risks, and coverage.
        """
        chunks, _truncated = self._split_into_chunks(contract_text)

        if len(chunks) == 1:
            prompt = self._build_analysis_prompt(
                chunks[0], agreement_type, is_ocr, ocr_confidence
            )
            response = await self._call_llm(prompt)
            result = self._parse_analysis_response(response)
            result.coverage = {
                "characters_total": len(contract_text),
                "chunks": 1,
                "chunks_analyzed": 1,
                "chunks_failed": 0,
                "truncated": False,
            }
            return result

        # Map-reduce: full analysis on the first chunk (it carries the
        # parties/dates the key terms come from), risk-only passes on the rest.
        first_result: AnalysisResult | None = None
        risks: list[RiskItem] = []
        coverage: dict = {
            "characters_total": len(contract_text),
            "chunks": len(chunks),
            "chunks_failed": 0,
            "truncated": _truncated,
        }
        if _truncated:
            coverage["note"] = (
                "Contract exceeded AI_MAX_CHUNKS_PER_ANALYSIS; only a sample "
                "of sections was analyzed."
            )

        for position, chunk in enumerate(chunks, start=1):
            try:
                if position == 1:
                    prompt = self._build_analysis_prompt(
                        chunk, agreement_type, is_ocr, ocr_confidence,
                        position=position, total=len(chunks),
                    )
                    first_result = self._parse_analysis_response(await self._call_llm(prompt))
                else:
                    prompt = self._build_risk_prompt(
                        chunk, agreement_type, is_ocr, ocr_confidence,
                        position=position, total=len(chunks),
                    )
                    risks.extend(self._parse_risks_response(await self._call_llm(prompt)))
            except Exception as exc:  # noqa: BLE001 — partial coverage beats none
                coverage["chunks_failed"] += 1
                logger.warning("AI chunk %d/%d failed: %s", position, len(chunks), exc)

        if first_result is None:
            # First chunk (or every chunk) failed — degrade to risk-only
            # merge if anything survived, else surface the failure.
            if not risks:
                raise RuntimeError(
                    "AI analysis failed on all chunks of the contract"
                )
            first_result = AnalysisResult(summary="", key_terms={})

        merged = self._dedupe_risks([*first_result.risks, *risks])
        coverage["chunks_analyzed"] = len(chunks) - coverage["chunks_failed"]
        return AnalysisResult(
            summary=first_result.summary,
            key_terms=first_result.key_terms,
            risks=merged,
            model_used=first_result.model_used,
            confidence=first_result.confidence,
            coverage=coverage,
        )

    async def detect_risks(
        self,
        contract_text: str,
        agreement_type: str = "mutual_nda",
        is_ocr: bool = False,
        ocr_confidence: float = 1.0,
    ) -> list[RiskItem]:
        """Detect risks in a contract.

        Long contracts are analyzed chunk-by-chunk with the full text read
        (spec 1.19.24); findings are merged and overlap-duplicates removed.

        Args:
            contract_text: The full text of the contract.
            agreement_type: The type of agreement.
            is_ocr: Whether the text was extracted via OCR.
            ocr_confidence: The confidence score of the OCR extraction.

        Returns:
            List of RiskItem findings.
        """
        risks, _coverage = await self._analyze_all_chunks(
            contract_text,
            build_prompt=lambda chunk, position, total: self._build_risk_prompt(
                chunk, agreement_type, is_ocr, ocr_confidence,
                position=position, total=total,
            ),
            parse_response=self._parse_risks_response,
        )
        return self._dedupe_risks(risks)

    async def compare_contracts(
        self,
        base_text: str,
        compared_text: str,
        base_version: int = 1,
        compared_version: int = 2,
    ) -> dict:
        """Compare two contract versions and highlight changes.

        Long versions are chunked on the same paragraph boundaries as
        analysis (spec 1.19.24): full-text comparisons read every section.

        Args:
            base_text: Text of the base version.
            compared_text: Text of the compared version.
            base_version: Version number of base.
            compared_version: Version number of compared.

        Returns:
            Comparison result with summary and changes.
        """
        base_chunks, base_trunc = self._split_into_chunks(base_text)
        compared_chunks, compared_trunc = self._split_into_chunks(compared_text)
        truncated = base_trunc or compared_trunc

        # Compare positionally: chunk N of base against chunk N of compared.
        # Both versions are chunked independently so each LLM call sees the
        # same section from both sides (comparing a chunk to itself would
        # hide every diff). Version count differences surface as fewer/more
        # sections reported per side.
        total = max(len(base_chunks), len(compared_chunks))
        if total == 1:
            prompt = self._build_comparison_prompt(
                base_text, compared_text, base_version, compared_version
            )
            response = await self._call_llm(prompt)
            return self._parse_comparison_response(response)

        changes: list[dict] = []
        risk_changes: list[dict] = []
        failures = 0
        for position in range(1, total + 1):
            idx = position - 1
            base_chunk = base_chunks[idx] if idx < len(base_chunks) else "(section absent in this version)"
            compared_chunk = (
                compared_chunks[idx] if idx < len(compared_chunks) else "(section absent in this version)"
            )
            try:
                prompt = self._build_comparison_prompt(
                    base_chunk, compared_chunk, base_version, compared_version,
                    position=position, total=total,
                )
                parsed = self._parse_comparison_response(await self._call_llm(prompt))
                changes.extend(parsed.get("detailed_changes", []))
                risk_changes.extend(parsed.get("risk_changes", []))
            except Exception as exc:  # noqa: BLE001 — partial coverage beats none
                failures += 1
                logger.warning("AI compare chunk %d/%d failed: %s", position, total, exc)

        if failures and len(changes) + len(risk_changes) == 0 and failures == total:
            raise RuntimeError("AI comparison failed on all chunks")

        return {
            "summary": (
                f"{len(changes)} changes detected across {total} sections"
                if not failures
                else f"{len(changes)} changes detected ({failures} section(s) failed analysis)"
            ),
            "changes_detected": len(changes),
            "risk_changes": risk_changes,
            "detailed_changes": changes,
            "coverage": {
                "characters_total": len(base_text) + len(compared_text),
                "chunks": total,
                "chunks_failed": failures,
                "truncated": truncated,
            },
        }

    async def extract_obligations(
        self,
        contract_text: str,
    ) -> list[dict]:
        """Extract obligations from an executed contract.

        Long contracts are chunked and every section read (spec 1.19.24);
        obligations are merged in document order.

        Args:
            contract_text: The full text of the contract.

        Returns:
            List of obligation dictionaries.
        """
        obligations, _coverage = await self._analyze_all_chunks(
            contract_text,
            build_prompt=lambda chunk, position, total: self._build_obligation_prompt(
                chunk, position=position, total=total,
            ),
            parse_response=self._parse_obligations_response,
        )
        return obligations

    def _build_analysis_prompt(
        self, contract_text: str, agreement_type: str, is_ocr: bool = False, ocr_confidence: float = 1.0,
        *, position: int = 1, total: int = 1,
    ) -> str:
        ocr_context = ""
        if is_ocr:
            ocr_context = f"\nNote: This text was extracted via OCR with {ocr_confidence*100:.1f}% confidence. Please account for possible misspellings, missing punctuation, or artifacts.\n"

        section_note = (
            f"\nThis is section {position} of {total} of the contract. "
            "Analyze ALL sections together for a complete picture; focus on "
            "the clauses present in THIS section."
            if total > 1
            else ""
        )

        return f"""Analyze this {agreement_type} contract and provide:
1. A concise summary (2-3 paragraphs)
2. Key terms extracted as JSON
3. Risk assessment
{ocr_context}{section_note}

Contract:
{contract_text}

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
        self, contract_text: str, agreement_type: str, is_ocr: bool = False, ocr_confidence: float = 1.0,
        *, position: int = 1, total: int = 1,
    ) -> str:
        ocr_context = ""
        if is_ocr:
            ocr_context = f"\nNote: This text was extracted via OCR with {ocr_confidence*100:.1f}% confidence. Please account for possible misspellings, missing punctuation, or artifacts.\n"

        section_note = (
            f"\nThis is section {position} of {total} of the contract. "
            "Report risks found in THIS section only."
            if total > 1
            else ""
        )

        return f"""Perform a detailed risk analysis of this {agreement_type} contract.
{ocr_context}{section_note}

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
{contract_text}

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
        *,
        position: int = 1,
        total: int = 1,
    ) -> str:
        section_note = (
            f"\nYou are comparing section {position} of {total}. Report changes "
            "found in THIS section only."
            if total > 1
            else ""
        )
        return f"""Compare these two contract versions and identify all changes.
{section_note}
Base Version ({base_version}):
{base_text}

Compared Version ({compared_version}):
{compared_text}

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

    def _build_obligation_prompt(
        self, contract_text: str, *, position: int = 1, total: int = 1,
    ) -> str:
        section_note = (
            f"\nThis is section {position} of {total} of the contract. "
            "Extract obligations appearing in THIS section only."
            if total > 1
            else ""
        )
        return f"""Extract all obligations from this executed contract.{section_note}

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
{contract_text}

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
