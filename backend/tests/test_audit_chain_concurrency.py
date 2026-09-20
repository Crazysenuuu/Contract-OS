"""Concurrent-append regression tests for the audit hash chain.

Under Postgres, two concurrent writers can read the same chain head and race
to the same sequence_number; the unique constraint arbitrates the winner.
Before the savepoint+retry fix, the loser raised IntegrityError which 500'd
the whole user request (e.g. login). These tests pin the retry behavior.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEvent
from app.services import audit_service
from app.services.audit_service import record_event


@pytest.mark.asyncio
async def test_retry_on_sequence_conflict(db_session: AsyncSession, test_org):
    """A stale head read (the loser of a concurrent append) must retry from
    the current committed head and succeed instead of raising."""
    from app.services.audit_service import _get_chain_head

    # Committed chain: seq 1..2.
    for action in ("A", "B"):
        await record_event(
            db_session,
            tenant_id=test_org.id,
            actor_type="system",
            action=action,
        )
    await db_session.commit()

    # Simulate the race: the FIRST head read returns a stale head (seq=1)
    # even though seq=2 is already committed — exactly what the loser of a
    # concurrent append sees. Its insert then violates
    # uq_audit_events_tenant_sequence and the retry path must recover.
    original_get_head = audit_service._get_chain_head
    calls = {"n": 0}

    async def stale_head_once(db, tid):
        calls["n"] += 1
        head = await original_get_head(db, tid)
        if calls["n"] == 1:
            # Uncommitted stand-in for "the winner's row we didn't see".
            return AuditEvent(
                tenant_id=tid,
                action="GHOST",
                actor_type="system",
                sequence_number=1,
                event_hash="0" * 64,
            )
        return head

    audit_service._get_chain_head = stale_head_once
    try:
        event = await record_event(
            db_session,
            tenant_id=test_org.id,
            actor_type="system",
            action="RACY",
        )
    finally:
        audit_service._get_chain_head = original_get_head

    assert event.sequence_number == 3

    events = (
        (
            await db_session.execute(
                select(AuditEvent)
                .where(AuditEvent.tenant_id == test_org.id)
                .order_by(AuditEvent.sequence_number.asc())
            )
        )
        .scalars()
        .all()
    )

    # Exactly three events: the two seeded + the retried append.
    assert [e.sequence_number for e in events] == [1, 2, 3]
    # The retry re-read the real head (seq=2) and chained onto it.
    assert events[-1].prev_hash == events[1].event_hash
    # Stale head returned exactly once, then the real one on the retry.
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_record_event_persists_after_retry(
    db_session: AsyncSession, test_org
):
    """Sanity: after a conflict+retry, the event is queryable once committed."""
    await record_event(
        db_session,
        tenant_id=test_org.id,
        actor_type="system",
        action="PLAIN",
    )
    await db_session.commit()

    count = len(
        (
            await db_session.execute(
                select(AuditEvent).where(AuditEvent.tenant_id == test_org.id)
            )
        ).scalars().all()
    )
    assert count == 1
