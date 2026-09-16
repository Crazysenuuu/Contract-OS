"""Tests for the outbox-flush beat task (spec 1.14 scheduler).

Verifies the full scheduled path: an event enqueued in a domain
transaction is picked up and flushed (published + Notification created)
by the celery beat task ``process_outbox_events``.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.event_outbox import OutboxEvent
from app.models.notification import Notification
from app.services.event_outbox_service import enqueue_event
from app.tasks.scheduler import process_outbox_events


class TestOutboxBeatTask:
    async def test_beat_task_flushes_enqueued_event(
        self,
        monkeypatch,
        db_session: AsyncSession,
        engine,
        test_org,
        test_user,
    ):
        # The beat task opens its own session via
        # app.core.database.AsyncSessionLocal; point it at the test engine
        # so the task operates on the same in-memory database.
        from app.core import database as database_module

        factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        monkeypatch.setattr(database_module, "AsyncSessionLocal", factory)

        # 1. Enqueue an event in a domain transaction.
        event = await enqueue_event(
            db_session,
            tenant_id=test_org.id,
            event_type="negotiation.proposal_created",
            aggregate_type="change_proposal",
            aggregate_id=None,
            payload={
                "recipient_email": "lawyer@firm.com",
                "recipient_user_id": str(test_user.id),
                "subject": "New proposal",
            },
        )
        await db_session.commit()
        assert event.status == "pending"

        # 2. Run the beat task (eager mode) — it opens its own session,
        #    publishes pending events and creates notifications.
        result = process_outbox_events(limit=10)

        assert result["delivered"] == 1
        assert result["failed"] == 0

        # 3. Verify the flush through the test session. The test session
        #    holds the enqueued event in its identity map (expire_on_commit
        #    is False), so refresh to see the task's committed changes.
        await db_session.refresh(event)
        assert event.status == "published"
        assert event.published_at is not None

        notif_result = await db_session.execute(
            select(Notification).where(
                Notification.organization_id == test_org.id
            )
        )
        notifications = list(notif_result.scalars().all())
        assert len(notifications) == 1
        assert notifications[0].to_email == "lawyer@firm.com"
        assert notifications[0].subject == "New proposal"

    async def test_beat_task_is_idempotent(
        self,
        monkeypatch,
        db_session: AsyncSession,
        engine,
        test_org,
        test_user,
    ):
        """A second run must not double-publish or double-notify."""
        from app.core import database as database_module

        factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        monkeypatch.setattr(database_module, "AsyncSessionLocal", factory)

        await enqueue_event(
            db_session,
            tenant_id=test_org.id,
            event_type="obligation.reminder",
            aggregate_type="obligation",
            payload={
                "recipient_email": "ops@corp.com",
                "recipient_user_id": str(test_user.id),
                "subject": "Obligation due",
            },
        )
        await db_session.commit()

        first = process_outbox_events(limit=10)
        second = process_outbox_events(limit=10)

        assert first["delivered"] == 1
        # Already published → nothing left to flush.
        assert second["delivered"] == 0

        notif_result = await db_session.execute(
            select(Notification).where(
                Notification.organization_id == test_org.id
            )
        )
        assert len(list(notif_result.scalars().all())) == 1