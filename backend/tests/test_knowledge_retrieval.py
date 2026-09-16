"""Tests for knowledge retrieval: chunking, indexing, permission-filtered
retrieval, and citation verification (spec 2.10)."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.knowledge import KnowledgeChunk
from app.services.retrieval_service import (
    AnswerCitation,
    CitationError,
    RetrievalHit,
    RetrievalScope,
    chunk_text,
    cosine_similarity,
    embed_text,
    index_agreement_version,
    retrieve,
    synthesize_answer,
    verify_citations,
)


def test_chunk_text_respects_size():
    text = "word " * 5000
    chunks = chunk_text(text, max_chars=1000, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) <= 1100 for c in chunks)


def test_embedding_and_cosine():
    v1 = embed_text("confidentiality clause term")
    v2 = embed_text("confidentiality clause term")
    v3 = embed_text("governing law new york")
    assert cosine_similarity(v1, v2) > 0.99
    assert cosine_similarity(v1, v3) < 0.5


@pytest.mark.asyncio
async def test_index_agreement_version_creates_chunks(db_session, test_org, test_agreement):
    text = (
        "1. Confidentiality. Each party shall hold the other's Confidential "
        "Information in confidence for a period of two years. "
        "2. Term. This agreement continues for twelve months. "
        "3. Governing law. This agreement is governed by the laws of New York. "
        "4. Indemnification. The receiving party shall indemnify the disclosing "
        "party against any loss arising from unauthorized disclosure. "
        "5. Assignment. Neither party may assign this agreement without written "
        "consent. "
        "6. Entire agreement. This document supersedes all prior discussions. "
        "7. Severability. Any invalid provision shall be severed. "
        "8. Notices. All notices shall be delivered in writing to registered "
        "offices of each party. "
        "9. Force majeure. Neither party is liable for delay caused by events "
        "beyond reasonable control including natural disasters or war. "
        "10. Limitation of liability. Each party's aggregate liability shall not "
        "exceed the fees paid under this agreement during the prior twelve months. "
        "11. Insurance. Each party shall maintain commercial general liability "
        "insurance with limits of at least one million US dollars per occurrence. "
        "12. Audit rights. The customer may audit supplier records annually. "
        "13. Data protection. Both parties shall comply with applicable data "
        "protection regulations and maintain appropriate technical safeguards. "
        "14. Dispute resolution. Disputes shall first be escalated to senior "
        "management representatives before formal arbitration is commenced. "
        "15. Counterparts. This agreement may be executed in counterparts, each "
        "of which shall be deemed an original and together shall constitute one "
        "and the same instrument, binding upon the parties. "
    )
    chunks = await index_agreement_version(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        version_id=uuid.uuid4(),
        content=text,
        agreement_type="nda",
        jurisdiction="US-NY",
        classification="confidential",
    )
    assert len(chunks) >= 2
    assert all(c.embedding for c in chunks)
    assert all(c.is_current for c in chunks)


@pytest.mark.asyncio
async def test_permission_filtered_retrieval_excludes_inaccessible(
    db_session, test_org, test_agreement
):
    # Index two agreements but only grant access to one.
    secret_agreement_id = uuid.uuid4()
    await index_agreement_version(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        version_id=uuid.uuid4(),
        content="Liability cap is USD 1,000,000 under this master agreement.",
        agreement_type="msa",
    )
    await index_agreement_version(
        db_session,
        organization_id=test_org.id,
        agreement_id=secret_agreement_id,
        version_id=uuid.uuid4(),
        content="The company agrees to pay unlimited liability in full.",
        agreement_type="msa",
    )

    hits = await retrieve(
        db_session,
        organization_id=test_org.id,
        question="What is the liability cap?",
        accessible_agreement_ids=[test_agreement.id],
        limit=10,
        min_score=0.0,
    )
    assert hits
    # No chunk from the inaccessible agreement may appear.
    assert all(h.agreement_id == test_agreement.id for h in hits)


@pytest.mark.asyncio
async def test_temporal_scope_filters_superseded_chunks(db_session, test_org, test_agreement):
    version_id = uuid.uuid4()
    await index_agreement_version(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        version_id=version_id,
        content="Renewal term is one year, renewable automatically.",
        agreement_type="msa",
        effective_from=datetime(2024, 1, 1).date(),
        effective_to=datetime(2025, 12, 31).date(),
    )

    hits = await retrieve(
        db_session,
        organization_id=test_org.id,
        question="renewal term",
        accessible_agreement_ids=[test_agreement.id],
        scope=RetrievalScope(as_of=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        limit=5,
        min_score=0.0,
    )
    # Out of effective window -> no hits.
    assert hits == []


def test_citation_verification_accepts_supported_quotes():
    evidence = [
        RetrievalHit(
            chunk_id=uuid.uuid4(),
            agreement_id=uuid.uuid4(),
            version_id=None,
            clause_id=None,
            content="Each party shall keep the information confidential for two years.",
            score=0.9,
            is_current=True,
        )
    ]
    citations = [
        AnswerCitation(
            source_number=1,
            agreement_id=evidence[0].agreement_id,
            version_id=None,
            clause_id=None,
            quote="keep the information confidential for two years",
        )
    ]
    assert len(verify_citations(citations, evidence)) == 1


def test_citation_verification_rejects_fabricated_quote():
    evidence = [
        RetrievalHit(
            chunk_id=uuid.uuid4(),
            agreement_id=uuid.uuid4(),
            version_id=None,
            clause_id=None,
            content="The liability cap is one million dollars.",
            score=0.9,
            is_current=True,
        )
    ]
    citations = [
        AnswerCitation(
            source_number=1,
            agreement_id=evidence[0].agreement_id,
            version_id=None,
            clause_id=None,
            quote="unlimited liability applies",
        )
    ]
    with pytest.raises(CitationError):
        verify_citations(citations, evidence)


@pytest.mark.asyncio
async def test_synthesize_answer_no_hallucination_when_no_evidence():
    answer = synthesize_answer("What is the auto-renewal clause?", [])
    assert answer.status == "INSUFFICIENT_EVIDENCE"
    assert answer.requires_human_review is True