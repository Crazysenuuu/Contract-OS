"""Audit service — tamper-evident hash chain (spec 1.20).

Every audit event is written with a per-tenant monotonic sequence number,
a hash of the previous event (``prev_hash``) and a hash of its own
canonical payload (``event_hash``). The first event's hash is persisted in
``AuditChainRoot`` so the head of the chain cannot be silently truncated.

``record_event`` is the ONLY supported way to append to the chain; all
call sites (lifecycle transitions, signing, workflow engine, documents)
route through it. Verification walks the chain and recomputes every hash.

Anti-deletion protection:
  - Application level: ``delete`` and ``update`` of chain fields are
    blocked by the service/API.
  - Database level: the migration installs PostgreSQL triggers that reject
    DELETE and UPDATE of audit_events outside of an explicit
    ``audit.maintenance`` flag (see migrations).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditChainRoot, AuditEvent, AuditEvidence


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(data: Any) -> str:
    """Deterministic JSON serialization (sorted keys, stable separators)."""
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _canonical_datetime(dt: datetime | None) -> str | None:
    """Canonical UTC ISO string — stable across storage round-trips.

    SQLite returns naive datetimes on load; Postgres returns aware ones.
    Normalizing both to UTC guarantees the recomputed hash at verification
    time equals the hash computed at insert time.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def _event_payload(event: AuditEvent) -> dict:
    """Canonical payload hashed into ``event_hash``.

    Only identity + immutable content fields are included — never fields
    that are legitimately updated later (none exist today, but keeping the
    payload definition explicit makes that contract visible).
    """
    return {
        "tenant_id": str(event.tenant_id),
        "agreement_id": str(event.agreement_id) if event.agreement_id else None,
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "actor_type": event.actor_type,
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": str(event.resource_id) if event.resource_id else None,
        "metadata_json": event.metadata_json or {},
        "ip_address": event.ip_address,
        "created_at": _canonical_datetime(event.created_at),
    }


def compute_event_hash(
    prev_hash: str | None,
    *,
    tenant_id: uuid.UUID,
    agreement_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    actor_type: str,
    action: str,
    resource_type: str | None,
    resource_id: uuid.UUID | None,
    metadata_json: dict | None,
    ip_address: str | None,
    created_at: datetime,
) -> str:
    """Compute the hash of an event given the previous chain hash."""
    payload = _event_payload(
        AuditEvent(
            tenant_id=tenant_id,
            agreement_id=agreement_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_json=metadata_json,
            ip_address=ip_address,
            created_at=created_at,
        )
    )
    chain_input = (prev_hash or "") + _canonical_json(payload)
    return hashlib.sha256(chain_input.encode("utf-8")).hexdigest()


async def _get_chain_head(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> AuditEvent | None:
    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.sequence_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# Concurrent appends (e.g. two users logging in at once) can read the same
# chain head and race to the same sequence number; the unique constraint
# "uq_audit_events_tenant_sequence" arbitrates the winner. The loser retries
# from the *current* committed head — under READ COMMITTED the re-SELECT sees
# the winner's row — so concurrent writers serialize instead of erroring.
_SEQUENCE_CONFLICT_RETRIES = 5


# Historical sentinel some callers still pass for "no human actor". It is
# normalized to NULL before insert: audit_events.actor_id carries a FK to
# users.id, and a zero UUID is not a real user (this only surfaced on
# PostgreSQL — SQLite-based tests do not enforce the constraint).
_SYSTEM_ACTOR_SENTINEL = uuid.UUID(int=0)


async def record_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agreement_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    actor_type: str,
    action: str,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    metadata_json: dict | None = None,
    ip_address: str | None = None,
    created_at: datetime | None = None,
) -> AuditEvent:
    """Append an event to the tenant's hash chain.

    Sequence numbers are assigned from the persisted chain head inside the
    caller's transaction (never from application memory). Concurrent appends
    serialize on the unique constraint via a savepoint + retry; a genuine
    constraint failure after exhausting retries still raises.
    """
    # Normalize the legacy system-actor sentinel to NULL so the FK to
    # users.id is never violated; actor_type (e.g. "system") preserves the
    # information that no human performed the action.
    if actor_id is not None and actor_id == _SYSTEM_ACTOR_SENTINEL:
        actor_id = None
    created_at = created_at or now_utc()
    last_error: Exception | None = None
    for _ in range(_SEQUENCE_CONFLICT_RETRIES):
        try:
            # Savepoint: roll back only the conflicted INSERT, keeping the
            # caller's surrounding transaction alive for the retry.
            async with db.begin_nested():
                return await _append_event(
                    db,
                    tenant_id=tenant_id,
                    agreement_id=agreement_id,
                    actor_id=actor_id,
                    actor_type=actor_type,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    metadata_json=metadata_json,
                    ip_address=ip_address,
                    created_at=created_at,
                )
        except IntegrityError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


