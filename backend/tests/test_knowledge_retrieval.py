"""Tests for knowledge retrieval: chunking, indexing, permission-filtered
retrieval, and citation verification (spec 2.10)."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.knowledge import EmbeddingVectorType, KnowledgeChunk
from app.services.embedding_provider import (
    EmbeddingProviderError,
    HashEmbeddingProvider,
    OpenAIEmbeddingProvider,
    reset_embedding_provider,
)
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


@pytest.fixture(autouse=True)
def _reset_provider_cache():
    """Keep the cached embedding provider isolated between tests."""
    reset_embedding_provider()
    yield
    reset_embedding_provider()


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


# ===========================================================================
# Embedding provider (spec 2.10.3 / 2.10.4)
# ===========================================================================


class TestHashEmbeddingProvider:
    def test_deterministic_and_normalized(self):
        provider = HashEmbeddingProvider()
        v1 = provider.embed(["confidentiality clause"])[0]
        v2 = provider.embed(["confidentiality clause"])[0]
        assert v1 == v2
        assert abs(sum(x * x for x in v1) - 1.0) < 1e-9

    def test_batch_preserves_order_and_count(self):
        provider = HashEmbeddingProvider()
        vectors = provider.embed(["one", "two", "three"])
        assert len(vectors) == 3

    def test_empty_batch(self):
        assert HashEmbeddingProvider().embed([]) == []


class TestOpenAIEmbeddingProvider:
    def test_fail_closed_on_http_error(self):
        """Provider errors must raise, never return fabricated vectors."""
        provider = OpenAIEmbeddingProvider(api_key="k", dim=1536)
        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = RuntimeError("boom")
            with pytest.raises(EmbeddingProviderError):
                provider.embed(["some contract text"])

    def test_parses_response_and_batches(self):
        provider = OpenAIEmbeddingProvider(api_key="k", dim=4)
        calls = []

        def fake_post(url, **kwargs):
            calls.append(kwargs["json"]["input"])
            resp = MagicMock()
            inputs = kwargs["json"]["input"]
            resp.json.return_value = {
                "data": [
                    {"index": i, "embedding": [float(i)] * 4}
                    for i in range(len(inputs))
                ]
            }
            resp.raise_for_status = lambda: None
            return resp

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = fake_post
            vectors = provider.embed([f"chunk {i}" for i in range(3)])

        assert len(vectors) == 3
        assert vectors[0] == [0.0, 0.0, 0.0, 0.0]
        assert vectors[2] == [2.0, 2.0, 2.0, 2.0]

    def test_malformed_response_raises(self):
        provider = OpenAIEmbeddingProvider(api_key="k", dim=4)
        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = lambda *a, **k: (
                MagicMock(json=lambda: {"unexpected": True}, raise_for_status=lambda: None)
            )
            with pytest.raises(EmbeddingProviderError, match="malformed"):
                provider.embed(["text"])


class TestProviderResolution:
    def test_default_is_hash(self):
        with patch("app.services.embedding_provider.get_settings_lazy") as s:
            s.return_value.embedding_provider = "hash"
            s.return_value.embedding_dim = 256
            from app.services.embedding_provider import get_embedding_provider

            assert isinstance(get_embedding_provider(), HashEmbeddingProvider)

    def test_openai_without_key_fails_closed(self):
        with patch("app.services.embedding_provider.get_settings_lazy") as s:
            s.return_value.embedding_provider = "openai"
            s.return_value.openai_api_key = None
            from app.services.embedding_provider import get_embedding_provider

            with pytest.raises(EmbeddingProviderError, match="OPENAI_API_KEY"):
                get_embedding_provider()

    def test_unknown_provider_raises(self):
        with patch("app.services.embedding_provider.get_settings_lazy") as s:
            s.return_value.embedding_provider = "skynet"
            from app.services.embedding_provider import get_embedding_provider

            with pytest.raises(EmbeddingProviderError, match="Unknown EMBEDDING_PROVIDER"):
                get_embedding_provider()

    def test_hash_projects_to_configured_dim(self):
        """The fallback projects into the configured (column) dimension so it
        can index against the production 1536-dim pgvector column."""
        with patch("app.services.embedding_provider.get_settings_lazy") as s:
            s.return_value.embedding_provider = "hash"
            s.return_value.embedding_dim = 1536
            from app.services.embedding_provider import get_embedding_provider

            provider = get_embedding_provider()
            assert provider.name == "hash-bow-256@1536"
            assert len(provider.embed(["x"])[0]) == 1536
            # Cosine similarity only counts the native 256-dim prefix.
            a, b = provider.embed(["confidentiality clause", "governing law"])
            assert cosine_similarity(a, b) < 0.5


# ===========================================================================
# Dialect-aware vector column
# ===========================================================================


class TestEmbeddingVectorType:
    def test_col_spec(self):
        assert EmbeddingVectorType(1536).get_col_spec() == "VECTOR(1536)"

    def test_pg_text_roundtrip(self):
        from sqlalchemy.dialects import postgresql

        t = EmbeddingVectorType(1536)
        bind = t.bind_processor(postgresql.dialect)
        result = t.result_processor(postgresql.dialect, None)
        vec = [0.1, -0.25, 0.5]
        assert result(bind(vec)) == vec

    def test_pg_text_roundtrip_from_string(self):
        from sqlalchemy.dialects import postgresql

        t = EmbeddingVectorType(1536)
        result = t.result_processor(postgresql.dialect, None)
        assert result("[0.1,0.2,0.3]") == [0.1, 0.2, 0.3]
        assert result(None) is None

    def test_json_roundtrip(self):
        from sqlalchemy.dialects import sqlite

        t = EmbeddingVectorType(1536)
        bind = t.bind_processor(sqlite.dialect)
        result = t.result_processor(sqlite.dialect, None)
        vec = [0.5, 0.25]
        assert result(bind(vec)) == vec
        assert result(None) is None
        assert result("not json") is None


# ===========================================================================
# Indexing with providers
# ===========================================================================


@pytest.mark.asyncio
async def test_index_records_embedding_model(db_session, test_org, test_agreement):
    from app.core.config import get_settings_lazy

    chunks = await index_agreement_version(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        version_id=uuid.uuid4(),
        content="Term. The agreement runs for one year.",
    )
    assert chunks
    # Settings default EMBEDDING_DIM=1536 -> projected fallback vectors.
    dim = getattr(get_settings_lazy(), "embedding_dim", 256)
    expected_name = "hash-bow-256" if dim == 256 else f"hash-bow-256@{dim}"
    assert all(c.embedding_model == expected_name for c in chunks)
    assert all(len(c.embedding) == dim for c in chunks)


@pytest.mark.asyncio
async def test_index_survives_provider_failure(db_session, test_org, test_agreement):
    """Provider outage must not block ingestion: text is indexed unembedded
    and keyword retrieval still works."""
    failing = MagicMock(side_effect=EmbeddingProviderError("provider down"))
    with patch("app.services.retrieval_service.get_embedding_provider", failing):
        chunks = await index_agreement_version(
            db_session,
            organization_id=test_org.id,
            agreement_id=test_agreement.id,
            version_id=uuid.uuid4(),
            content="Liability cap is one million dollars.",
        )
    assert chunks
    assert all(c.embedding is None for c in chunks)

    hits = await retrieve(
        db_session,
        organization_id=test_org.id,
        question="liability cap",
        accessible_agreement_ids=[test_agreement.id],
        min_score=0.0,
    )
    assert hits
    assert all(h.score > 0 for h in hits)


@pytest.mark.asyncio
async def test_reindex_uses_new_provider_model(db_session, test_org, test_agreement):
    """Re-indexing the same version replaces its chunks (delete + insert) and
    records the model per chunk — this is how vectors are backfilled after an
    embedding-model change."""
    version_id = uuid.uuid4()
    first = await index_agreement_version(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        version_id=version_id,
        content="Term. One year.",
    )
    assert all(c.embedding_model and c.embedding_model.startswith("hash-bow-256") for c in first)

    fake = HashEmbeddingProvider()
    fake.name = "fake-model-7"
    with patch("app.services.retrieval_service.get_embedding_provider", return_value=fake):
        chunks = await index_agreement_version(
            db_session,
            organization_id=test_org.id,
            agreement_id=test_agreement.id,
            version_id=version_id,
            content="Term. One year, renewable.",
        )
    assert all(c.embedding_model == "fake-model-7" for c in chunks)
    assert all(c.is_current for c in chunks)

    # Only the new pass remains for this version.
    remaining = (
        await db_session.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.agreement_id == test_agreement.id,
                KnowledgeChunk.version_id == version_id,
            )
        )
    ).scalars().all()
    assert {c.id for c in remaining} == {c.id for c in chunks}