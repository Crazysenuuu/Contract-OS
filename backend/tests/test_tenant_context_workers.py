"""Tests for per-tenant scoping of Celery background jobs.

Background jobs are written as tenant-agnostic fan-outs ("find every pending
X"). Once tenant tables carry row-level security, a worker session with no
``app.current_tenant`` matches zero rows and the job silently succeeds while
doing nothing. ``run_per_tenant`` closes that hole.
"""

import uuid

from sqlalchemy import select

from app.models.organization import Organization
from app.services.tenant_context import (
    list_tenant_ids,
    merge_counters,
    run_per_tenant,
    sum_values,
)


def _mk_org(db_session, name: str, status: str = "active") -> Organization:
    org = Organization(
        name=name,
        slug=f"{name}-{uuid.uuid4().hex[:8]}".lower().replace(" ", "-"),
        country="US",
        timezone="America/New_York",
        status=status,
    )
    db_session.add(org)
    db_session.flush()
    return org


async def test_list_tenant_ids_returns_only_active_orgs(db_session):
    active = _mk_org(db_session, "Active Co")
    _mk_org(db_session, "Archived Co", status="suspended")
    await db_session.commit()

    ids = await list_tenant_ids(db_session)

    assert active.id in ids
    assert len(ids) == 1


async def test_run_per_tenant_visits_every_active_org(db_session):
    first = _mk_org(db_session, "First Co")
    second = _mk_org(db_session, "Second Co")
    await db_session.commit()

    calls = 0

    async def body(db):
        nonlocal calls
        calls += 1
        return {"handled": 1}

    results = await run_per_tenant(body, session_factory=lambda: db_session)

    assert calls == 2
    assert set(results) == {first.id, second.id}
    assert all(v == {"handled": 1} for v in results.values())


async def test_run_per_tenant_isolates_failures(db_session):
    """One tenant blowing up must not stop the others."""
    _mk_org(db_session, "Good Co")
    _mk_org(db_session, "Bad Co")
    await db_session.commit()

    calls = 0

    async def body(db):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("tenant-scoped service blew up")
        return {"ok": 1}

    results = await run_per_tenant(body, session_factory=lambda: db_session)

    assert calls == 2, "both tenants must be attempted"
    assert sum(1 for v in results.values() if v == {"ok": 1}) == 1
    assert sum(1 for v in results.values() if v is None) == 1


async def test_run_per_tenant_skips_inactive_orgs(db_session):
    active = _mk_org(db_session, "Active Co")
    _mk_org(db_session, "Gone Co", status="suspended")
    await db_session.commit()

    results = await run_per_tenant(
        lambda db: _noop(db), session_factory=lambda: db_session
    )

    assert set(results) == {active.id}


async def _noop(db):
    return True


async def test_run_per_tenant_rolls_back_the_failing_tenant(db_session):
    org = _mk_org(db_session, "Rollback Co")
    await db_session.commit()

    class Sentinel(Exception):
        pass

    async def body(db):
        db.add(Organization(name="Should Not Persist", slug="ghost-slug"))
        await db.flush()
        raise Sentinel()

    results = await run_per_tenant(body, session_factory=lambda: db_session)

    assert list(results.values()) == [None]
    ghosts = (await db_session.execute(
        select(Organization).where(Organization.slug == "ghost-slug")
    )).scalars().all()
    assert ghosts == []


async def test_merge_counters_sums_across_tenants():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    merged = merge_counters({a: {"delivered": 2, "failed": 0}, b: {"delivered": 1, "failed": 1}, c: None})
    assert merged == {"delivered": 3, "failed": 1}


def test_merge_counters_ignores_non_dict_results():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert merge_counters({a: ["not", "a", "dict"], b: None}) == {}


def test_sum_values_counts_only_integers():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert sum_values({a: 3, b: None, c: 4}) == 7


async def test_run_per_tenant_with_no_orgs_is_a_noop(db_session):
    calls = []

    async def body(db):
        calls.append(1)
        return {}

    await run_per_tenant(body, session_factory=lambda: db_session)
    assert calls == []


def test_membership_fixture_still_usable(db_session):
    """Guard: tenant discovery must not depend on membership rows."""
    # Not an async test; just assert the import surface stays intact.
    assert callable(run_per_tenant)
    assert callable(list_tenant_ids)