async def _append_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agreement_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    actor_type: str,
    action: str,
    resource_type: str | None,
    resource_id: uuid.UUID | None,
    metadata_json: dict | None,
    ip_address: str | None,
    created_at: datetime,
) -> AuditEvent:
    head = await _get_chain_head(db, tenant_id)
    prev_hash = head.event_hash if head is not None else None
    sequence_number = ((head.sequence_number or 0) + 1) if head is not None else 1

    event_hash = compute_event_hash(
        prev_hash,
        tenant_id=tenant_id,
        agreement_id=agreement_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=metadata_json,
        ip_address=ip_address,
        created_at=created_at,
    )

    event = AuditEvent(
        tenant_id=tenant_id,
        agreement_id=agreement_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=metadata_json or {},
        ip_address=ip_address,
        created_at=created_at,
        sequence_number=sequence_number,
        prev_hash=prev_hash,
        event_hash=event_hash,
    )
    db.add(event)
    await db.flush()

    # Persist the chain root on the first event so truncation is detectable.
    if sequence_number == 1:
        root = await db.execute(
            select(AuditChainRoot).where(
                AuditChainRoot.tenant_id == tenant_id
            )
        )
        existing = root.scalar_one_or_none()
        if existing is None:
            db.add(
                AuditChainRoot(
                    tenant_id=tenant_id,
                    root_hash=event_hash,
                    first_event_id=event.id,
                )
            )
        else:
            # Chain root already exists — sequence numbering is per-tenant
            # and monotonic, so a second seq=1 means the head was deleted.
            # Preserve the original root (verification will flag the gap).
            pass

    await db.flush()
    return event


async def verify_chain(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    agreement_id: uuid.UUID | None = None,
) -> dict:
    """Walk the tenant's chain and verify every hash.

    Returns ``valid`` plus diagnostic details. When ``agreement_id`` is
    given, verification covers the events for that agreement; because the
    chain itself is tenant-wide, integrity of the full chain is always
    checked and only the agreement-scoped subset is reported.
    """
    root_result = await db.execute(
        select(AuditChainRoot).where(
            AuditChainRoot.tenant_id == tenant_id
        )
    )
    root = root_result.scalar_one_or_none()

    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.sequence_number.asc())
    )
    events = list(result.scalars().all())

    if not events:
        return {
            "valid": True,
            "checked": 0,
            "message": "No audit events in chain",
        }

    if root is None:
        return {
            "valid": False,
            "checked": 0,
            "message": "Audit chain root is missing — chain head may have been deleted",
        }

    expected_prev: str | None = None
    first_broken: dict | None = None
    for idx, event in enumerate(events):
        if event.sequence_number != idx + 1:
            first_broken = {
                "event_id": str(event.id),
                "reason": f"sequence gap/out-of-order: expected {idx + 1}, got {event.sequence_number}",
            }
            break
        if event.prev_hash != expected_prev:
            first_broken = {
                "event_id": str(event.id),
                "reason": "prev_hash does not match previous event_hash",
            }
            break
        recomputed = compute_event_hash(
            expected_prev,
            tenant_id=event.tenant_id,
            agreement_id=event.agreement_id,
            actor_id=event.actor_id,
            actor_type=event.actor_type,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            metadata_json=event.metadata_json,
            ip_address=event.ip_address,
            created_at=event.created_at,
        )
        if recomputed != event.event_hash:
            first_broken = {
                "event_id": str(event.id),
                "reason": "event_hash does not match canonical payload",
            }
            break
        expected_prev = event.event_hash

    if first_broken is not None:
        return {
            "valid": False,
            "checked": events.index(
                next(e for e in events if str(e.id) == first_broken["event_id"])
            )
            + 1,
            "first_broken": first_broken,
            "message": "Audit chain integrity violation detected",
        }

    if root.root_hash != events[0].event_hash:
        return {
            "valid": False,
            "checked": len(events),
            "first_broken": {
                "event_id": str(events[0].id),
                "reason": "chain root hash does not match first event hash",
            },
            "message": "Audit chain head has been tampered with",
        }

    return {
        "valid": True,
        "checked": len(events),
        "message": "Audit chain is intact",
    }


async def create_evidence(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agreement_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
    evidence_type: str,
    content_hash: str,
    content_ref: str | None = None,
    metadata_json: dict | None = None,
    created_by: uuid.UUID | None = None,
) -> AuditEvidence:
    """Persist an evidence snapshot (document hash at a point in time)."""
    evidence = AuditEvidence(
        tenant_id=tenant_id,
        agreement_id=agreement_id,
        version_id=version_id,
        evidence_type=evidence_type,
        content_hash=content_hash,
        content_ref=content_ref,
        metadata_json=metadata_json or {},
        created_by=created_by,
    )
    db.add(evidence)
    await db.flush()
    return evidence


async def list_evidence(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> list[AuditEvidence]:
    result = await db.execute(
        select(AuditEvidence)
        .where(AuditEvidence.agreement_id == agreement_id)
        .order_by(AuditEvidence.created_at.asc())
    )
    return list(result.scalars().all())


async def export_history(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    limit: int = 1000,
) -> list[dict]:
    """Export an agreement's complete audit history as serializable dicts."""
    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.agreement_id == agreement_id)
        .order_by(AuditEvent.sequence_number.asc())
        .limit(limit)
    )
    events = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "sequence_number": e.sequence_number,
            "tenant_id": str(e.tenant_id),
            "agreement_id": str(e.agreement_id) if e.agreement_id else None,
            "actor_id": str(e.actor_id) if e.actor_id else None,
            "actor_type": e.actor_type,
            "action": e.action,
            "resource_type": e.resource_type,
            "resource_id": str(e.resource_id) if e.resource_id else None,
            "metadata_json": e.metadata_json or {},
            "ip_address": e.ip_address,
            "prev_hash": e.prev_hash,
            "event_hash": e.event_hash,
            "created_at": e.created_at.isoformat(),
        }
        for e in events
    ]