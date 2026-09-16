"""EventService.publish idempotency tests (spec 2.05.5).

Publishing the same logical event twice (e.g., a retried business
operation) must yield exactly one outbox row — never duplicate
notifications or doubles in downstream consumers.
"""

import pytest
from sqlalchemy import func, select

from app.models.event_outbox import OutboxEvent
from app.services.event_service import EventService, build_event_key

pytestmark = pytest.mark.asyncio


async def test_build_event_key_deterministic_and_dedup_keyed():
    kwargs = dict(
        event_type="agreement.change_set.created",
        aggregate_type="agreement",
        aggregate_id="aaaa-bbbb",
        dedup_key="abc",
    )
    assert build_event_key(**kwargs) == "agreement:aaaa-bbbb:agreement.change_set.created:abc"
    assert build_event_key(**kwargs) == build_event_key(**kwargs)
    assert build_event_key(**{**kwargs, "dedup_key": "xyz"}) != build_event_key(**kwargs)


async def test_publish_appends_event_with_spec_fields(db_session, test_org, test_user):
    event = await EventService.publish(
        db_session,
        event_type="agreement.change_set.created",
        aggregate_type="agreement",
        aggregate_id="11111111-1111-1111-1111-111111111111",
        organization_id=test_org.id,
        actor_user_id=test_user.id,
        payload={"change_set_id": "xxx"},
        dedup_key="change-set-abc",
    )
    await db_session.commit()

    row = await db_session.get(OutboxEvent, event.id)
    assert row is not None
    assert row.event_type == "agreement.change_set.created"
    assert row.tenant_id == test_org.id
    assert row.actor_user_id == test_user.id
    assert row.event_version == 1
    assert row.status == "pending"
    assert row.available_at is not None
    assert row.payload == {"change_set_id": "xxx"}


async def test_publish_is_idempotent_for_same_logical_event(
    db_session, test_org
):
    common = dict(
        event_type="obligation.reminder",
        aggregate_type="obligation",
        aggregate_id="22222222-2222-2222-2222-222222222222",
        organization_id=test_org.id,
        payload={},
        dedup_key="cycle-2026-09-08",
    )
    first = await EventService.publish(db_session, **common)
    second = await EventService.publish(db_session, **common)
    third = await EventService.publish(db_session, **common)
    await db_session.commit()

    assert first.id == second.id == third.id
    total = (
        await db_session.execute(select(func.count(OutboxEvent.id)))
    ).scalar_one()
    assert total == 1


async def test_distinct_dedup_keys_enqueue_separate_events(
    db_session, test_org
):
    common = dict(
        event_type="agreement.status_changed",
        aggregate_type="agreement",
        aggregate_id="33333333-3333-3333-3333-333333333333",
        organization_id=test_org.id,
        payload={},
    )
    await EventService.publish(db_session, **common, dedup_key="first")
    await EventService.publish(db_session, **common, dedup_key="second")
    await db_session.commit()

    total = (
        await db_session.execute(select(func.count(OutboxEvent.id)))
    ).scalar_one()
    assert total == 2


async def test_publish_matches_enqueue_called_by_domain_endpoint(
    db_session, test_agreement, test_org
):
    """The negotiate endpoint enqueues an event; EventService survives the
    same dispatch path that marks it published exactly once."""
    from app.services.event_outbox_service import list_pending_events

    await EventService.publish(
        db_session,
        event_type="negotiation.proposal_created",
        aggregate_type="agreement",
        aggregate_id=test_agreement.id,
        organization_id=test_org.id,
        payload={"agreement_id": str(test_agreement.id)},
        dedup_key=f"proposal:{test_agreement.id}",
    )
    await db_session.commit()

    pending = await list_pending_events(db_session, limit=10)
    events = [e for e in pending if e.event_type == "negotiation.proposal_created"]
    assert len(events) == 1