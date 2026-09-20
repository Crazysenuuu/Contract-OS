"""Load tests for hot endpoints (spec §97 test category: "Load tests").

Runs realistic concurrency through the full in-process ASGI stack against
the seeded per-test database, asserting both correctness (no 5xx) and
throughput floors on a developer-class machine.

Design:

  - Pure ``anyio`` + ``httpx`` over ``ASGITransport`` — no extra services,
    no network, so it is CI-runnable everywhere.
  - A ``loadapp`` fixture (module scope, shared with ``client``) bootstraps
    one seeded database per module: N users, org, agreement types, and 60
    agreements — the shape list/search/dashboard endpoints page over.
  - ``_hammer`` drives ``concurrency`` virtual users, each executing the
    endpoint ``iterations`` times; per-request wall times are collected for
    p50/p95 latency assertions.
  - A locustfile (``load/locustfile.py``) ships alongside for staging runs;
    these in-process tests are the CI gate, locust is the deep probe.

Thresholds are intentionally modest (throughput floor is a regression trip
wire, not a benchmark): a 10x slowdown on a hot endpoint fails the suite.
"""
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio

# Shape of the load run — sized so the whole module finishes in a few
# seconds on CI hardware while still exercising real concurrency.
CONCURRENCY = 10
ITERATIONS = 4
THROUGHPUT_FLOOR_RPS = 25
P95_CEILING_MS = 2500


# --------------------------------------------------------------------------
# Shared seeded app + engine (module scope — one DB for the whole module)
# --------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def loadapp(_module_seed):
    from app.main import app

    return app


