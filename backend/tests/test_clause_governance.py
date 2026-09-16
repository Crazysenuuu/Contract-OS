"""Tests for clause lifecycle governance (spec 24.8)."""

import pytest
from sqlalchemy import select

from app.models.document_intelligence import ClauseLibrary, ClauseCategory
from app.services.clause_governance import (
    ClauseGovernanceError,
    find_inflight_drafts,
    publish_clause_version,
    resolve_inflight_draft,
    set_clause_status,
    _user_can_publish,
)


async def _create_clause(db_session, org_id, actor_id, **kwargs):
    entry = ClauseLibrary(
        organization_id=org_id,
        title=kwargs.get("title", "Standard Indemnification"),
        text=kwargs.get("text", "Party A shall indemnify Party B."),
        category=ClauseCategory.INDEMNIFICATION,
        version=kwargs.get("version", 1),
        is_approved=kwargs.get("is_approved", True),
        lifecycle_status=kwargs.get("lifecycle_status", "active"),
        created_by=actor_id,
        **{k: v for k, v in kwargs.items() if k not in ("title", "text", "version", "is_approved", "lifecycle_status")},
    )
    db_session.add(entry)
    await db_session.flush()
    return entry


@pytest.mark.asyncio
async def test_user_without_permission_cannot_publish(db_session, test_user, test_org):
    entry = await _create_clause(db_session, test_org.id, test_user.id)
    await db_session.flush()

    with pytest.raises(ClauseGovernanceError):
        await publish_clause_version(
            db_session,
            clause_id=entry.id,
            actor_id=test_user.id,
            org_id=test_org.id,
        )


@pytest.mark.asyncio
async def test_admin_can_publish_and_deprecates_old(db_session, test_user, test_org):
    from app.models.user import User

    test_user.is_admin = True
    await db_session.flush()

    entry = await _create_clause(db_session, test_org.id, test_user.id)
    await db_session.flush()

    new_entry = await publish_clause_version(
        db_session,
        clause_id=entry.id,
        actor_id=test_user.id,
        org_id=test_org.id,
        new_text="Party A shall indemnify Party B up to USD 1,000,000.",
    )
    await db_session.flush()

    assert new_entry.version == 2
    assert new_entry.lifecycle_status == "active"

    # Old version is deprecated.
    result = await db_session.execute(
        select(ClauseLibrary).where(ClauseLibrary.id == entry.id)
    )
    old = result.scalar_one()
    assert old.lifecycle_status == "deprecated"


@pytest.mark.asyncio
async def test_publish_requires_permission_via_role(db_session, test_user, test_org):
    # Give the user's role the clause.publish permission.
    from app.models.rbac import OrganizationMember, Role, RolePermission, Permission
    from sqlalchemy import select as sa_select

    member = (
        await db_session.execute(
            sa_select(OrganizationMember).where(
                OrganizationMember.organization_id == test_org.id,
                OrganizationMember.user_id == test_user.id,
            )
        )
    ).scalar_one()

    perm = Permission(key="clause.publish", description="Publish clause versions")
    db_session.add(perm)
    await db_session.flush()

    rp = RolePermission(role_id=member.role_id, permission_id=perm.id)
    db_session.add(rp)
    await db_session.flush()

    can = await _user_can_publish(db_session, user_id=test_user.id, org_id=test_org.id)
    assert can is True


@pytest.mark.asyncio
async def test_set_status_lifecycle(db_session, test_user, test_org):
    test_user.is_admin = True
    await db_session.flush()

    entry = await _create_clause(db_session, test_org.id, test_user.id)
    await db_session.flush()

    updated = await set_clause_status(
        db_session,
        clause_id=entry.id,
        status="archived",
        actor_id=test_user.id,
        org_id=test_org.id,
        reason="Replaced by new template",
    )
    assert updated.lifecycle_status == "archived"

    with pytest.raises(ClauseGovernanceError):
        await set_clause_status(
            db_session,
            clause_id=entry.id,
            status="bogus",
            actor_id=test_user.id,
            org_id=test_org.id,
        )


@pytest.mark.asyncio
async def test_inflight_draft_detection_and_resolution(db_session, test_user, test_org, test_agreement):
    test_user.is_admin = True
    await db_session.flush()

    entry = await _create_clause(db_session, test_org.id, test_user.id)
    await db_session.flush()

    # Draft referencing the clause version.
    test_agreement.data = {"clause_version_ids": [str(entry.id)]}
    await db_session.flush()

    drafts = await find_inflight_drafts(
        db_session,
        org_id=test_org.id,
        clause_id=entry.id,
    )
    assert len(drafts) == 1
    assert drafts[0]["agreement_id"] == str(test_agreement.id)

    result = await resolve_inflight_draft(
        db_session,
        agreement_id=test_agreement.id,
        clause_id=entry.id,
        resolution="keep_legacy",
        actor_id=test_user.id,
    )
    assert result["resolution"] == "keep_legacy"

    # After resolution, data records it.
    await db_session.refresh(test_agreement)
    assert test_agreement.data["clause_resolutions"][0]["clause_id"] == str(entry.id)