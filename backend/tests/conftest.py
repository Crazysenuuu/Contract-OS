"""Test database fixtures for ContractOS integration tests.

Patches PostgreSQL-specific types to use SQLite-compatible types for testing.
"""
import asyncio
import os
import uuid as _uuid_mod

# Run Celery tasks synchronously and inline in tests (no Redis/broker needed).
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"

# ===== CRITICAL: Patch PostgreSQL types BEFORE any model imports =====

from sqlalchemy import JSON, String, TypeDecorator
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB, UUID


class _SQLiteJSONB(JSON):
    """JSONB stand-in for SQLite."""
    __visit_name__ = "json"

    def __init__(self, *args, **kwargs):
        kwargs.pop("none_as_null", None)
        super().__init__(*args, **kwargs)


class _SQLiteUUID(TypeDecorator):
    """UUID type that uses VARCHAR(36) for SQLite, with proper bind/result processing."""
    impl = String(36)
    cache_ok = True

    def __init__(self, as_uuid=True, *args, **kwargs):
        self.as_uuid = as_uuid
        super().__init__(*args, **kwargs)

    def process_bind_param(self, value, dialect):
        if value is not None:
            if isinstance(value, str):
                return value
            if isinstance(value, _uuid_mod.UUID):
                return str(value)
            return str(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and self.as_uuid:
            return _uuid_mod.UUID(value) if not isinstance(value, _uuid_mod.UUID) else value
        return value


# Register type compilations so SQLite DDL compiler can render them
@compiles(_SQLiteJSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@compiles(_SQLiteJSONB, "postgresql")
def _compile_jsonb_pg(type_, compiler, **kw):
    return "JSONB"


@compiles(_SQLiteUUID, "sqlite")
def _compile_uuid_sqlite(type_, compiler, **kw):
    return "VARCHAR(36)"


@compiles(_SQLiteUUID, "postgresql")
def _compile_uuid_pg(type_, compiler, **kw):
    return "UUID"


# Monkey-patch the postgresql dialect module
import sqlalchemy.dialects.postgresql as pg_dialect
pg_dialect.JSONB = _SQLiteJSONB
pg_dialect.UUID = _SQLiteUUID


# ===== Now safe to import everything else =====
import pytest
import pytest_asyncio
from datetime import datetime

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.base import Base

# Import ALL models so Base.metadata registers them
from app.models import *  # noqa

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def engine():
    """Create a test database engine with all tables."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(engine):
    """Create a test database session."""
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(scope="function")
async def client(engine):
    """Create a test HTTP client with database override."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()


# ===== Data Fixtures =====

@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession):
    """Create a test user in the database."""
    from app.models.user import User

    user = User(
        email="test@example.com",
        name="Test User",
        password_hash=hash_password("TestPass123!"),
        status="active",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture(autouse=True)
async def seed_lifecycle(db_session: AsyncSession):
    """Seed agreement states and status transition rules for every test.

    Mirrors the data-driven lifecycle from seed_data so API endpoints that
    enforce transitions work under the test suite.
    """
    from sqlalchemy import select
    from app.models.lifecycle import AgreementState, StatusTransitionRule

    from seed_data import AGREEMENT_STATES, TRANSITION_RULES

    existing = await db_session.execute(select(AgreementState.status))
    state_keys = {row[0] for row in existing.all()}
    for s in AGREEMENT_STATES:
        if s["status"] in state_keys:
            continue
        db_session.add(AgreementState(
            status=s["status"],
            label=s["label"],
            is_terminal=s["is_terminal"],
            description=s["description"],
            is_system=True,
        ))

    existing_rules = await db_session.execute(
        select(StatusTransitionRule.action_key, StatusTransitionRule.from_status)
    )
    have = {(r[0], r[1]) for r in existing_rules.all()}
    for r in TRANSITION_RULES:
        if (r["action_key"], r["from_status"]) in have:
            continue
        db_session.add(StatusTransitionRule(
            action_key=r["action_key"],
            from_status=r["from_status"],
            to_status=r["to_status"],
            description=r["description"],
            required_permission=r.get("required_permission"),
            allowed_conditions=r.get("conditions"),
            is_system=True,
        ))
    await db_session.flush()
    yield


@pytest_asyncio.fixture
async def test_org(db_session: AsyncSession, test_user):
    """Create a test organization with the user as owner."""
    from app.models.organization import Organization
    from app.models.rbac import Role, OrganizationMember

    org = Organization(
        name="Test Corp",
        slug="test-corp",
        country="US",
        timezone="America/New_York",
    )
    db_session.add(org)
    await db_session.flush()

    role = Role(
        organization_id=org.id,
        name="owner",
    )
    db_session.add(role)
    await db_session.flush()

    member = OrganizationMember(
        organization_id=org.id,
        user_id=test_user.id,
        role_id=role.id,
        status="active",
    )
    db_session.add(member)
    await db_session.commit()
    await db_session.refresh(org)
    return org


@pytest_asyncio.fixture
async def auth_headers(test_user):
    """Get JWT auth headers for the test user."""
    token = create_access_token(user_id=test_user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def test_agreement_type(db_session: AsyncSession):
    """Create a test agreement type."""
    from app.models.agreement_type import AgreementType

    atype = AgreementType(
        key="mutual_nda",
        name="Mutual NDA",
        description="A mutual non-disclosure agreement",
        category="confidentiality",
        schema={
            "questions": [
                {"key": "effective_date", "type": "date", "label": "Effective Date"},
            ],
            "clauses": [],
        },
    )
    db_session.add(atype)
    await db_session.commit()
    await db_session.refresh(atype)
    return atype


@pytest_asyncio.fixture
async def test_agreement(
    db_session: AsyncSession, test_user, test_org, test_agreement_type
):
    """Create a test agreement in the database."""
    from app.models.agreement import Agreement

    agreement = Agreement(
        organization_id=test_org.id,
        agreement_type_id=test_agreement_type.id,
        title="Test NDA Agreement",
        status="draft",
        created_by=test_user.id,
        data={},
    )
    db_session.add(agreement)
    await db_session.commit()
    await db_session.refresh(agreement)
    return agreement


@pytest_asyncio.fixture
async def test_legal_entity(db_session: AsyncSession, test_org):
    """Create a test legal entity in the database."""
    from app.models.legal_entity import LegalEntity

    entity = LegalEntity(
        organization_id=test_org.id,
        legal_name="Test Corp Ltd",
        country="US",
        registration_number="12345678",
        entity_type="corporation",
    )
    db_session.add(entity)
    await db_session.commit()
    await db_session.refresh(entity)
    return entity
