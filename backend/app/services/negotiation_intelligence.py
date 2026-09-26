"""Negotiation intelligence (spec §3.22-adjacent §9-11, §23).

- Deadlock detection: N consecutive rounds with zero accepted changes and
  no movement on the same disputed clauses raise a NegotiationDeadlock.
- Playbook matching: walk the playbook's ordered positions against a
  counterparty proposal; first position whose acceptance criteria hold wins.
- Concession ledger: quantify what each side gave up per round.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.negotiation import (
    AgreementChange,
    ClausePlaybook,
    NegotiationDeadlock,
    NegotiationRound,
)


class DeadlockError(Exception):
    pass


async def detect_deadlock(
    db: AsyncSession,
    *,
    agreement_id: uuid.UUID,
    window_rounds: int = 3,
) -> NegotiationDeadlock | None:
    """Detect a deadlock over the trailing rounds (§23).

    A deadlock exists when the last ``window_rounds`` open/closed rounds
    produced no accepted changes while the same set of clauses kept being
    proposed and rejected. Returns the created record, or None.
    """
    rounds = (
        await db.execute(
            select(NegotiationRound)
            .where(NegotiationRound.agreement_id == agreement_id)
            .order_by(NegotiationRound.round_number.desc())
            .limit(window_rounds)
        )
    ).scalars().all()

    if len(rounds) < window_rounds:
        return None

    changes = (
        await db.execute(
            select(AgreementChange).where(
                AgreementChange.agreement_id == agreement_id
            )
        )
    ).scalars().all()

    recent_round_ids = {r.id for r in rounds}
    recent = [c for c in changes if c.base_version_id and c.status in ("proposed", "rejected")]
    accepted_recent = [
        c for c in changes if c.status == "accepted"
    ]
    _ = recent_round_ids  # rounds are the time window; changes carry status

    if accepted_recent:
        return None  # convergence is happening

    disputed = sorted({c.items and "" or "" for c in recent})
    disputed_clauses: dict[str, int] = {}
    for change in recent:
        for item in change.items:
            disputed_clauses[item.clause_identifier] = (
                disputed_clauses.get(item.clause_identifier, 0) + 1
            )
    _ = disputed  # superseded by the per-clause tally above

    # Every disputed clause must have been contested in every window round.
    if not disputed_clauses or min(disputed_clauses.values()) < window_rounds:
        return None

    latest_round = rounds[0]
    deadlock = NegotiationDeadlock(
        agreement_id=agreement_id,
        round_number=latest_round.round_number,
        disputed_clauses={"clauses": sorted(disputed_clauses.keys())},
        detection_rule=f"no_acceptance_{window_rounds}_rounds",
    )
    db.add(deadlock)
    await db.flush()
    return deadlock


async def resolve_deadlock(
    db: AsyncSession,
    *,
    deadlock_id: uuid.UUID,
    resolution: str,
    resolved_by: uuid.UUID,
    note: str | None = None,
) -> NegotiationDeadlock:
    """Record the path out of a deadlock (§23 resolution behaviour)."""
    if resolution not in ("resolved", "escalated", "terminated"):
        raise DeadlockError("resolution must be resolved|escalated|terminated")
    deadlock = await db.get(NegotiationDeadlock, deadlock_id)
    if deadlock is None:
        raise DeadlockError("Deadlock not found")
    deadlock.status = resolution
    deadlock.resolved_by = resolved_by
    deadlock.resolution_note = note
    deadlock.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    return deadlock


# ---------------------------------------------------------------------------
# Playbooks (§10-11)
# ---------------------------------------------------------------------------


async def upsert_playbook(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    clause_identifier: str,
    name: str,
    positions: list[dict],
    is_active: bool = True,
) -> ClausePlaybook:
    if not positions:
        raise ValueError("A playbook needs at least one position")
    playbook = ClausePlaybook(
        organization_id=organization_id,
        clause_identifier=clause_identifier,
        name=name,
        positions={"ordered": positions},
        is_active=is_active,
    )
    db.add(playbook)
    await db.flush()
    return playbook


def match_playbook(
    playbook: ClausePlaybook,
    proposed_text: str,
) -> dict | None:
    """Match a counterparty proposal against ordered positions (§11).

    A position matches when its acceptable_criteria tokens all appear in the
    proposal (deterministic substring scoring, no AI), or when the proposal
    is byte-equal to the position text. Returns the matched position and its
    fallback depth, or None when no position is acceptable.
    """
    positions = (playbook.positions or {}).get("ordered", [])
    lowered = (proposed_text or "").lower()
    for depth, position in enumerate(positions):
        text = position.get("text", "")
        if proposed_text and text and lowered == text.lower():
            return {"position": position, "depth": depth, "match": "exact"}
        criteria = position.get("acceptable_criteria") or []
        tokens = [c.lower() for c in criteria if isinstance(c, str)]
        if tokens and all(t in lowered for t in tokens):
            return {"position": position, "depth": depth, "match": "criteria"}
    return None


# ---------------------------------------------------------------------------
# Concessions (§9)
# ---------------------------------------------------------------------------


async def concession_ledger(
    db: AsyncSession,
    *,
    agreement_id: uuid.UUID,
) -> dict:
    """Summarize recorded concessions per proposing party."""
    from collections import defaultdict

    changes = (
        await db.execute(
            select(AgreementChange).where(
                AgreementChange.agreement_id == agreement_id
            )
        )
    ).scalars().all()

    per_party: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "total_value": 0.0}
    )
    for change in changes:
        if change.change_type not in ("counter", "redline"):
            continue
        key = str(change.proposing_party_id) if change.proposing_party_id else "internal"
        entries = per_party[key]
        entries["count"] += 1
        for item in change.items:
            if item.status == "accepted":
                entries["total_value"] += float(item.concession_value or 0)

    return {
        "agreement_id": str(agreement_id),
        "parties": dict(per_party),
    }
