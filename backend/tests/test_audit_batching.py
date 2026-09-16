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
