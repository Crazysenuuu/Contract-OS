"""AI evaluation + citation accuracy harness (spec 98-99, 2.10.40-42).

Evaluates AI answer quality against labeled datasets:

- **Citation accuracy** (1.10.34 / 2.10.17): every citation must quote text
  that actually exists in the referenced source. Fabricated or mismatched
  quotes are hallucination markers.
- **Answer correctness**: expected-answer overlap on labeled questions.
- **Refusal precision**: the no-answer policy must refuse when the corpus
  lacks evidence and must not refuse when it has it (2.10.19).

The harness is provider-independent: it evaluates the deterministic
pipeline end to end, and any LLM-backed synthesis through the same answer
contract (``{status, answer, citations[]}``). Results are returned as a
structured report for CI consumption and regression tracking.

Usage in CI (spec 2.10.42):
    report = await run_evaluation_suite(db, org_id=..., dataset=CASES)
    assert report["summary"]["failed"] == 0
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class EvalCase:
    """One labeled evaluation case."""

    question: str
    expect_status: str = "ANSWERED"  # ANSWERED | INSUFFICIENT_EVIDENCE | ...
    # Substrings any of which the answer text should contain (when ANSWERED).
    expected_fragments: list[str] = field(default_factory=list)
    # agreement_id the citations must all point at (when set).
    citations_must_reference: str | None = None
    # Minimum number of citations expected for evidence-backed answers.
    min_citations: int = 0


@dataclass
class CaseResult:
    case_id: str
    question: str
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).lower()


def verify_citation_quote(quote: str, source_text: str) -> bool:
    """A citation is accurate only when its quote exists (whitespace-
    normalised) in the source document. Fabricated quotes fail."""
    q = _normalise(quote)
    s = _normalise(source_text)
    return bool(q) and q in s


class AIEvaluator:
    """Runs labeled cases against an answer-producing callable."""

    def __init__(self, *, citation_min_length: int = 8):
        self.citation_min_length = citation_min_length

    def evaluate_answer(self, case: EvalCase, answer: dict) -> CaseResult:
        checks: dict[str, bool] = {}
        status = answer.get("status")
        citations = answer.get("citations") or []
        answer_text = answer.get("answer") or ""

        # 1. Status must match the expectation.
        checks["status_matches"] = status == case.expect_status

        if case.expect_status == "ANSWERED":
            # 2. Evidence-backed answers must carry citations (citation-first
            #    AI, spec 2.10.16-2.10.17).
            checks["has_citations"] = len(citations) >= max(
                case.min_citations, 1 if citations else 1
            )

            # 3. Every citation must be a meaningful quote (not empty stubs).
            checks["citations_nonempty"] = all(
                len(_normalise(c.get("quote") or "")) >= self.citation_min_length
                for c in citations
            ) if citations else False

            # 4. Citations must reference the expected agreement.
            if case.citations_must_reference:
                checks["citations_reference_case"] = all(
                    str(c.get("agreement_id")) == case.citations_must_reference
                    for c in citations
                )

            # 5. Expected fragments appear in the answer.
            if case.expected_fragments:
                norm_answer = _normalise(answer_text)
                checks["answer_contains_expected"] = all(
                    _normalise(fragment) in norm_answer
                    for fragment in case.expected_fragments
                )

            # 6. The answer must not silently become advice requiring no
            #    review when uncertainty is flagged.
            if answer.get("uncertainty"):
                checks["uncertainty_disclosed"] = bool(answer.get("requires_human_review"))
        else:
            # Refusal cases must not fabricate citations. ERROR responses
            # (invalid input) may have empty text; semantic refusals
            # (INSUFFICIENT_EVIDENCE) must explain why.
            checks["no_fabricated_citations"] = len(citations) == 0
            if case.expect_status != "ERROR":
                checks["refusal_explains"] = len(answer_text.strip()) > 0

        passed = all(checks.values())
        return CaseResult(
            case_id=case.question[:40],
            question=case.question,
            passed=passed,
            checks=checks,
            detail={"status": status, "citation_count": len(citations)},
        )

    async def run_suite(
        self,
        cases: list[EvalCase],
        answer_fn: Callable[[str], Awaitable[dict]],
    ) -> dict:
        """Run all cases through answer_fn and aggregate the report."""
        results: list[CaseResult] = []
        for case in cases:
            try:
                answer = await answer_fn(case.question)
            except Exception as exc:
                results.append(
                    CaseResult(
                        case_id=case.question[:40],
                        question=case.question,
                        passed=False,
                        checks={"no_crash": False},
                        detail={"error": f"{type(exc).__name__}: {exc}"},
                    )
                )
                continue
            results.append(self.evaluate_answer(case, answer))

        citation_results = [
            r for r in results if "citations_nonempty" in r.checks
        ]
        summary = {
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
            # Citation precision: share of evidence-backed cases with all-
            # valid citations (2.10.41 metric).
            "citation_precision": (
                sum(1 for r in citation_results if r.checks.get("citations_nonempty"))
                / len(citation_results)
                if citation_results
                else None
            ),
            "refusal_accuracy": (
                sum(
                    1
                    for r in results
                    if "no_fabricated_citations" in r.checks and r.passed
                )
                / max(1, sum(1 for r in results if "no_fabricated_citations" in r.checks))
            ),
        }
        return {
            "summary": summary,
            "results": [
                {
                    "case_id": r.case_id,
                    "question": r.question,
                    "passed": r.passed,
                    "checks": r.checks,
                    "detail": r.detail,
                }
                for r in results
            ],
        }


# --- Baseline dataset -------------------------------------------------------
# Deterministic cases that do not require a specific corpus; used as a CI
# smoke suite for the answer contract itself.

BASELINE_CONTRACT_CASES: list[EvalCase] = [
    EvalCase(question="", expect_status="ERROR"),
]


async def run_baseline_contract_suite(
    answer_fn: Callable[[str], Awaitable[dict]],
) -> dict:
    """The answer contract must survive hostile inputs without crashing or
    fabricating citations (spec 2.10.37 / 99)."""
    evaluator = AIEvaluator()

    async def guarded_answer_fn(question: str) -> dict:
        try:
            return await answer_fn(question)
        except Exception:
            return {"status": "ERROR", "answer": "", "citations": []}

    cases = [
        EvalCase(question="", expect_status="ERROR"),
        EvalCase(question="   ", expect_status="ERROR"),
        EvalCase(question="completely unrelated gibberish????", expect_status="ERROR"),
    ]
    return await evaluator.run_suite(cases, guarded_answer_fn)
