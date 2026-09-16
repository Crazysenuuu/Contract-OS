"""SSO/SCIM provisioning tests (spec 1.21.8-1.21.9)."""

import pytest
import pytest_asyncio

from app.services import scim_service


@pytest_asyncio.fixture
async def scim_token(db_session, test_org):
    raw = await scim_service.issue_scim_token(
        db_session, organization_id=test_org.id, name="okta"
    )
    await db_session.commit()
    return raw


@pytest.mark.asyncio
async def test_token_authenticates_to_org(db_session, test_org, scim_token):
    org_id = await scim_service.authenticate_scim_token(
        db_session, authorization_header=f"Bearer {scim_token}"
    )
    assert org_id == test_org.id


@pytest.mark.asyncio
async def test_invalid_token_rejected(db_session):
    from app.core.exceptions import UnauthenticatedError

    with pytest.raises(UnauthenticatedError):
        await scim_service.authenticate_scim_token(
            db_session, authorization_header="Bearer bogus"
        )
    with pytest.raises(UnauthenticatedError):
        await scim_service.authenticate_scim_token(db_session, authorization_header=None)


@pytest.mark.asyncio
async def test_provision_creates_user_and_membership(db_session, test_org, scim_token):
    result = await scim_service.provision_user(
        db_session,
        organization_id=test_org.id,
        user_name="jane@corp.example.com",
        email="jane@corp.example.com",
        given_name="Jane",
        family_name="Doe",
    )
    await db_session.commit()
    assert result["id"]

    from sqlalchemy import select
    from app.models.user import User

    user = (
        await db_session.execute(select(User).where(User.email == "jane@corp.example.com"))
    ).scalar_one()
    assert user.status == "active"


@pytest.mark.asyncio
async def test_deactivation_preserves_user(db_session, test_org, scim_token):
    """SCIM DELETE deactivates but never deletes legal data (1.21.37)."""
    created = await scim_service.provision_user(
        db_session,
        organization_id=test_org.id,
        user_name="bob@corp.example.com",
        email="bob@corp.example.com",
    )
    await db_session.commit()

    from uuid import UUID

    from sqlalchemy import select
    from app.models.user import User

    result = await scim_service.deactivate_user(
        db_session, organization_id=test_org.id, user_id=UUID(created["id"])
    )
    await db_session.commit()
    assert result["active"] is False

    user = (
        await db_session.execute(select(User).where(User.id == UUID(created["id"])))
    ).scalar_one()
    assert user is not None, "user must not be deleted"
    assert user.status == "deactivated"


@pytest.mark.asyncio
async def test_provision_requires_valid_email(db_session, test_org, scim_token):
    from app.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        await scim_service.provision_user(
            db_session,
            organization_id=test_org.id,
            user_name="not-an-email",
            email="not-an-email",
        )


@pytest.mark.asyncio
async def test_idp_event_recorded(db_session, test_org, scim_token):
    from sqlalchemy import select
    from app.models.sso import IdentityProviderEvent

    await scim_service.provision_user(
        db_session,
        organization_id=test_org.id,
        user_name="eve@corp.example.com",
        email="eve@corp.example.com",
    )
    await db_session.commit()
    events = (
        await db_session.execute(
            select(IdentityProviderEvent).where(
                IdentityProviderEvent.organization_id == test_org.id,
                IdentityProviderEvent.event_type == "scim_user_created",
            )
        )
    ).scalars().all()
    assert events
