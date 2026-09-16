"""Tenant context for PostgreSQL Row-Level Security (spec 1.22).

RLS policies on tenant-scoped tables read ``current_setting('app.current_tenant')``
to decide row visibility. Every request that resolves the caller's
organization sets this context (see ``get_current_organization_id``), so
queries in the same transaction are automatically tenant-scoped by the
database — defense in depth on top of application-level scoping.

SQLite (tests) has no such mechanism; the helper is a no-op there.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_tenant_context(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> None:
    """Set ``app.current_tenant`` for the current transaction (Postgres only)."""
    dialect = getattr(db.bind, "dialect", None)
    if dialect is None or dialect.name != "postgresql":
        return
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )


async def clear_tenant_context(db: AsyncSession) -> None:
    dialect = getattr(db.bind, "dialect", None)
    if dialect is None or dialect.name != "postgresql":
        return
    await db.execute(
        text("SELECT set_config('app.current_tenant', '', true)")
    )