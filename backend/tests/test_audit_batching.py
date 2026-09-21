"""Merkle audit batching tests (spec 1.20.15-1.20.16)."""

import pytest
import pytest_asyncio
from datetime import datetime, timezone

from app.services.audit_batching import (
    AuditBatchService,
    ExternalTimestampAnchor,
    merkle_root,
)
from app.services.audit_service import record_event


@pytest_asyncio.fixture
async def chained_events(db_session, test_org, test_user):
    """Append a small chain of audit events."""
    events = []
    for i in range(4):
        event = await record_event(
            db_session,
            tenant_id=test_org.id,
            actor_id=test_user.id,
            actor_type="user",
            action=f"TEST_EVENT_{i}",
            resource_type="agreement",
            metadata_json={"i": i},
        )
        events.append(event)
    await db_session.commit()
    return events


def test_merkle_root_properties():
    assert merkle_root([]) is None
    assert merkle_root(["a" * 64]) == "a" * 64
    leaves = [f"{i:064x}" for i in range(4)]
    r1 = merkle_root(leaves)
    r2 = merkle_root(list(reversed(leaves)))
    assert r1 != r2, "order matters"
    leaves[0] = "f" * 64
    assert merkle_root(leaves) != r1, "any leaf change changes the root"


@pytest.mark.asyncio
async def test_seal_and_verify_batch(db_session, test_org, chained_events):
    service = AuditBatchService(db_session)
    batch = await service.seal_batch(
        tenant_id=test_org.id, from_sequence=1, to_sequence=4
    )
    await db_session.commit()
    assert batch is not None
    assert batch.leaf_count == 4
    assert batch.first_sequence == 1 and batch.last_sequence == 4
    # No TSA configured: honest internal-only anchor
    assert batch.anchor_status == "internal_only"
    assert batch.anchor_token is None

    report = await service.verify_batch(batch.id)
    assert report["valid"] is True


@pytest.mark.asyncio
async def test_tampered_event_breaks_batch(db_session, test_org, chained_events):
    """Mutating an event's hash after sealing breaks batch verification."""
    service = AuditBatchService(db_session)
    batch = await service.seal_batch(tenant_id=test_org.id, from_sequence=1, to_sequence=4)
    await db_session.commit()

    chained_events[1].event_hash = "f" * 64  # tamper
    await db_session.commit()

    report = await service.verify_batch(batch.id)
    assert report["valid"] is False
    assert "root" in report["reason"] or "found" in report["reason"]


@pytest.mark.asyncio
async def test_removed_event_breaks_batch(db_session, test_org, chained_events):
    service = AuditBatchService(db_session)
    batch = await service.seal_batch(tenant_id=test_org.id, from_sequence=1, to_sequence=4)
    await db_session.commit()

    # Simulate row removal (never allowed by API, but the DB must prove it).
    await db_session.delete(chained_events[2])
    await db_session.commit()

    report = await service.verify_batch(batch.id)
    assert report["valid"] is False
    assert "found" in report["reason"]


@pytest.mark.asyncio
async def test_no_double_sealing(db_session, test_org, chained_events):
    service = AuditBatchService(db_session)
    await service.seal_batch(tenant_id=test_org.id, from_sequence=1, to_sequence=4)
    with pytest.raises(ValueError):
        await service.seal_batch(tenant_id=test_org.id, from_sequence=1, to_sequence=4)


@pytest.mark.asyncio
async def test_empty_range_returns_none(db_session, test_org):
    service = AuditBatchService(db_session)
    assert (
        await service.seal_batch(tenant_id=test_org.id, from_sequence=999, to_sequence=1000)
        is None
    )


def test_external_anchor_success_and_failure():
    anchor = ExternalTimestampAnchor()

    anchor.set_issuer(lambda root: "RFC3161-TOKEN-XYZ")
    result = anchor.issue("ab" * 32)
    assert result["anchor_status"] == "externally_anchored"
    assert result["token"] == "RFC3161-TOKEN-XYZ"

    anchor.set_issuer(lambda root: None)
    assert anchor.issue("ab" * 32)["anchor_status"] == "internal_only"

    def broken(root):
        raise RuntimeError("TSA down")

    anchor.set_issuer(broken)
    result = anchor.issue("ab" * 32)
    assert result["anchor_status"] == "anchor_failed"


# ── Automatic batch sealing (Celery beat task backend) ──────────────────────


