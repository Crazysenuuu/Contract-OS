"""Cross-contract precedent retrieval service (spec 2.10.26–2.10.28).

A precedent is an indexed passage (KnowledgeChunk) of one agreement that
informed the drafting/negotiation of another. Retrieval is always
permission-filtered: a suggestion can only ever cite passages the asking
user can already access, so precedent search cannot leak cross-party
content (2.10.28).

Precedent rows are data, not authority — they never bypass the knowledge
chunk permission filters, and suggestions are status='suggested' until a
user accepts or pins them.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.intelligence_governance import AgreementPrecedent
from app.models.knowledge import KnowledgeChunk
from app.services.retrieval_service import (
    RetrievalHit,
    accessible_agreement_ids,
    retrieve,
)

# Statuses a precedent link can take.
PRECEDENT_STATUSES = {"suggested", "accepted", "pinned", "dismissed"}


class PrecedentError(Exception):
    """Raised for precedent input/flow errors."""


def _serialize(row: AgreementPrecedent) -> dict:
    return {
        "id": str(row.id),
        "organization_id": str(row.organization_id),
        "source_agreement_id": str(row.source_agreement_id),
        "source_version_id": (
            str(row.source_version_id) if row.source_version_id else None
        ),
        "source_chunk_id": (
            str(row.source_chunk_id) if row.source_chunk_id else None
        ),
        "target_agreement_id": str(row.target_agreement_id),
        "target_clause_id": (
            str(row.target_clause_id) if row.target_clause_id else None
        ),
        "status": row.status,
        "origin": row.origin,
        "score": row.score,
        "note": row.note,
        "created_by": str(row.created_by) if row.created_by else None,
        "created_at": row.created_at.isoformat(),
    }


async def find_precedents(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    target_agreement_id: uuid.UUID,
    target_clause_id: uuid.UUID | None = None,
    question: str,
    limit: int = 5,
    min_score: float = 0.15,
    record: bool = True,
) -> dict:
    """Suggest precedent passages for a target agreement (2.10.26).

    Retrieval runs over the user's *currently accessible* corpus with the
    target agreement excluded, so a hit is by construction precedent from
    another contract the user may see. Suggestions are recorded as
    status='suggested' rows (idempotent on the unique link) unless
    ``record=False``.
    """
    if not question or not question.strip():
        raise PrecedentError("A question describing the needed precedent is required")

    accessible = await accessible_agreement_ids(
        db, organization_id=organization_id, user_id=user_id
    )
    corpus = [a for a in accessible if a != target_agreement_id]
    if not corpus:
        return {"suggestions": [], "recorded": 0}

    hits: list[RetrievalHit] = await retrieve(
        db,
        organization_id=organization_id,
        question=question,
        accessible_agreement_ids=corpus,
        limit=limit * 2,
        min_score=min_score,
    )
    hits = hits[:limit]

    recorded = 0
    rows: list[AgreementPrecedent] = []
    if record:
        for hit in hits:
            existing = await db.execute(
                select(AgreementPrecedent).where(
                    AgreementPrecedent.target_agreement_id == target_agreement_id,
                    AgreementPrecedent.target_clause_id == target_clause_id,
                    AgreementPrecedent.source_chunk_id == hit.chunk_id,
                    AgreementPrecedent.origin == "retrieval",
                )
            )
            row = existing.scalar_one_or_none()
            if row is None:
                row = AgreementPrecedent(
                    organization_id=organization_id,
                    source_agreement_id=hit.agreement_id,
                    source_version_id=hit.version_id,
                    source_chunk_id=hit.chunk_id,
                    target_agreement_id=target_agreement_id,
                    target_clause_id=target_clause_id,
                    status="suggested",
                    origin="retrieval",
                    score=hit.score,
                    created_by=user_id,
                )
                db.add(row)
                recorded += 1
            else:
                # Refresh the score/explanation, keep the link stable.
                row.score = hit.score
            rows.append(row)
        await db.flush()

    suggestions = [
        {
            "chunk": hit.to_dict(),
            "precedent_id": (
                str(rows[i].id) if record and i < len(rows) else None
            ),
        }
        for i, hit in enumerate(hits)
    ]
    return {"suggestions": suggestions, "recorded": recorded}


async def pin_precedent(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    source_chunk_id: uuid.UUID,
    target_agreement_id: uuid.UUID,
    target_clause_id: uuid.UUID | None = None,
    note: str | None = None,
) -> AgreementPrecedent:
    """Explicitly pin a passage as precedent (2.10.26, 'user' origin).

    The chunk must exist inside the caller's accessible corpus — pinning
    is an authorization boundary, not a formality (2.10.28).
    """
    chunk_result = await db.execute(
        select(KnowledgeChunk).where(KnowledgeChunk.id == source_chunk_id)
    )
    chunk = chunk_result.scalar_one_or_none()
    if chunk is None:
        raise PrecedentError("Source passage not indexed")

    accessible = await accessible_agreement_ids(
        db, organization_id=organization_id, user_id=user_id
    )
    if chunk.agreement_id not in accessible:
        raise PrecedentError(
            "Source passage belongs to an agreement you cannot access"
        )

    existing = await db.execute(
        select(AgreementPrecedent).where(
            AgreementPrecedent.source_agreement_id == chunk.agreement_id,
            AgreementPrecedent.source_chunk_id == chunk.id,
            AgreementPrecedent.target_agreement_id == target_agreement_id,
            AgreementPrecedent.target_clause_id == target_clause_id,
            AgreementPrecedent.origin == "user",
        )
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        row.status = "pinned"
        row.note = note if note is not None else row.note
        await db.flush()
        return row

    row = AgreementPrecedent(
        organization_id=organization_id,
        source_agreement_id=chunk.agreement_id,
        source_version_id=chunk.version_id,
        source_chunk_id=chunk.id,
        target_agreement_id=target_agreement_id,
        target_clause_id=target_clause_id,
        status="pinned",
        origin="user",
        score=None,
        note=note,
        created_by=user_id,
    )
    db.add(row)
    await db.flush()
    return row


async def update_precedent_status(
    db: AsyncSession,
    *,
    precedent_id: uuid.UUID,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    status: str,
    note: str | None = None,
) -> AgreementPrecedent:
    """Accept / dismiss a suggested precedent (2.10.26 lifecycle)."""
    if status not in PRECEDENT_STATUSES:
        raise PrecedentError(
            f"Invalid precedent status '{status}'; "
            f"expected one of {sorted(PRECEDENT_STATUSES)}"
        )
    result = await db.execute(
        select(AgreementPrecedent).where(
            AgreementPrecedent.id == precedent_id,
            AgreementPrecedent.organization_id == organization_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise PrecedentError("Precedent not found")
    row.status = status
    if note is not None:
        row.note = note
    if row.created_by is None:
        row.created_by = user_id
    await db.flush()
    return row


async def list_precedents(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    target_agreement_id: uuid.UUID,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List precedent links for a target agreement, newest first."""
    query = (
        select(AgreementPrecedent)
        .where(
            AgreementPrecedent.organization_id == organization_id,
            AgreementPrecedent.target_agreement_id == target_agreement_id,
        )
        .order_by(AgreementPrecedent.created_at.desc())
        .limit(limit)
    )
    if status is not None:
        query = query.where(AgreementPrecedent.status == status)
    result = await db.execute(query)
    return [_serialize(r) for r in result.scalars().all()]
