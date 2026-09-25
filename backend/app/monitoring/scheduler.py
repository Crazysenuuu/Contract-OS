"""Scheduler orchestration (spec 3.15.31-3.15.33).

``run_sweep`` is the entry point for the Celery beat task and for manual /
test invocation: it opens one database session, runs every due monitoring,
detects stale-version rules, and commits once. All heavy lifting lives in
``app.monitoring.service``.
"""

from __future__ import annotations

import logging

from app.core.database import AsyncSessionLocal
from app.monitoring.service import (
    detect_expiring_credentials,
    detect_stale_monitorings,
    purge_expired_observations,
    schedule_due_monitorings,
)

_log = logging.getLogger(__name__)


async def _run_sweep(db, limit: int) -> dict:
    due = await schedule_due_monitorings(db, limit=limit)
    stale = await detect_stale_monitorings(db)
    expiring = await detect_expiring_credentials(db)
    purged = await purge_expired_observations(db)
    await db.commit()
    return {
        "evaluation_ids_due": [str(rid) for rid in due],
        "evaluations_run": len(due),
        "stale_paused": stale,
        "credentials_expiring": expiring,
        **purged,
    }


def run_sweep(limit: int = 50) -> dict:
    """Synchronous entry point: open a session, sweep, commit, rollback on
    error. Safe to run from Celery, a management script or a shell."""
    import asyncio

    async def _go():
        async with AsyncSessionLocal() as db:
            try:
                return await _run_sweep(db, limit)
            except Exception:
                await db.rollback()
                raise

    return _asyncio_run(_go)


def detect_stale() -> dict:
    import asyncio

    async def _go():
        async with AsyncSessionLocal() as db:
            try:
                paused = await detect_stale_monitorings(db)
                await db.commit()
                return {"paused": paused}
            except Exception:
                await db.rollback()
                raise

    return _asyncio_run(_go)


def _asyncio_run(coro_factory):
    """Run a coroutine from a synchronous caller.

    Uses ``asyncio.run`` when no loop is running (normal worker execution).
    If a loop is already running (pytest eager mode, nested call from an
    async context), the coroutine runs in a fresh thread with its own loop so
    the caller is never blocked — identical to app/tasks/scheduler.py.
    """
    import asyncio
    import threading

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    box: dict = {}

    def _target():
        try:
            box["result"] = asyncio.run(coro_factory())
        except BaseException as exc:  # noqa: BLE001 - re-raised in caller thread
            box["error"] = exc

    thread = threading.Thread(target=_target)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["result"]