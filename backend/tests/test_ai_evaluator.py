"""AI evaluation harness tests (spec 98-99, 2.10.40-42)."""

import pytest

from app.services.ai_evaluator import (
    AIEvaluator,
    EvalCase,
    run_baseline_contract_suite,
    verify_citation_quote,
)


class TestCitationVerification:
    def test_accurate_quote_passes(self):
        source = (
            "The Customer shall pay the Service Provider a monthly fee of "
            "USD 5,000 within 30 days of invoice."
        )
        assert verify_citation_quote("pay the Service Provider", source)
        assert verify_citation_quote("USD 5,000", source)

    def test_fabricated_quote_fails(self):
        source = "Payment is due within 30 days."
        assert not verify_citation_quote("due within 24 hours", source)
        assert not verify_citation_quote("", source)

    def test_whitespace_normalisation(self):
        source = "The Supplier shall meet the SLA\n  at all times."
        assert verify_citation_quote("shall meet the SLA at all times", source)


class TestAnswerEvaluation:
    def test_good_answer_passes(self):
        evaluator = AIEvaluator()
        case = EvalCase(
            question="What is the payment term?",
            expect_status="ANSWERED",
            min_citations=1,
            expected_fragments=["30 days"],
        )
        answer = {
            "status": "ANSWERED",
            "answer": "Payment is due within 30 days of invoice.",
            "citations": [
                {"agreement_id": "a-1", "quote": "due within 30 days of invoice"}
            ],
        }
        result = evaluator.evaluate_answer(case, answer)
        assert result.passed

    def test_uncited_answer_fails(self):
        evaluator = AIEvaluator()
        case = EvalCase(question="What is the payment term?", expect_status="ANSWERED")
        answer = {"status": "ANSWERED", "answer": "30 days.", "citations": []}
        result = evaluator.evaluate_answer(case, answer)
        assert not result.passed
        assert result.checks["has_citations"] is False

    def test_stub_citation_fails(self):
        evaluator = AIEvaluator()
        case = EvalCase(question="q", expect_status="ANSWERED")
        answer = {"status": "ANSWERED", "answer": "x", "citations": [{"quote": "..."}]}
        result = evaluator.evaluate_answer(case, answer)
        assert result.checks["citations_nonempty"] is False

    def test_uncertainty_requires_review_flag(self):
        evaluator = AIEvaluator()
        case = EvalCase(question="q", expect_status="ANSWERED")
        answer = {
            "status": "ANSWERED",
            "answer": "Probably 30 days",
            "citations": [{"quote": "due within 30 days"}],
            "uncertainty": "conflicting clauses",
            "requires_human_review": False,  # should be True
        }
        result = evaluator.evaluate_answer(case, answer)
        assert result.checks["uncertainty_disclosed"] is False

    def test_refusal_must_not_fabricate(self):
        evaluator = AIEvaluator()
        case = EvalCase(question="q", expect_status="INSUFFICIENT_EVIDENCE")
        fabricated = {
            "status": "INSUFFICIENT_EVIDENCE",
            "answer": "Cannot answer.",
            "citations": [{"quote": "invented evidence"}],
        }
        result = evaluator.evaluate_answer(case, fabricated)
        assert not result.passed

        honest = {
            "status": "INSUFFICIENT_EVIDENCE",
            "answer": "No indexed contract contains this information.",
            "citations": [],
        }
        assert evaluator.evaluate_answer(case, honest).passed


class TestSuiteRunner:
    @pytest.mark.asyncio
    async def test_run_suite_aggregates(self):
        evaluator = AIEvaluator()

        async def good_answer_fn(question: str) -> dict:
            return {
                "status": "ANSWERED",
                "answer": "The term is 24 months.",
                "citations": [{"agreement_id": "a-1", "quote": "continues for 24 months"}],
            }

        cases = [
            EvalCase(question="What is the term?", expect_status="ANSWERED", min_citations=1),
            EvalCase(question="What is the notice period?", expect_status="ANSWERED", min_citations=1),
        ]
        report = await evaluator.run_suite(cases, good_answer_fn)
        assert report["summary"]["total"] == 2
        assert report["summary"]["failed"] == 0
        assert report["summary"]["citation_precision"] == 1.0

    @pytest.mark.asyncio
    async def test_crashing_answer_fn_is_recorded_not_raised(self):
        evaluator = AIEvaluator()

        async def bad_fn(question: str) -> dict:
            raise RuntimeError("LLM exploded")

        report = await evaluator.run_suite(
            [EvalCase(question="q")], bad_fn
        )
        assert report["summary"]["failed"] == 1
        assert report["results"][0]["checks"]["no_crash"] is False

    @pytest.mark.asyncio
    async def test_baseline_suite_rejects_blank_questions(self):
        """The answer contract must return ERROR (not crash/fabricate) for
        hostile inputs - the CI smoke gate."""

        async def contract_answer_fn(question: str) -> dict:
            if not question or not question.strip():
                raise ValueError("empty")
            return {"status": "ERROR", "answer": "", "citations": []}

        report = await run_baseline_contract_suite(contract_answer_fn)
        assert report["summary"]["failed"] == 0
