"""Merkle-style audit batching + external timestamping (spec 1.20.15-16).

Periodically seals ranges of the per-tenant audit chain into batches:

    leaf_i      = event_hash of each event in the batch
    internal_i  = sha256(left || right) over sorted pairs
    batch_root  = single Merkle root for all leaves

Each batch record stores the root, the covered sequence range and an
external timestamp anchor. The anchor is *data*: when an RFC 3161 TSA
(or any notarization service) is configured, the returned token is stored;
without one the batch is sealed with an honest ``anchor_status =
'internal_only'`` so nobody mistakes it for externally witnessed (spec
1.8.18: do not pretend provenance you do not have).

Verification recomputes the root from the referenced events and compares it
with the stored root - any event mutated or removed after sealing breaks
the batch.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditBatch, AuditEvent


def _hash_pair(left: str, right: str) -> str:
    return hashlib.sha256(f"{left}{right}".encode("utf-8")).hexdigest()


def merkle_root(leaves: list[str]) -> str | None:
    """Compute a Merkle root over hex leaf hashes (duplicates last when odd)."""
    if not leaves:
        return None
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [_hash_pair(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


class ExternalTimestampAnchor:
    """Pluggable RFC 3161-style timestamp authority interface.

    Deployments register a real TSA client via ``set_issuer``. The default
    issuer returns ``None`` meaning 'no external witnessing available' -
    the batch is still sealed, but flagged internal_only.
    """

    def __init__(self):
        self._issuer = None

    def set_issuer(self, issuer) -> None:
        """issuer(root_hash: str) -> str | None (opaque token/der)."""
        self._issuer = issuer

    def issue(self, root_hash: str) -> dict:
        if self._issuer is None:
            return {"anchor_status": "internal_only", "token": None}
        try:
            token = self._issuer(root_hash)
        except Exception as exc:  # TSA outage must not block sealing
            return {
                "anchor_status": "anchor_failed",
                "token": None,
                "error": f"{type(exc).__name__}: {exc}"[:200],
            }
        if token is None:
            return {"anchor_status": "internal_only", "token": None}
        return {"anchor_status": "externally_anchored", "token": token}


_anchor = ExternalTimestampAnchor()


def get_anchor_service() -> ExternalTimestampAnchor:
    return _anchor


class AuditBatchService:
    """Seals audit event ranges into verifiable batches."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def seal_batch(
        self,
        *,
        tenant_id: uuid.UUID,
        from_sequence: int,
        to_sequence: int,
        actor_id: uuid.UUID | None = None,
    ) -> AuditBatch | None:
        """Seal events [from_sequence, to_sequence] for a tenant.

        Returns None when the range contains no events (nothing to seal).
        """
        result = await self.db.execute(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == tenant_id,
                AuditEvent.sequence_number >= from_sequence,
                AuditEvent.sequence_number <= to_sequence,
            )
            .order_by(AuditEvent.sequence_number.asc())
        )
        events = list(result.scalars().all())
        if not events:
            return None

        # Refuse to re-seal an already-batched event in the overlapping range.
        already = [e for e in events if e.batch_id is not None]
        if already:
            raise ValueError(
                f"Events {already[0].sequence_number}-{already[-1].sequence_number} "
                "already belong to a sealed batch"
            )

        leaves = [e.event_hash for e in events if e.event_hash]
        if len(leaves) != len(events):
            raise ValueError("Batch contains events without a hash - chain corrupt")

        root = merkle_root(leaves)
        anchor = get_anchor_service().issue(root)

        batch = AuditBatch(
            tenant_id=tenant_id,
            root_hash=root,
            leaf_count=len(leaves),
            first_sequence=events[0].sequence_number,
            last_sequence=events[-1].sequence_number,
            first_event_id=events[0].id,
            last_event_id=events[-1].id,
            anchored_at=datetime.now(timezone.utc) if anchor["token"] else None,
            anchor_status=anchor["anchor_status"],
            anchor_token=anchor["token"],
            created_by=actor_id,
        )
        self.db.add(batch)
        await self.db.flush()

        for event in events:
            event.batch_id = batch.id
        await self.db.flush()
        return batch

    async def verify_batch(self, batch_id: uuid.UUID) -> dict:
        """Recompute the Merkle root from live event rows and compare."""
        result = await self.db.execute(
            select(AuditBatch).where(AuditBatch.id == batch_id)
        )
        batch = result.scalar_one_or_none()
        if batch is None:
            return {"valid": False, "reason": "batch not found"}

        result = await self.db.execute(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == batch.tenant_id,
                AuditEvent.sequence_number >= batch.first_sequence,
                AuditEvent.sequence_number <= batch.last_sequence,
            )
            .order_by(AuditEvent.sequence_number.asc())
        )
        events = list(result.scalars().all())

        if len(events) != batch.leaf_count:
            return {
                "valid": False,
                "reason": f"expected {batch.leaf_count} events, found {len(events)} (events removed?)",
            }
        if any(e.batch_id != batch.id for e in events):
            return {"valid": False, "reason": "event batch assignment changed"}

        root = merkle_root([e.event_hash for e in events if e.event_hash])
        if root != batch.root_hash:
            return {"valid": False, "reason": "recomputed root does not match stored root"}

        return {
            "valid": True,
            "root_hash": batch.root_hash,
            "anchor_status": batch.anchor_status,
            "anchored_at": batch.anchored_at.isoformat() if batch.anchored_at else None,
            "leaf_count": batch.leaf_count,
        }
