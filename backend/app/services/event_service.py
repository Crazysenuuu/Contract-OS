"""EventService (spec 2.05.5 / 2.05.6 outbox service).

A transactional outbox publisher. Domain services publish events in the
SAME transaction as their state change; the surrounding call commits.
Publishing is idempotent: a deterministic ``event_key`` (derived from the
aggregate + event type + a caller-provided dedup key) ensures a retried
business operation enqueues the logical event exactly once.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_outbox import OutboxEvent


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def build_event_key(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID | None,
    dedup_key: str | None,
) -> str:
    """Deterministic key for the logical event.

    Two publishes describing the same logical occurrence produce the same
    key, so retries collapse to a single outbox row.
    """
    bits = [aggregate_type, aggregate_id, event_type]
    if dedup_key:
        bits.append(dedup_key)
    return ":".join(str(b).strip() for b in bits)


class EventService:
    """Publish domain events to the transactional outbox."""

    @staticmethod
    async def publish(
        db: AsyncSession,
        *,
        event_type: str,
        # Spec §62: event_type should be a known type from the catalogue.
        # Unknown types are allowed but logged for observability.
        aggregate_type: str,
        aggregate_id: UUID,
        organization_id: UUID,
        payload: dict,
        actor_user_id: UUID | None = None,
        event_version: int = 1,
        dedup_key: str | None = None,
    ) -> OutboxEvent:
        """Append an event to the outbox, deduplicating identical logs.

        The caller does not commit here — the surrounding business
        transaction commits everything together (spec 2.05.5).
        """
        event_key = build_event_key(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            dedup_key=dedup_key,
        )

        # Idempotency: a previously-published logical event is returned
        # instead of being enqueued again.
        existing = (
            await db.execute(
                select(OutboxEvent).where(OutboxEvent.event_key == event_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        event = OutboxEvent(
            tenant_id=organization_id,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            actor_user_id=actor_user_id,
            payload=payload or {},
            event_version=event_version,
            event_key=event_key,
            status="pending",
            attempts=0,
            available_at=now_utc(),
            next_attempt_at=now_utc(),
        )
        db.add(event)
        await db.flush()
        return event

    @staticmethod
    async def publish_once(
        db: AsyncSession,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: UUID,
        organization_id: UUID,
        payload: dict,
        actor_user_id: UUID | None = None,
        event_version: int = 1,
        dedup_key: str | None = None,
    ) -> OutboxEvent:
        """Alias of publish with the idempotency contract made explicit."""
        return await EventService.publish(
            db,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            organization_id=organization_id,
            payload=payload,
            actor_user_id=actor_user_id,
            event_version=event_version,
            dedup_key=dedup_key,
        )