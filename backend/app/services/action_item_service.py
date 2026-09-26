"""Action Center service (spec §3.10.13-19).

Creates, resolves and lists action items. Idempotent by construction: one
live item per (source_system, source_id); re-emitting the same source event
refreshes the existing row instead of duplicating it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_item import ActionItem

LIVE_STATUSES = ("pending",)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_action_item(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    action_type: str,
    title: str,
    source_system: str,
    source_id: uuid.UUID,
    description: str | None = None,
    assignee_user_id: uuid.UUID | None = None,
    priority: int = 50,
    action_url: str | None = None,
    expires_at: datetime | None = None,
    metadata: dict | None = None,
) -> tuple[ActionItem, bool]:
    """Create or refresh the live action item for a source record.

    Returns (item, created). When a live item already exists for the source
    record its fields are updated in place and ``created`` is False.
    """
    existing = (
        await db.execute(
            select(ActionItem).where(
                ActionItem.source_system == source_system,
                ActionItem.source_id == source_id,
                ActionItem.status.in_(LIVE_STATUSES),
            )
        )
    ).scalars().first()

    if existing is not None:
        existing.title = title
        existing.description = description
        existing.assignee_user_id = assignee_user_id
        existing.priority = priority
        existing.action_url = action_url
        existing.expires_at = expires_at
        existing.metadata_ = metadata
        await db.flush()
        return existing, False

    item = ActionItem(
        organization_id=organization_id,
        action_type=action_type,
        title=title,
        description=description,
        source_system=source_system,
        source_id=source_id,
        assignee_user_id=assignee_user_id,
        status="pending",
        priority=priority,
        action_url=action_url,
        expires_at=expires_at,
        metadata_=metadata,
    )
    db.add(item)
    await db.flush()
    return item, True


async def complete_action_item(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    completed_by: uuid.UUID,
) -> ActionItem | None:
    """Idempotent completion (§3.10.42): completing twice is a no-op."""
    item = await db.get(ActionItem, item_id)
    if item is None or item.status != "pending":
        return item
    item.status = "completed"
    item.completed_at = now_utc()
    item.completed_by = completed_by
    await db.flush()
    return item


async def dismiss_action_item(
    db: AsyncSession, *, item_id: uuid.UUID
) -> ActionItem | None:
    item = await db.get(ActionItem, item_id)
    if item is None or item.status != "pending":
        return item
    item.status = "dismissed"
    await db.flush()
    return item


async def resolve_source_item(
    db: AsyncSession, *, source_system: str, source_id: uuid.UUID
) -> int:
    """Complete every live item for a source record.

    Called by the owning subsystem when the underlying decision has been
    made elsewhere (e.g. an approval was decided, so its action item closes).
    Returns the number of items closed.
    """
    rows = (
        await db.execute(
            select(ActionItem).where(
                ActionItem.source_system == source_system,
                ActionItem.source_id == source_id,
                ActionItem.status.in_(LIVE_STATUSES),
            )
        )
    ).scalars().all()
    closed = 0
    for item in rows:
        item.status = "completed"
        item.completed_at = now_utc()
        closed += 1
    if closed:
        await db.flush()
    return closed


async def expire_stale_items(db: AsyncSession) -> int:
    """Mark pending items past ``expires_at`` as expired (§3.10.40)."""
    rows = (
        await db.execute(
            select(ActionItem).where(
                ActionItem.status == "pending",
                ActionItem.expires_at.is_not(None),
                ActionItem.expires_at < now_utc(),
            )
        )
    ).scalars().all()
    for item in rows:
        item.status = "expired"
    if rows:
        await db.flush()
    return len(rows)


async def list_action_items(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    include_completed: bool = False,
    limit: int = 100,
) -> list[ActionItem]:
    """The action-center queue: unassigned items (permission-gated upstream)
    plus the caller's own items, highest priority first."""
    from sqlalchemy import or_

    stmt = select(ActionItem).where(
        ActionItem.organization_id == org_id,
        ActionItem.status == ("pending" if not include_completed else ActionItem.status),
    )
    if not include_completed:
        stmt = stmt.where(ActionItem.status == "pending")
    if user_id is not None:
        stmt = stmt.where(
            or_(
                ActionItem.assignee_user_id == user_id,
                ActionItem.assignee_user_id.is_(None),
            )
        )
    stmt = stmt.order_by(ActionItem.priority.desc(), ActionItem.created_at.asc())
    stmt = stmt.limit(limit)
    return list((await db.execute(stmt)).scalars().all())


def serialize_item(item: ActionItem) -> dict:
    return {
        "id": str(item.id),
        "action_type": item.action_type,
        "title": item.title,
        "description": item.description,
        "source_system": item.source_system,
        "source_id": str(item.source_id),
        "status": item.status,
        "priority": item.priority,
        "action_url": item.action_url,
        "assignee_user_id": (
            str(item.assignee_user_id) if item.assignee_user_id else None
        ),
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
