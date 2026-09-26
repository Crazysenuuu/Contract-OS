"""Ingestion intelligence service (spec §3.21.22-46, §3.21.85-96).

Candidates never become truth implicitly: verification is explicit, promotion
writes to a named target and closes the candidate, conflicts are detected on
competing values and resolved by rule or human, and every pipeline stage
appends provenance with content hashes.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingestion_intelligence import (
    ExtractionCandidate,
    ExtractionConflict,
    ExtractionProvenance,
)


class CandidateError(Exception):
    pass


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hash_payload(payload) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def create_candidate(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    document_id: uuid.UUID,
    candidate_type: str,
    field_key: str,
    value: dict | None = None,
    value_text: str | None = None,
    source_ref: dict | None = None,
    confidence: float = 0.0,
) -> ExtractionCandidate:
    """Record one extracted assertion (never authoritative yet)."""
    candidate = ExtractionCandidate(
        organization_id=organization_id,
        document_id=document_id,
        candidate_type=candidate_type,
        field_key=field_key,
        value=value,
        value_text=value_text,
        source_ref=source_ref,
        confidence=confidence,
        status="pending",
    )
    db.add(candidate)
    await db.flush()
    await detect_conflicts(db, organization_id=organization_id,
                           document_id=document_id, field_key=field_key)
    return candidate


async def verify_candidate(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    verified_by: uuid.UUID,
    accept: bool,
) -> ExtractionCandidate:
    """Human verification (§3.21.25). Idempotent: only pending candidates."""
    candidate = await db.get(ExtractionCandidate, candidate_id)
    if candidate is None:
        raise CandidateError("Candidate not found")
    if candidate.status != "pending":
        return candidate
    candidate.status = "verified" if accept else "rejected"
    candidate.verified_by = verified_by
    candidate.verified_at = now_utc()
    await db.flush()
    return candidate


async def promote_candidate(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    promoted_by: uuid.UUID,
    target: str,
    target_id: uuid.UUID,
) -> ExtractionCandidate:
    """Promote a verified candidate into a domain table (§3.21.26-28).

    Only verified candidates can be promoted; the promotion records where
    the value landed so provenance queries can follow the chain.
    """
    candidate = await db.get(ExtractionCandidate, candidate_id)
    if candidate is None:
        raise CandidateError("Candidate not found")
    if candidate.status != "verified":
        raise CandidateError(
            "Only verified candidates can be promoted "
            f"(candidate is {candidate.status})"
        )
    candidate.status = "promoted"
    candidate.promoted_target = target
    candidate.promoted_target_id = target_id
    await db.flush()
    return candidate


async def detect_conflicts(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    document_id: uuid.UUID,
    field_key: str,
) -> ExtractionConflict | None:
    """Flag a conflict when pending candidates disagree on one field
    (§3.21.44-45). Deterministic rule: values disagree when their JSON
    payloads differ."""
    candidates = (
        await db.execute(
            select(ExtractionCandidate).where(
                ExtractionCandidate.organization_id == organization_id,
                ExtractionCandidate.document_id == document_id,
                ExtractionCandidate.field_key == field_key,
                ExtractionCandidate.status == "pending",
            )
        )
    ).scalars().all()

    if len(candidates) < 2:
        return None
    payloads = {
        _hash_payload({"v": c.value, "t": c.value_text}) for c in candidates
    }
    if len(payloads) < 2:
        return None

    existing = (
        await db.execute(
            select(ExtractionConflict).where(
                ExtractionConflict.organization_id == organization_id,
                ExtractionConflict.document_id == document_id,
                ExtractionConflict.field_key == field_key,
                ExtractionConflict.status == "open",
            )
        )
    ).scalars().first()
    if existing is not None:
        existing.candidate_ids = {
            "ids": [str(c.id) for c in candidates]
        }
        await db.flush()
        return existing

    conflict = ExtractionConflict(
        organization_id=organization_id,
        document_id=document_id,
        field_key=field_key,
        candidate_ids={"ids": [str(c.id) for c in candidates]},
    )
    db.add(conflict)
    await db.flush()
    return conflict


async def resolve_conflict(
    db: AsyncSession,
    *,
    conflict_id: uuid.UUID,
    winner_candidate_id: uuid.UUID,
    resolved_by: uuid.UUID,
    rule: str = "human_choice",
) -> ExtractionConflict:
    """Resolve a conflict: the winner survives, losers are rejected."""
    conflict = await db.get(ExtractionConflict, conflict_id)
    if conflict is None:
        raise CandidateError("Conflict not found")
    winner = await db.get(ExtractionCandidate, winner_candidate_id)
    if winner is None or winner.document_id != conflict.document_id:
        raise CandidateError("Winner candidate is not part of this conflict")

    for candidate_id in (conflict.candidate_ids or {}).get("ids", []):
        candidate = await db.get(ExtractionCandidate, uuid.UUID(candidate_id))
        if candidate is None or candidate.status != "pending":
            continue
        candidate.status = "verified" if candidate.id == winner.id else "rejected"
        candidate.verified_by = resolved_by
        candidate.verified_at = now_utc()

    conflict.status = "resolved"
    conflict.winner_candidate_id = winner.id
    conflict.resolution_rule = rule
    conflict.resolved_by = resolved_by
    conflict.resolved_at = now_utc()
    await db.flush()
    return conflict


async def record_provenance(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    document_id: uuid.UUID,
    stage: str,
    input_payload=None,
    output_payload=None,
    parser_version: str | None = None,
    details: dict | None = None,
) -> ExtractionProvenance:
    """Append one provenance stage with input/output hashes (§3.21.85-87)."""
    record = ExtractionProvenance(
        organization_id=organization_id,
        document_id=document_id,
        stage=stage,
        parser_version=parser_version,
        input_hash=(
            _hash_payload(input_payload) if input_payload is not None else None
        ),
        output_hash=(
            _hash_payload(output_payload) if output_payload is not None else None
        ),
        details=details,
        created_at=now_utc(),
    )
    db.add(record)
    await db.flush()
    return record


async def provenance_chain(
    db: AsyncSession, *, document_id: uuid.UUID
) -> list[dict]:
    """The full extraction audit trail for one document, oldest first."""
    rows = (
        await db.execute(
            select(ExtractionProvenance)
            .where(ExtractionProvenance.document_id == document_id)
            .order_by(ExtractionProvenance.created_at.asc())
        )
    ).scalars().all()
    return [
        {
            "id": str(r.id),
            "stage": r.stage,
            "parser_version": r.parser_version,
            "input_hash": r.input_hash,
            "output_hash": r.output_hash,
            "details": r.details,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
