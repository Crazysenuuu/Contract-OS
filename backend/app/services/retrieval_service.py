"""Contract intelligence retrieval (spec 2.10).

- Legal-aware chunking of agreement text.
- Embeddings come from the configured provider (``app/services/embedding_provider.py``):
  OpenAI ``text-embedding-3-small`` (1536-dim) in production, deterministic
  hashing fallback in dev/test. The model is recorded per chunk
  (``embedding_model``) so model changes are detectable and re-indexable.
- Storage: on PostgreSQL the embedding column is native pgvector
  ``vector(1536)`` and retrieval orders by SQL ``<=>`` cosine distance
  (HNSW index); on other dialects (SQLite test suite) vectors are stored as
  JSON and similarity is computed in Python. Both paths apply the same
  permission / temporal filters and the same hybrid scoring, so callers see
  one interface.
- Permission-filtered retrieval: every chunk is filtered by organization
  and agreement access before it can be returned, and temporal scope
  (as-of date / version) is honored.
- Citation verification: every AI answer's citations are checked against the
  actual retrieved evidence; unsupported citations are rejected.
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.knowledge import EMBEDDING_DIM, KnowledgeChunk
from app.services.embedding_provider import (
    EmbeddingProviderError,
    get_embedding_provider,
)

# Deprecated: legacy hashing embedding width. Kept only so older imports
# do not break; the active dimension is EMBEDDING_DIM (models/knowledge.py).
EMBEDDING_DIM_LEGACY = 256


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, *, max_chars: int = 1500, overlap: int = 100) -> list[str]:
    """Legal-aware chunking: split on clause/section boundaries when
    possible, otherwise on paragraphs/sentences, keeping overlap so context
    is not lost across chunk edges.
    """
    text = (text or "").strip()
    if not text:
        return []

    # Split on clause headings first (e.g. "1. Definitions", "Article 4").
    boundaries = re.split(
        r"(?=\n\s*(?:\d+(?:\.\d+)*\.?|Article\s+\d+|Section\s+\d+|Clause\s+\d+|"
        r"[A-Z][A-Z\s]{2,})\b)",
        text,
    )
    chunks: list[str] = []
    for part in boundaries:
        part = part.strip()
        if not part:
            continue
        while len(part) > max_chars:
            cut = part.rfind(" ", 0, max_chars)
            if cut < max_chars // 2:
                cut = max_chars
            chunks.append(part[:cut].strip())
            part = part[cut - overlap:].strip()
        if part:
            chunks.append(part)
    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# Embeddings (provider-backed; deterministic helpers kept for tests)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]{2,}", text.lower())


def embed_text(text: str) -> list[float]:
    """Embed a single text via the configured provider."""
    return get_embedding_provider().embed([text])[0]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _hybrid_score(cosine_sim: float, keyword_overlap: float) -> float:
    """Blend semantic similarity with a keyword-overlap boost (2.10.13)."""
    return cosine_sim * 0.8 + keyword_overlap * 0.2


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

async def index_agreement_version(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement_id: uuid.UUID,
    version_id: uuid.UUID,
    content: str,
    agreement_type: str | None = None,
    clause_id: uuid.UUID | None = None,
    party_ids: list[uuid.UUID] | None = None,
    jurisdiction: str | None = None,
    classification: str = "internal",
    embedding_model: str | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
) -> list[KnowledgeChunk]:
    """Replace the index for a version with fresh chunks."""
    chunks = chunk_text(content)

    # Delete the previous index pass for this exact version. Chunks are
    # derived data (the version text itself is immutable); keeping the old
    # rows would violate uq_knowledge_chunk_agreement_version_index on
    # re-insert and block re-indexing (e.g. after an embedding-model change).
    if not chunks:
        old_result = await db.execute(
            select(KnowledgeChunk.id).where(
                KnowledgeChunk.agreement_id == agreement_id,
                KnowledgeChunk.version_id == version_id,
            )
        )
        if old_result.first() is not None:
            await db.execute(
                KnowledgeChunk.__table__.delete().where(
                    KnowledgeChunk.agreement_id == agreement_id,
                    KnowledgeChunk.version_id == version_id,
                )
            )
            await db.flush()
        return []

    from sqlalchemy import delete as _delete

    await db.execute(
        _delete(KnowledgeChunk).where(
            KnowledgeChunk.agreement_id == agreement_id,
            KnowledgeChunk.version_id == version_id,
        )
    )

    # One batched provider call per version keeps indexing cheap. Provider
    # resolution and embedding are both allowed to fail without blocking
    # ingestion: text is indexed unembedded and keyword retrieval still
    # works; vectors are backfilled by re-indexing.
    vectors: list | None = None
    try:
        provider = get_embedding_provider()
        vectors = provider.embed(chunks)
        if embedding_model is None:
            embedding_model = provider.name
    except EmbeddingProviderError:
        vectors = None
    if embedding_model is None:
        embedding_model = "unknown"

    created: list[KnowledgeChunk] = []
    for i, chunk in enumerate(chunks):
        chunk_hash = hashlib.sha256(chunk.encode()).hexdigest()
        row = KnowledgeChunk(
            organization_id=organization_id,
            agreement_id=agreement_id,
            version_id=version_id,
            clause_id=clause_id,
            agreement_type=agreement_type,
            chunk_index=i,
            content=chunk,
            content_hash=chunk_hash,
            embedding=(vectors[i] if vectors and i < len(vectors) else None),
            embedding_model=embedding_model,
            party_ids=[str(p) for p in (party_ids or [])],
            effective_from=effective_from,
            effective_to=effective_to,
            jurisdiction=jurisdiction,
            classification=classification,
            is_current=True,
        )
        db.add(row)
        created.append(row)
    await db.flush()
    return created


# ---------------------------------------------------------------------------
# Permission-filtered retrieval
# ---------------------------------------------------------------------------

@dataclass
class RetrievalScope:
    """Temporal scope for retrieval (spec 2.10.57)."""

    as_of: datetime | None = None
    version_id: uuid.UUID | None = None


@dataclass
class RetrievalHit:
    chunk_id: uuid.UUID
    agreement_id: uuid.UUID
    version_id: uuid.UUID | None
    clause_id: uuid.UUID | None
    content: str
    score: float
    is_current: bool

    def to_dict(self) -> dict:
        return {
            "chunk_id": str(self.chunk_id),
            "agreement_id": str(self.agreement_id),
            "version_id": str(self.version_id) if self.version_id else None,
            "clause_id": str(self.clause_id) if self.clause_id else None,
            "content": self.content,
            "score": round(self.score, 4),
            "is_current": self.is_current,
        }


def _base_chunk_query(
    *,
    organization_id: uuid.UUID,
    accessible_agreement_ids: list[uuid.UUID],
    agreement_id: uuid.UUID | None,
    scope: RetrievalScope | None,
):
    """Filters shared by both search strategies (spec 2.10.11)."""
    query = select(KnowledgeChunk).where(
        KnowledgeChunk.organization_id == organization_id,
        KnowledgeChunk.agreement_id.in_(accessible_agreement_ids or [uuid.uuid4()]),
    )
    if agreement_id is not None:
        query = query.where(KnowledgeChunk.agreement_id == agreement_id)
    if scope is not None:
        if scope.version_id is not None:
            query = query.where(KnowledgeChunk.version_id == scope.version_id)
        elif scope.as_of is not None:
            query = query.where(
                (KnowledgeChunk.effective_from.is_(None) | (KnowledgeChunk.effective_from <= scope.as_of.date())),
                (KnowledgeChunk.effective_to.is_(None) | (KnowledgeChunk.effective_to >= scope.as_of.date())),
            )
    return query


async def _retrieve_python(
    db: AsyncSession,
    *,
    query,
    question: str,
    limit: int,
    min_score: float,
) -> list[RetrievalHit]:
    """Python-side scoring (non-PostgreSQL dialects / JSON vectors)."""
    result = await db.execute(query)
    chunks = result.scalars().all()

    q_vec = embed_text(question)
    q_tokens = set(_tokenize(question))

    scored: list[RetrievalHit] = []
    for chunk in chunks:
        sim = cosine_similarity(q_vec, chunk.embedding or [])
        chunk_tokens = set(_tokenize(chunk.content))
        overlap = len(q_tokens & chunk_tokens) / max(len(q_tokens), 1)
        score = _hybrid_score(sim, overlap)
        if score < min_score:
            continue
        scored.append(_model_for_hit(chunk, score))

    scored.sort(key=lambda h: h.score, reverse=True)
    return scored[:limit]


def _model_for_hit(chunk: KnowledgeChunk, score: float) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk.id,
        agreement_id=chunk.agreement_id,
        version_id=chunk.version_id,
        clause_id=chunk.clause_id,
        content=chunk.content,
        score=score,
        is_current=chunk.is_current,
    )


async def _retrieve_pgvector(
    db: AsyncSession,
    *,
    query,
    question: str,
    limit: int,
    min_score: float,
) -> list[RetrievalHit]:
    """pgvector cosine search (PostgreSQL).

    Rows are ordered by SQL ``embedding <=> :query`` (HNSW-assisted) and the
    keyword boost is applied to the candidate set in Python. Chunks without
    an embedding (provider failure / legacy rows) still participate via the
    keyword score only.
    """
    from sqlalchemy import literal

    # Oversample so the Python-side re-ranking (keyword boost) still has a
    # meaningful candidate set after ordering by vector distance alone.
    fetch = max(limit * 8, 50)

    sql = (
        query.add_columns(KnowledgeChunk.embedding.cosine_distance(
            literal(embed_text(question), KnowledgeChunk.embedding.type)
        ).label("distance"))
        .order_by("distance")
        .limit(fetch)
    )
    result = await db.execute(sql)
    rows = result.all()

    q_tokens = set(_tokenize(question))
    scored: list[RetrievalHit] = []
    for chunk, distance in rows:
        if distance is not None:
            sim = max(0.0, min(1.0, 1.0 - float(distance)))
        else:
            sim = 0.0
        chunk_tokens = set(_tokenize(chunk.content))
        overlap = len(q_tokens & chunk_tokens) / max(len(q_tokens), 1)
        score = _hybrid_score(sim, overlap)
        if score < min_score:
            continue
        scored.append(_model_for_hit(chunk, score))

    scored.sort(key=lambda h: h.score, reverse=True)
    return scored[:limit]


async def retrieve(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    question: str,
    accessible_agreement_ids: list[uuid.UUID],
    agreement_id: uuid.UUID | None = None,
    scope: RetrievalScope | None = None,
    limit: int = 8,
    min_score: float = 0.05,
) -> list[RetrievalHit]:
    """Permission-filtered hybrid retrieval.

    Filters: organization, accessible agreements, optional agreement,
    optional temporal scope. Scores = cosine(embedding) boosted by keyword
    overlap. Never returns chunks from inaccessible agreements.
    """
    query = _base_chunk_query(
        organization_id=organization_id,
        accessible_agreement_ids=accessible_agreement_ids,
        agreement_id=agreement_id,
        scope=scope,
    )

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        try:
            return await _retrieve_pgvector(
                db,
                query=query,
                question=question,
                limit=limit,
                min_score=min_score,
            )
        except Exception:
            # pgvector unavailable (extension missing / type mismatch):
            # degrade to Python scoring rather than failing the answer path.
            import logging

            logging.getLogger(__name__).exception(
                "pgvector retrieval failed; falling back to Python scoring"
            )

    return await _retrieve_python(
        db,
        query=query,
        question=question,
        limit=limit,
        min_score=min_score,
    )


# ---------------------------------------------------------------------------
# Citation verification
# ---------------------------------------------------------------------------

@dataclass
class AnswerCitation:
    source_number: int
    agreement_id: uuid.UUID
    version_id: uuid.UUID | None
    clause_id: uuid.UUID | None
    quote: str


class CitationError(Exception):
    """Raised when an answer cites evidence that does not exist."""


def verify_citations(
    citations: list[AnswerCitation],
    evidence: list[RetrievalHit],
) -> list[AnswerCitation]:
    """Verify every citation against actual retrieved evidence.

    A citation is valid when its quote appears in the evidence chunk
    content (normalized). Invalid citations raise CitationError — answers
    must never cite fabricated evidence (spec 2.10.17).
    """
    valid: list[AnswerCitation] = []
    evidence_texts = [e.content for e in evidence]

    def normalize(s: str) -> str:
        return re.sub(r"\s+", " ", s.lower()).strip()

    for citation in citations:
        quote_norm = normalize(citation.quote)
        if not quote_norm:
            raise CitationError(f"Citation {citation.source_number} has an empty quote")
        if not any(quote_norm in normalize(t) for t in evidence_texts):
            raise CitationError(
                f"Citation {citation.source_number} is not supported by retrieved evidence"
            )
        valid.append(citation)
    return valid


# ---------------------------------------------------------------------------
# Answer synthesis (grounded, no-hallucination policy)
# ---------------------------------------------------------------------------

@dataclass
class IntelligenceAnswer:
    status: str  # 'ANSWERED' | 'INSUFFICIENT_EVIDENCE' | 'REQUIRES_HUMAN_REVIEW'
    answer: str
    citations: list[dict] = field(default_factory=list)
    uncertainty: str | None = None
    suggested_actions: list[str] = field(default_factory=list)
    requires_human_review: bool = False


def synthesize_answer(
    question: str,
    hits: list[RetrievalHit],
    *,
    max_citations: int = 5,
) -> IntelligenceAnswer:
    """Build a grounded answer from retrieval hits.

    No-answer policy: if no evidence supports the question, return
    INSUFFICIENT_EVIDENCE instead of inventing a term (spec 2.10.19).
    """
    if not hits:
        return IntelligenceAnswer(
            status="INSUFFICIENT_EVIDENCE",
            answer="The retrieved contract evidence does not contain an answer to this question.",
            requires_human_review=True,
        )

    # Extract the most relevant sentences from top hits.
    sentences: list[str] = []
    for hit in hits[:max_citations]:
        for sent in re.split(r"(?<=[.;])\s+", hit.content):
            sent = sent.strip()
            if len(sent) > 30:
                sentences.append(sent)

    q_tokens = set(_tokenize(question))
    ranked = sorted(
        sentences,
        key=lambda s: len(q_tokens & set(_tokenize(s))),
        reverse=True,
    )[:3]

    if not ranked:
        return IntelligenceAnswer(
            status="REQUIRES_HUMAN_REVIEW",
            answer=(
                "Evidence was found but no direct passage answers the question. "
                "A legal professional should review the source clauses."
            ),
            citations=[
                {
                    "source_number": i + 1,
                    "agreement_id": str(h.agreement_id),
                    "version_id": str(h.version_id) if h.version_id else None,
                    "clause_id": str(h.clause_id) if h.clause_id else None,
                    "quote": h.content[:200],
                }
                for i, h in enumerate(hits[:max_citations])
            ],
            requires_human_review=True,
        )

    answer = "Based on the retrieved contract evidence: " + " ".join(ranked)
    return IntelligenceAnswer(
        status="ANSWERED",
        answer=answer,
        citations=[
            {
                "source_number": i + 1,
                "agreement_id": str(h.agreement_id),
                "version_id": str(h.version_id) if h.version_id else None,
                "clause_id": str(h.clause_id) if h.clause_id else None,
                "quote": h.content[:200],
            }
            for i, h in enumerate(hits[:max_citations])
        ],
        uncertainty="Low" if hits[0].score > 0.35 else "Medium",
        suggested_actions=[],
        requires_human_review=False,
    )


# ---------------------------------------------------------------------------
# Access helper
# ---------------------------------------------------------------------------

async def accessible_agreement_ids(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Agreement IDs the user may retrieve from (participant or org member)."""
    from app.models.agreement_access import AgreementParticipant

    result = await db.execute(
        select(AgreementParticipant.agreement_id).where(
            AgreementParticipant.user_id == user_id
        )
    )
    participant_ids = [row[0] for row in result.all()]

    org_result = await db.execute(
        select(Agreement.id).where(Agreement.organization_id == organization_id)
    )
    org_ids = [row[0] for row in org_result.all()]

    # Organization members see org agreements; participants may see
    # agreements they were added to even cross-org (lawyer scenario).
    return list(dict.fromkeys(participant_ids + org_ids))
