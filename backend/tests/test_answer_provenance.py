"""Spec §70 — AI must distinguish facts from assumptions."""

from app.domain.answer_provenance import (
    AnswerSource,
    classify_user_update,
    review_findings,
    tag_answers,
)
from app.services.contract_quality import run_quality_checks

QUESTIONS = [
    {"id": "party_a_legal_name", "label": "Party A", "required": True},
    {"id": "governing_law", "label": "Governing Law", "required": True, "default": "Sri Lanka"},
    {"id": "warranty_months", "label": "Warranty period", "required": True},
    {"id": "notes", "label": "Notes", "required": False},
]


class TestTagging:
    def test_tag_answers_marks_every_key(self):
        prov = tag_answers({}, ["a", "b"], AnswerSource.EXTRACTED)
        assert prov == {"a": "EXTRACTED", "b": "EXTRACTED"}

    def test_weaker_source_never_overwrites_user_fact(self):
        prov = tag_answers({"a": "USER_PROVIDED"}, ["a"], AnswerSource.SYSTEM_DEFAULT)
        assert prov["a"] == "USER_PROVIDED"

    def test_force_overrides(self):
        prov = tag_answers({"a": "USER_PROVIDED"}, ["a"], AnswerSource.REVIEW_REQUIRED, force=True)
        assert prov["a"] == "REVIEW_REQUIRED"


class TestClassifyUserUpdate:
    def test_value_equal_to_default_is_system_default(self):
        prov = classify_user_update({}, {"governing_law": "Sri Lanka"}, QUESTIONS)
        assert prov["governing_law"] == "SYSTEM_DEFAULT"

    def test_changed_value_is_user_provided(self):
        prov = classify_user_update({}, {"governing_law": "Singapore"}, QUESTIONS)
        assert prov["governing_law"] == "USER_PROVIDED"

    def test_confirmed_default_stays_user_provided(self):
        prov = classify_user_update(
            {"governing_law": "USER_PROVIDED"}, {"governing_law": "Sri Lanka"}, QUESTIONS
        )
        assert prov["governing_law"] == "USER_PROVIDED"

    def test_blank_required_answer_is_review_required(self):
        prov = classify_user_update({}, {"warranty_months": ""}, QUESTIONS)
        assert prov["warranty_months"] == "REVIEW_REQUIRED"

    def test_blank_optional_answer_drops_tag(self):
        prov = classify_user_update({"notes": "USER_PROVIDED"}, {"notes": ""}, QUESTIONS)
        assert "notes" not in prov


class TestReviewFindings:
    def test_missing_required_is_blocker_not_invented(self):
        findings = review_findings({"party_a_legal_name": "ABC"}, QUESTIONS, {})
        codes = {(f["field"], f["severity"]) for f in findings}
        assert ("warranty_months", "high") in codes
        assert ("governing_law", "high") in codes
        assert all(f["source"] == "REVIEW_REQUIRED" for f in findings)

    def test_assumed_default_is_advisory(self):
        findings = review_findings(
            {"party_a_legal_name": "ABC", "governing_law": "Sri Lanka", "warranty_months": 12},
            QUESTIONS,
            {"governing_law": "SYSTEM_DEFAULT", "warranty_months": "INFERRED"},
        )
        assert {f["severity"] for f in findings} == {"medium"}
        assert {f["field"] for f in findings} == {"governing_law", "warranty_months"}

    def test_confirmed_facts_produce_no_findings(self):
        findings = review_findings(
            {"party_a_legal_name": "ABC", "governing_law": "Singapore", "warranty_months": 12},
            QUESTIONS,
            {"party_a_legal_name": "USER_PROVIDED", "governing_law": "USER_PROVIDED", "warranty_months": "EXTRACTED"},
        )
        assert findings == []


class TestQualityGateIntegration:
    def test_quality_report_blocks_on_missing_required_answer(self):
        report = run_quality_checks(
            None,
            agreement_meta={"answers": {}, "questions": QUESTIONS, "provenance": {}},
        )
        assert report["has_blockers"] is True
        assert any(f["engine"] == "provenance" and f["severity"] == "high" for f in report["findings"])

    def test_quality_report_clean_when_all_confirmed(self):
        answers = {"party_a_legal_name": "ABC", "governing_law": "Singapore", "warranty_months": 6}
        prov = tag_answers({}, answers.keys(), AnswerSource.USER_PROVIDED)
        report = run_quality_checks(
            None, agreement_meta={"answers": answers, "questions": QUESTIONS, "provenance": prov}
        )
        assert not [f for f in report["findings"] if f["engine"] == "provenance"]
