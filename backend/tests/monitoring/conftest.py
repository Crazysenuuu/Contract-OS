"""Fixtures for the monitoring test suite.

Builds the org → agreement → AgreementVersion → Obligation → Integration →
IntegrationCredential → ObligationMonitoring chain shared by pipeline and
API tests. The REST integration's bearer token is bound to
``env://MONITORING_TEST_TOKEN``. Fixtures commit so the API ``client``
(independent session over the shared StaticPool connection) sees them.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy import select

from app.models.agreement import AgreementVersion
from app.models.obligation import Obligation
from app.monitoring.models import (
    IntegrationConnection,
    IntegrationCredential,
    ObligationMonitoring,
)

TEST_TOKEN_ENV = "MONITORING_TEST_TOKEN"


@pytest_asyncio.fixture
async def agreement_version(db_session, test_agreement, test_user):
    version = AgreementVersion(
        agreement_id=test_agreement.id,
        version_number=1,
        content="## Confidentiality\nNDA provisions.",
        content_hash="test-hash-v1",
        status="ACTIVE",
        created_by=test_user.id,
        data={},
    )
    db_session.add(version)
    await db_session.commit()
    await db_session.refresh(version)
    return version


@pytest_asyncio.fixture
async def monitoring_obligation(db_session, test_org, test_agreement, test_user):
    obligation = Obligation(
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        title="Submit monthly performance claims",
        owner_party="Vendor Ltd",
        description="Submit verified claim volume by the 5th business day.",
        obligation_type="reporting",
        status="OPEN",
        criticality="HIGH",
        evidence_status="REQUIRED",
    )
    db_session.add(obligation)
    await db_session.commit()
    await db_session.refresh(obligation)
    return obligation


@pytest_asyncio.fixture
async def monitoring_integration(db_session, test_org, test_user):
    integration = IntegrationConnection(
        organization_id=test_org.id,
        name="Vendor claims API",
        integration_type="REST_API",
        provider_key="rest_api",
        status="ACTIVE",
        configuration={
            "base_url": "https://vendor.example.internal",
            "id_field": "id",
            "observed_at_field": "observed_at",
        },
        created_by=test_user.id,
        created_by_name=test_user.name,
    )
    db_session.add(integration)
    await db_session.flush()

    db_session.add(
        IntegrationCredential(
            integration_id=integration.id,
            secret_reference=f"env://{TEST_TOKEN_ENV}",
            status="ACTIVE",
        )
    )
    await db_session.commit()
    await db_session.refresh(integration)
    return integration


@pytest_asyncio.fixture
async def active_monitoring(
    db_session,
    test_org,
    monitoring_obligation,
    agreement_version,
    monitoring_integration,
    test_user,
):
    rule = ObligationMonitoring(
        organization_id=test_org.id,
        obligation_id=monitoring_obligation.id,
        source_version_id=agreement_version.id,
        integration_id=monitoring_integration.id,
        status="ACTIVE",
        query_definition={
            "resource": "claims",
            "fields": ["id", "amount", "observed_at"],
        },
        evaluation_definition={"kind": "existence", "expected": True},
        schedule_definition={"recurrence": "interval", "minutes": 60},
        automation={},
        created_by=test_user.id,
    )
    db_session.add(rule)
    await db_session.commit()
    await db_session.refresh(rule)
    return rule


@pytest_asyncio.fixture
async def get_second_org_user(db_session):
    """Factory building a distinct org + user + role.

    Returns org/user/role/auth_headers. ``permissions`` (list of keys) are
    granted to the role via Permission + RolePermission rows. Roles default
    to an empty-permission analyst role (only the platform roles ``owner`` /
    ``admin`` are special-cased as all-granting).
    """

    async def factory(*, role_name: str | None = "analyst", permissions: list[str] | None = None):
        from app.core.security import create_access_token, hash_password
        from app.models.organization import Organization
        from app.models.rbac import OrganizationMember, Permission, Role, RolePermission
        from app.models.user import User

        org2 = Organization(
            name=f"Other Corp {uuid.uuid4().hex[:6]}",
            slug=f"other-{uuid.uuid4().hex[:6]}",
            country="US",
            timezone="America/New_York",
        )
        db_session.add(org2)
        await db_session.flush()

        user2 = User(
            email=f"{uuid.uuid4().hex[:8]}@example.com",
            name="Other User",
            password_hash=hash_password("TestPass123!"),
            status="active",
        )
        db_session.add(user2)
        await db_session.flush()

        role = Role(organization_id=org2.id, name=role_name or "member")
        db_session.add(role)
        await db_session.flush()

        for key in permissions or []:
            permission = (
                await db_session.execute(
                    select(Permission).where(Permission.key == key)
                )
            ).scalar_one_or_none()
            if permission is None:
                permission = Permission(key=key, description=key)
                db_session.add(permission)
                await db_session.flush()
            db_session.add(
                RolePermission(role_id=role.id, permission_id=permission.id)
            )

        db_session.add(
            OrganizationMember(
                organization_id=org2.id,
                user_id=user2.id,
                role_id=role.id,
                status="active",
            )
        )
        await db_session.commit()
        return {
            "org": org2,
            "user": user2,
            "role": role,
            "auth_headers": {"Authorization": f"Bearer {create_access_token(user_id=user2.id)}"},
        }

    return factory


@pytest_asyncio.fixture(autouse=True)
async def set_test_token_env(monkeypatch):
    """Bind the fixture credential + webhook secret env vars for the test."""
    monkeypatch.setenv(TEST_TOKEN_ENV, "test-bearer-token-123")
    monkeypatch.setenv("MONITORING_WEBHOOK_SECRET", "test-webhook-secret-456")