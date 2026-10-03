"""Tenant context for PostgreSQL Row-Level Security (spec 1.22).

RLS policies on tenant-scoped tables read ``current_setting('app.current_tenant')``
to decide row visibility. Every request that resolves the caller's
organization sets this context (see ``get_current_organization_id``), so
queries in the same transaction are automatically tenant-scoped by the
database — defense in depth on top of application-level scoping.

SQLite (tests) has no such mechanism; the helper is a no-op there.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import TypeVar

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _is_postgres(db: AsyncSession) -> bool:
    """Whether this session talks to PostgreSQL (and therefore honours RLS).

    Reads the dialect defensively: ``db.bind`` is absent on stand-in sessions
    used by tests and in a few service wrappers, and those are never
    PostgreSQL, so they must be a silent no-op rather than an AttributeError
    inside a background job.
    """
    dialect = getattr(getattr(db, "bind", None), "dialect", None)
    return bool(dialect and dialect.name == "postgresql")


async def set_tenant_context(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> None:
    """Set ``app.current_tenant`` for the current transaction (Postgres only)."""
    if not _is_postgres(db):
        return
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )


async def clear_tenant_context(db: AsyncSession) -> None:
    if not _is_postgres(db):
        return
    await db.execute(
        text("SELECT set_config('app.current_tenant', '', true)")
    )


@asynccontextmanager
async def tenant_scope(db: AsyncSession, tenant_id: uuid.UUID) -> AsyncIterator[None]:
    """Scope every statement in the block to one tenant.

    Wraps :func:`set_tenant_context` with a guaranteed clear, so a failure
    mid-block cannot leave one tenant's context attached to the session for
    whatever runs next on it.
    """
    await set_tenant_context(db, tenant_id)
    try:
        yield
    finally:
        await clear_tenant_context(db)


async def list_tenant_ids(db: AsyncSession) -> list[uuid.UUID]:
    """Every active organization id.

    Deliberately queries ``organizations``, which is not RLS-protected, so
    it works from a session with no tenant context — which is exactly the
    state a worker starts in.
    """
    result = await db.execute(
        select(Organization.id).where(Organization.status == "active")
    )
    return [row[0] for row in result.all()]


async def run_per_tenant(
    body: Callable[[AsyncSession], Awaitable[T]],
    *,
    session_factory: Callable[[], AsyncSession] | None = None,
    min_interval_seconds: float = 0.0,
    raise_on_error: bool = False,
) -> dict[uuid.UUID, T]:
    """Run ``body`` once per active organization, each in its own transaction.

    Background jobs are written as "find every pending X and handle it",
    which is deliberately tenant-agnostic. That works fine without RLS and
    breaks completely with it: the worker's session has no
    ``app.current_tenant``, so every tenant-scoped SELECT matches zero rows
    and the job silently succeeds while doing nothing.

    Rather than push ``organization_id`` parameters through every service,
    this loops the tenants and lets row-level security do the scoping. The
    service body is unchanged — "give me all pending X" simply becomes
    "give me all pending X for this tenant", which is what it should have
    meant all along.

    Each tenant runs in its own session and transaction. A fresh session per
    organization means a failed or rolled-back tenant cannot leave an aborted
    transaction or stale identity-map state behind for the next one, and the
    tenant GUC disappears when the session closes. The cost is one connection
    checkout per organization instead of one for the whole sweep — accepted,
    because cross-tenant correctness matters more than connection churn for a
    background sweep, and ``min_interval_seconds`` bounds how fast that churn
    happens.

    By default a failing tenant is logged and skipped, because aborting the
    sweep would make Celery retry the whole job and re-do the tenants that
    already succeeded. Pass ``raise_on_error=True`` for one-off management
    commands that should fail loudly and stop.
    """
    if session_factory is None:
        from app.core.database import AsyncSessionLocal

        session_factory = AsyncSessionLocal

    results: dict[uuid.UUID, T] = {}

    async with session_factory() as db:
        # No tenant context here: `organizations` is not RLS-protected and
        # this must run before any tenant loop begins.
        tenant_ids = await list_tenant_ids(db)

    last_started = 0.0
    for tenant_id in tenant_ids:
        if min_interval_seconds > 0:
            now = time.monotonic()
            wait = min_interval_seconds - (now - last_started)
            if wait > 0:
                await asyncio.sleep(wait)

        async with session_factory() as db:
            try:
                if _is_postgres(db):
                    # Session-scoped (is_local=false) so the setting persists
                    # across every statement in this tenant's block rather
                    # than being consumed by the next query.
                    await db.execute(
                        text(
                            "SELECT set_config('app.current_tenant', :tid, false)"
                        ),
                        {"tid": str(tenant_id)},
                    )
                results[tenant_id] = await body(db)
                await db.commit()
            except Exception:
                await db.rollback()
                logger.exception(
                    "per-tenant job failed for organization %s; continuing "
                    "with the remaining tenants",
                    tenant_id,
                )
                if raise_on_error:
                    raise
                results[tenant_id] = None  # type: ignore[assignment]
            finally:
                last_started = time.monotonic()
                if _is_postgres(db):
                    await db.execute(
                        text("SELECT set_config('app.current_tenant', '', false)")
                    )
                    await db.commit()

    return results


def merge_counters(results: Mapping[uuid.UUID, object]) -> dict[str, int]:
    """Collapse per-tenant counter dicts into one flat summary.

    Background tasks historically returned a single flat dict (``{"delivered":
    1, "failed": 0}``) and Celery results / dashboards read those keys.
    Fan-out now produces one result per tenant, so tasks that only report
    counters sum them back together here to keep their published contract
    unchanged. Tenants that failed contribute nothing rather than a
    misleading zero-or-one guess.
    """
    merged: dict[str, int] = {}
    for value in results.values():
        if not isinstance(value, dict):
            continue
        for key, item in value.items():
            if isinstance(item, bool):
                merged[key] = merged.get(key, 0) + int(item)
            elif isinstance(item, int):
                merged[key] = merged.get(key, 0) + item
    return merged


def sum_values(results: Mapping[uuid.UUID, object]) -> int:
    """Total the per-tenant integer results of a count-returning job."""
    return sum(v for v in results.values() if isinstance(v, int))