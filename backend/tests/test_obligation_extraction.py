"""Tests for the obligation extraction engine (spec 1.16.21-23).

Covers:
- deterministic extraction creates traceable CANDIDATE obligations
- relative deadlines are NOT resolved into dates (no invented data)
- untraceable AI output is dropped (anti-hallucination)
- extraction runs are recorded with provenance
"""

import pytest
import pytest_asyncio

from app.services.obligation_extractor import (
    extract_candidates_deterministic,
    ObligationExtractor,
)


CONTRACT_TEXT = """
SERVICE AGREEMENT

1. Definitions
Nothing in this agreement obligates either party to disclose source code.

2. Payment Terms
The Customer shall pay the Service Provider a monthly fee of USD 5,000.
Invoices are payable within 30 days of receipt.

3. Reporting
The Service Provider shall provide a monthly service report.

4. Security
The Service Provider shall maintain security controls appropriate to the
Services. The Supplier shall notify the Customer of any incident within
24 hours. The Service Provider shall comply with all applicable laws.

5. Termination
This agreement terminates on 31 December 2027 unless renewed.
"""


import re


def _normalised(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_deterministic_extraction_finds_payment_obligation():
    cands = extract_candidates_deterministic(CONTRACT_TEXT)
    types = {c["obligation_type"] for c in cands}
    assert "payment" in types
    payment = next(c for c in cands if c["obligation_type"] == "payment")
    assert payment["amount"] is not None
    assert "pay" in payment["description"].lower()
    # Source traceability: exact sentence and section (whitespace-normalised
    # on both sides because contract text wraps lines mid-sentence).
    assert payment["source_text"] in _normalised(CONTRACT_TEXT)
    assert payment["source_span"][0] >= 0
    assert payment["clause_identifier"] is None or payment["clause_identifier"].startswith("section.")


def test_deterministic_extraction_finds_notification_and_reporting():
    cands = extract_candidates_deterministic(CONTRACT_TEXT)
    types = {c["obligation_type"] for c in cands}
    assert "notification" in types  # 'notify ... within 24 hours'
    assert "reporting" in types  # 'provide a monthly service report'


def test_negative_sentences_are_excluded():
    cands = extract_candidates_deterministic(CONTRACT_TEXT)
    for c in cands:
        assert "source code" not in c["description"]


def test_relative_deadlines_are_not_invented():
    """'within 30 days' must not become a concrete date (spec 1.16.7)."""
    cands = extract_candidates_deterministic(CONTRACT_TEXT)
    for c in cands:
        # Only the explicit absolute date in clause 5 may be resolved.
        if c["obligation_type"] != "compliance" and "31 December 2027" not in c["description"]:
            assert c["due_date"] is None or "31 December 2027" in c["description"]


def test_absolute_date_is_parsed():
    text = "The Supplier shall maintain insurance as of 31 December 2027."
    cands = extract_candidates_deterministic(text)
    assert cands, "insurance sentence should match"
    assert any(c["due_date"] is not None for c in cands)


def test_every_candidate_has_source_text():
    cands = extract_candidates_deterministic(CONTRACT_TEXT)
    assert cands
    norm = _normalised(CONTRACT_TEXT)
    for c in cands:
        assert c["source_text"]
        assert c["source_text"] in norm


# --- Service-level tests (need DB fixtures) ---------------------------


@pytest_asyncio.fixture
async def seeded_agreement(db_session, test_user, test_org, test_agreement_type):
    """An agreement with one executed-style version full of obligations."""
    from app.models.agreement import Agreement, AgreementVersion

    agreement = Agreement(
        organization_id=test_org.id,
        agreement_type_id=test_agreement_type.id,
        title="Extraction Fixture MSA",
        status="executed",
        created_by=test_user.id,
        data={},
    )
    db_session.add(agreement)
    await db_session.flush()

    version = AgreementVersion(
        agreement_id=agreement.id,
        version_number=1,
        content=CONTRACT_TEXT,
        content_hash="fixture-hash",
        status="executed",
        created_by=test_user.id,
    )
    db_session.add(version)
    await db_session.commit()
    await db_session.refresh(agreement)
    await db_session.refresh(version)
    return agreement.id, version


@pytest.mark.asyncio
async def test_extractor_creates_candidates_and_run(db_session, seeded_agreement):
    """Full run: candidates created in CANDIDATE status + provenance run."""
    agreement_id, version = seeded_agreement
    extractor = ObligationExtractor(db_session)
    result = await extractor.extract(agreement_id)

    assert result["candidate_count"] >= 1
    assert result["run_id"] is not None
    for cand in result["candidates"]:
        assert cand["status"] == "CANDIDATE"


@pytest.mark.asyncio
async def test_extractor_drops_untraceable_ai_output(db_session, seeded_agreement, monkeypatch):
    """AI output that does not trace to document text must be dropped."""
    from app.services.ai_service import AIService

    async def fake_extract(self, text):
        return [
            {
                "owner_party": "Customer",
                "description": "Customer must maintain cyber insurance of $10m",  # not in doc
                "obligation_type": "insurance",
            },
            {
                "owner_party": "Customer",
                "description": "The Customer shall pay the Service Provider a monthly fee of USD 5,000",
                "obligation_type": "payment",
            },
        ]

    monkeypatch.setattr(AIService, "extract_obligations", fake_extract)

    agreement_id, _ = seeded_agreement
    extractor = ObligationExtractor(db_session)
    result = await extractor.extract(agreement_id, method="ai")

    descriptions = [c["description"] for c in result["candidates"]]
    assert all("cyber insurance" not in d for d in descriptions)
    assert any("shall pay" in d for d in descriptions)