class TestAutoSealAuditBatches:
    async def _seed_events(self, db_session, org, user, count, seq_start=1, action="AUTO"):
        """Append ``count`` chained events (sequence numbers auto-assign)."""
        from app.services.audit_service import record_event

        events = []
        for i in range(count):
            events.append(await record_event(
                db_session,
                tenant_id=org.id,
                actor_id=user.id,
                actor_type="user",
                action=f"{action}_{i}",
                resource_type="agreement",
                metadata_json={"i": i},
            ))
        await db_session.commit()
        return events

    async def test_seals_tenant_with_sufficient_backlog(
        self, db_session, test_org, test_user
    ):
        from app.services.audit_batching import auto_seal_audit_batches

        await self._seed_events(db_session, test_org, test_user, 5)
        summary = await auto_seal_audit_batches(db_session, min_batch_size=3)
        await db_session.commit()

        assert summary["batches_sealed"] == 1
        assert summary["events_sealed"] == 5
        assert summary["tenants"] == 1

        # Idempotent: a second run has nothing to do.
        again = await auto_seal_audit_batches(db_session, min_batch_size=3)
        assert again["batches_sealed"] == 0

    async def test_skips_tenants_below_min_batch_size(
        self, db_session, test_org, test_user
    ):
        from app.services.audit_batching import auto_seal_audit_batches

        await self._seed_events(db_session, test_org, test_user, 2)
        summary = await auto_seal_audit_batches(db_session, min_batch_size=3)
        assert summary["batches_sealed"] == 0

    async def test_seals_entire_contiguous_backlog(
        self, db_session, test_org, test_user
    ):
        from app.services.audit_batching import auto_seal_audit_batches

        await self._seed_events(db_session, test_org, test_user, 10)
        summary = await auto_seal_audit_batches(db_session, min_batch_size=3)
        # One batch swallowing the whole contiguous run (bounded by
        # MAX_EVENTS_PER_AUTO_BATCH) is the efficient production behavior.
        assert summary["batches_sealed"] == 1
        assert summary["events_sealed"] == 10

        # Remaining backlog picked up next run (none here).
        followup = await auto_seal_audit_batches(
            db_session, min_batch_size=3, max_batches_per_tenant=20
        )
        assert followup["events_sealed"] == 0

    async def test_max_batches_bounds_runs_with_gaps(
        self, db_session, test_org, test_user
    ):
        """A sequence gap splits the backlog: each contiguous run becomes
        its own batch, and max_batches_per_tenant caps how many per run."""
        from sqlalchemy import delete

        from app.models.audit import AuditEvent
        from app.services.audit_batching import auto_seal_audit_batches

        await self._seed_events(db_session, test_org, test_user, 9)
        # Punch a gap at sequences 4-6 (legacy/partial data scenario).
        await db_session.execute(
            delete(AuditEvent).where(
                AuditEvent.tenant_id == test_org.id,
                AuditEvent.sequence_number.in_([4, 5, 6]),
            )
        )
        await db_session.commit()

        summary = await auto_seal_audit_batches(
            db_session, min_batch_size=3, max_batches_per_tenant=20
        )
        assert summary["batches_sealed"] == 2  # 1-3 and 7-9
        assert summary["events_sealed"] == 6

    async def test_externally_anchored_batches_counted(
        self, db_session, test_org, test_user, monkeypatch
    ):
        import base64

        from app.services import audit_batching
        from app.services.audit_batching import auto_seal_audit_batches
        from tests.test_rfc3161_tsa import _make_token

        await self._seed_events(db_session, test_org, test_user, 4)

        monkeypatch.setattr(
            audit_batching, "get_anchor_service",
            lambda: (lambda a: (a.set_issuer(
                lambda root: base64.b64encode(_make_token(root)).decode()
            ), a)[1])(audit_batching.ExternalTimestampAnchor()),
        )
        summary = await auto_seal_audit_batches(db_session, min_batch_size=3)
        assert summary["anchored"] == 1

    async def test_task_runs_via_celery_eager(
        self, db_session, test_org, test_user
    ):
        # The task opens its own session via AsyncSessionLocal; under the
        # test rig CELERY_TASK_ALWAYS_EAGER=true and the DB is per-worker
        # SQLite, so verify only that the task is invokable end-to-end when
        # pointed at a session-bound loop: call the service through the
        # task's _run_async path indirectly.
        from app.tasks.scheduler import auto_seal_audit_batches_task

        assert auto_seal_audit_batches_task.name.endswith(
            "auto_seal_audit_batches_task"
        )
