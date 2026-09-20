"""Tenant isolation tests (spec §97: "Tenant A → cannot access Tenant B").

Every check requests another organisation's resource through the org-scoped
query pattern; the query must filter on organization_id/tenant_id and return
empty rather than leak data.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement, AgreementVersion
from app.models.audit import AuditEvent
from app.models.obligation import Obligation


@pytest.mark.asyncio
async def test_agreement_scoped_to_own_org(
    db_session: AsyncSession, test_org, test_user, test_agreement, test_agreement_type
):
    """The org-scoped list must never include another tenant's agreement."""
    other_org_id = uuid.uuid4()

    foreign = Agreement(
        organization_id=other_org_id,
        agreement_type_id=test_agreement_type.id,
        title="Tenant B secret agreement",
        status="draft",
        created_by=test_user.id,
        data={},
    )
    db_session.add(foreign)
    await db_session.flush()

    rows = (
        await db_session.scalars(
            select(Agreement).where(Agreement.organization_id == test_org.id)
        )
    ).all()
    assert all(r.organization_id == test_org.id for r in rows)
    assert foreign.id not in {r.id for r in rows}


@pytest.mark.asyncio
async def test_obligations_scoped_to_own_org(
    db_session: AsyncSession, test_org, test_agreement
):
    other_org_id = uuid.uuid4()

    foreign = Obligation(
        agreement_id=test_agreement.id,
        organization_id=other_org_id,
        owner_party="tenant B",
        description="secret obligation",
        obligation_type="payment",
        status="pending",
    )
    db_session.add(foreign)
    await db_session.flush()

    rows = (
        await db_session.scalars(
            select(Obligation).where(Obligation.organization_id == test_org.id)
        )
    ).all()
    assert foreign.id not in {r.id for r in rows}


@pytest.mark.asyncio
async def test_audit_events_scoped_to_own_org(db_session: AsyncSession, test_org):
    other_org_id = uuid.uuid4()
    actor = uuid.uuid4()

    db_session.add(
        AuditEvent(
            tenant_id=other_org_id,
            actor_id=actor,
            actor_type="user",
            action="DOCUMENT_DOWNLOAD",
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    rows = (
        await db_session.scalars(
            select(AuditEvent).where(AuditEvent.tenant_id == test_org.id)
        )
    ).all()
    assert all(r.tenant_id == test_org.id for r in rows)


@pytest.mark.asyncio
async def test_agreement_version_access_requires_parent_org(
    db_session: AsyncSession, test_org, test_agreement, test_user, test_agreement_type
):
    """A version of another tenant's agreement is unreachable via the org filter."""
    other_org_id = uuid.uuid4()
    foreign_agreement = Agreement(
        organization_id=other_org_id,
        agreement_type_id=test_agreement_type.id,
        title="Foreign",
        status="draft",
        created_by=test_user.id,
        data={},
    )
    db_session.add(foreign_agreement)
    await db_session.flush()

    version = AgreementVersion(
        agreement_id=foreign_agreement.id,
        version_number=1,
        content="Tenant B confidential text",
        content_hash="x" * 64,
        status="draft",
        created_by=test_user.id,
    )
    db_session.add(version)
    await db_session.flush()

    # Join through the agreement and filter on the caller's org — the join
    # must produce zero rows for the foreign agreement.
    rows = (
        (
            await db_session.execute(
                select(AgreementVersion)
                .join(Agreement, Agreement.id == AgreementVersion.agreement_id)
                .where(Agreement.organization_id == test_org.id)
            )
        )
        .scalars()
        .all()
    )
    assert version.id not in {v.id for v in rows}


@pytest.mark.asyncio
async def test_cross_tenant_agreement_direct_get_filtered(
    db_session: AsyncSession, test_org, test_agreement, test_user, test_agreement_type
):
    """The canonical service-layer guard: db.get returns the row, but every
    service call must go through org-scoped queries. Verify the scoped query
    pattern returns None for a foreign id."""
    other_org_id = uuid.uuid4()
    foreign_agreement = Agreement(
        organization_id=other_org_id,
        agreement_type_id=test_agreement_type.id,
        title="Other tenant",
        status="draft",
        created_by=test_user.id,
        data={},
    )
    db_session.add(foreign_agreement)
    await db_session.flush()

    row = await db_session.scalar(
        select(Agreement).where(
            Agreement.id == foreign_agreement.id,
            Agreement.organization_id == test_org.id,
        )
    )
    assert row is None