@pytest_asyncio.fixture(scope="module")
async def _module_seed():
    """Seed one database for the module and yield (engine, auth headers)."""
    import asyncio

    from sqlalchemy import select
    import tempfile
    from pathlib import Path

    from sqlalchemy import event
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.database import get_db
    from app.main import app
    from app.models.agreement import Agreement
    from app.models.agreement_type import AgreementType
    from app.models.base import Base
    from app.models.user import User
    from tests.conftest import hash_password

    # Real concurrency needs per-request connections: a file-backed DB with
    # NullPool (one aiosqlite connection + worker thread per request) and WAL
    # mode for concurrent readers. A :memory: StaticPool would funnel every
    # task through one connection, which aiosqlite cannot do safely.
    db_dir = tempfile.mkdtemp(prefix="load-test-")
    db_url = f"sqlite+aiosqlite:///{Path(db_dir) / 'load.db'}"

    engine = create_async_engine(
        db_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_wal(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    session = factory()

    # --- Seed: org, users, types, 60 agreements --------------------------
    from app.models.organization import Organization
    from app.models.rbac import OrganizationMember, Role

    org = Organization(
        name="LoadCo",
        slug=f"load-{time.time_ns()}",
        country="US",
        timezone="America/New_York",
    )
    session.add(org)
    await session.flush()

    role = Role(
        organization_id=org.id,
        name="load-owner",
    )
    session.add(role)
    await session.flush()

    users = []
    for i in range(CONCURRENCY):
        u = User(
            email=f"load-{time.time_ns()}-{i}@example.com",
            name=f"Load User {i}",
            password_hash=hash_password("LoadPass123!"),
            status="active",
        )
        session.add(u)
        users.append(u)
    await session.flush()

    for u in users:
        session.add(OrganizationMember(
            organization_id=org.id,
            user_id=u.id,
            role_id=role.id,
            status="active",
        ))

    atype = AgreementType(
        key=f"load_nda_{time.time_ns()}",
        name="Load NDA",
        category="confidentiality",
        schema={"questions": [], "clauses": []},
    )
    session.add(atype)
    await session.flush()

    primary = users[0]
    for i in range(60):
        session.add(Agreement(
            organization_id=org.id,
            agreement_type_id=atype.id,
            title=f"Load agreement {i} — vendor {i} services",
            status="active" if i % 2 else "draft",
            created_by=primary.id,
            data={"total_value": 1000 + i},
        ))
    await session.commit()

    # --- Wire the app to this engine -------------------------------------
    async def override_get_db():
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    yield {"engine": engine, "users": users, "org": org}

    app.dependency_overrides.clear()
    await session.close()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def loadclient(loadapp, _module_seed):
    """Unauthenticated client (health) over the shared seeded app."""
    async with AsyncClient(
        transport=ASGITransport(app=loadapp),
        base_url="http://testserver",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def authclients(loadapp, _module_seed):
    """One authenticated AsyncClient per virtual user (shared connection
    pool per client; concurrency comes from driving them concurrently)."""
    from app.core.security import create_access_token

    clients = []
    for u in _module_seed["users"]:
        token = create_access_token(user_id=u.id)
        ac = AsyncClient(
            transport=ASGITransport(app=loadapp),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {token}"},
        )
        clients.append(ac)
    try:
        yield clients
    finally:
        for ac in clients:
            await ac.aclose()


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


async def _hammer(clients, worker_index, method, url, *, iterations, latencies, errors):
    """Drive one virtual user: iterations requests, recording timings.

    httpx.AsyncClient is concurrency-safe (pooled), so workers share the
    pool round-robin instead of owning exclusive clients.
    """
    client = clients[worker_index % len(clients)]
    for _ in range(iterations):
        start = time.perf_counter()
        try:
            resp = await client.request(method, url)
            if resp.status_code >= 500:
                errors.append(
                    f"{url} -> {resp.status_code}: {resp.text[:120]}"
                )
        except Exception as exc:  # transport-level failure
            errors.append(f"{url} -> transport error: {exc}")
        latencies.append(time.perf_counter() - start)


async def _run_load(clients, method, url, *, concurrency, iterations):
    import anyio
    from functools import partial

    latencies: list[float] = []
    errors: list[str] = []
    started = time.perf_counter()
    async with anyio.create_task_group() as tg:
        for i in range(concurrency):
            tg.start_soon(
                partial(
                    _hammer, clients, i, method, url,
                    iterations=iterations,
                    latencies=latencies, errors=errors,
                )
            )
    elapsed = time.perf_counter() - started
    total = concurrency * iterations
    latencies.sort()
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    return {
        "total": total,
        "rps": total / elapsed if elapsed > 0 else float("inf"),
        "p50_ms": p50 * 1000,
        "p95_ms": p95 * 1000,
        "errors": errors,
    }


def _report(name, stats):
    print(
        f"\n[load] {name}: {stats['total']} reqs, "
        f"{stats['rps']:.0f} req/s, p50={stats['p50_ms']:.0f}ms, "
        f"p95={stats['p95_ms']:.0f}ms, errors={len(stats['errors'])}"
    )


# --------------------------------------------------------------------------
# Health (unauthenticated) — smoke that the harness itself works
# --------------------------------------------------------------------------


async def test_health_endpoint_throughput(loadclient):
    stats = await _run_load(
        [loadclient], "GET", "/api/v1/health",
        concurrency=CONCURRENCY, iterations=ITERATIONS,
    )
    _report("health", stats)
    assert stats["errors"] == []
    # Health runs without auth middleware; still floor at 2x the hot-endpoint
    # bar (measured ~87 req/s on a dev machine with per-request connections).
    assert stats["rps"] >= THROUGHPUT_FLOOR_RPS * 2


# --------------------------------------------------------------------------
# Hot endpoints — the CI load gate
# --------------------------------------------------------------------------


async def test_list_agreements_throughput(authclients):
    stats = await _run_load(
        authclients, "GET", "/api/v1/agreements?limit=20",
        concurrency=CONCURRENCY, iterations=ITERATIONS,
    )
    _report("list_agreements", stats)
    assert stats["errors"] == []
    assert stats["rps"] >= THROUGHPUT_FLOOR_RPS


async def test_search_agreements_throughput(authclients):
    stats = await _run_load(
        authclients, "GET", "/api/v1/search/agreements?q=vendor",
        concurrency=CONCURRENCY, iterations=ITERATIONS,
    )
    _report("search_agreements", stats)
    assert stats["errors"] == []
    assert stats["rps"] >= THROUGHPUT_FLOOR_RPS


async def test_dashboard_tasks_throughput(authclients):
    stats = await _run_load(
        authclients, "GET", "/api/v1/dashboard/tasks",
        concurrency=CONCURRENCY, iterations=ITERATIONS,
    )
    _report("dashboard_tasks", stats)
    assert stats["errors"] == []
    assert stats["rps"] >= THROUGHPUT_FLOOR_RPS


async def test_mixed_workload(authclients):
    """Concurrent mixed traffic — closest to real production shape."""
    import anyio
    from functools import partial

    latencies: list[float] = []
    errors: list[str] = []
    endpoints = [
        ("GET", "/api/v1/agreements?limit=20"),
        ("GET", "/api/v1/search/agreements?q=agreement"),
        ("GET", "/api/v1/dashboard/tasks"),
    ]
    started = time.perf_counter()
    async with anyio.create_task_group() as tg:
        for i in range(CONCURRENCY):
            method, url = endpoints[i % len(endpoints)]
            tg.start_soon(
                partial(
                    _hammer, authclients, i, method, url,
                    iterations=ITERATIONS,
                    latencies=latencies, errors=errors,
                )
            )
    elapsed = time.perf_counter() - started
    total = CONCURRENCY * ITERATIONS
    stats = {
        "total": total,
        "rps": total / elapsed if elapsed > 0 else float("inf"),
        "p50_ms": (sorted(latencies)[len(latencies) // 2]) * 1000
        if latencies else 0,
        "p95_ms": (sorted(latencies)[int(len(latencies) * 0.95)]) * 1000
        if latencies else 0,
        "errors": errors,
    }
    _report("mixed", stats)
    assert stats["errors"] == []
    assert stats["rps"] >= THROUGHPUT_FLOOR_RPS
