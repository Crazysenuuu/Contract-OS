"""Tests for the contract repository search (spec 2.13) + unread badge."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.agreement_access import AgreementParty
from app.models.legal_entity import LegalEntity
from app.models.notification import Notification


async def _create_agreement(
    db_session: AsyncSession,
    org,
    atype,
    user,
    title: str,
    status: str = "draft",
    party_names: tuple[str, ...] = (),
    effective_date=None,
    expiry_date=None,
):
    agreement = Agreement(
        organization_id=org.id,
        agreement_type_id=atype.id,
        title=title,
        status=status,
        created_by=user.id,
        data={},
        effective_date=effective_date,
        expiry_date=expiry_date,
    )
    db_session.add(agreement)
    await db_session.flush()
    for name in party_names:
        entity = LegalEntity(
            organization_id=org.id,
            legal_name=name,
            country="LK",
        )
        db_session.add(entity)
        await db_session.flush()
        db_session.add(
            AgreementParty(
                agreement_id=agreement.id,
                legal_entity_id=entity.id,
                party_role="counterparty",
                display_name=name,
            )
        )
    await db_session.commit()
    await db_session.refresh(agreement)
    return agreement


class TestSearchAgreements:
    async def test_search_by_title(
        self, client, auth_headers, test_org, test_agreement_type, test_user, db_session
    ):
        await _create_agreement(
            db_session, test_org, test_agreement_type, test_user,
            title="Acme Supply Master Agreement", status="executed",
        )
        await _create_agreement(
            db_session, test_org, test_agreement_type, test_user,
            title="Zebra Event Catering", status="draft",
        )

        res = await client.get(
            "/api/v1/search/agreements",
            headers=auth_headers,
            params={"q": "Acme"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "Acme Supply Master Agreement"
        assert body["items"][0]["agreement_type_name"] == "Mutual NDA"

    async def test_search_by_party_name(
        self, client, auth_headers, test_org, test_agreement_type, test_user, db_session
    ):
        await _create_agreement(
            db_session, test_org, test_agreement_type, test_user,
            title="Software Development SOW", party_names=("LankaSoft (Pvt) Ltd",),
        )
        res = await client.get(
            "/api/v1/search/agreements",
            headers=auth_headers,
            params={"q": "LankaSoft"},
        )
        assert res.status_code == 200
        assert res.json()["total"] == 1
        item = res.json()["items"][0]
        assert "LankaSoft (Pvt) Ltd" in item["party_names"]

    async def test_filters_status_and_dates(
        self, client, auth_headers, test_org, test_agreement_type, test_user, db_session
    ):
        await _create_agreement(
            db_session, test_org, test_agreement_type, test_user,
            title="Executed Deal", status="executed",
            effective_date=datetime(2026, 1, 1).date(),
            expiry_date=datetime(2027, 1, 1).date(),
        )
        await _create_agreement(
            db_session, test_org, test_agreement_type, test_user,
            title="Draft Deal", status="draft",
        )

        res = await client.get(
            "/api/v1/search/agreements",
            headers=auth_headers,
            params={"status": "draft"},
        )
        assert res.json()["total"] == 1
        assert res.json()["items"][0]["title"] == "Draft Deal"

        res = await client.get(
            "/api/v1/search/agreements",
            headers=auth_headers,
            params={"effective_from": "2026-01-01", "effective_to": "2026-12-31"},
        )
        assert res.json()["total"] == 1
        assert res.json()["items"][0]["title"] == "Executed Deal"

    async def test_empty_dataset_returns_no_matches(
        self, client, auth_headers, test_org
    ):
        res = await client.get(
            "/api/v1/search/agreements",
            headers=auth_headers,
            params={"q": "anything"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 0
        assert body["items"] == []

    async def test_tenant_isolation(
        self, client, auth_headers, test_org, test_agreement_type, test_user, db_session
    ):
        """Agreements from another org must never appear."""
        from app.models.organization import Organization

        other_org = Organization(
            name="Other Corp", slug="other-corp", country="US", timezone="UTC"
        )
        db_session.add(other_org)
        await db_session.flush()
        await _create_agreement(
            db_session, other_org, test_agreement_type, test_user,
            title="Secret Other Org Deal",
        )

        res = await client.get(
            "/api/v1/search/agreements",
            headers=auth_headers,
            params={"q": "Secret"},
        )
        assert res.json()["total"] == 0


class TestSavedSearches:
    async def test_crud_flow(
        self, client, auth_headers, test_org, test_user, db_session
    ):
        create = await client.post(
            "/api/v1/search/saved",
            headers=auth_headers,
            json={
                "name": "Executed NDAs",
                "query": "NDA",
                "filters": {"status": "executed"},
                "is_favorite": True,
            },
        )
        assert create.status_code == 201
        saved_id = create.json()["id"]
        assert create.json()["filters"] == {"status": "executed"}

        listing = await client.get("/api/v1/search/saved", headers=auth_headers)
        assert listing.status_code == 200
        assert any(s["id"] == saved_id for s in listing.json())

        update = await client.patch(
            f"/api/v1/search/saved/{saved_id}",
            headers=auth_headers,
            json={"is_favorite": False, "query": "mutual NDA"},
        )
        assert update.status_code == 200
        assert update.json()["is_favorite"] is False
        assert update.json()["query"] == "mutual NDA"

        delete = await client.delete(
            f"/api/v1/search/saved/{saved_id}", headers=auth_headers
        )
        assert delete.status_code == 200

        listing2 = await client.get("/api/v1/search/saved", headers=auth_headers)
        assert all(s["id"] != saved_id for s in listing2.json())

    async def test_delete_other_orgs_search_404(
        self, client, auth_headers, test_org, test_user, db_session
    ):
        from app.models.organization import Organization
        from app.models.saved_search import SavedSearch

        other_org = Organization(
            name="Other Corp", slug="other-corp", country="US", timezone="UTC"
        )
        db_session.add(other_org)
        await db_session.flush()
        saved = SavedSearch(
            organization_id=other_org.id,
            created_by=test_user.id,
            name="Theirs",
        )
        db_session.add(saved)
        await db_session.commit()

        res = await client.delete(
            f"/api/v1/search/saved/{saved.id}", headers=auth_headers
        )
        assert res.status_code == 404


class TestNotificationUnreadBadge:
    async def test_unread_count_and_mark_all_read(
        self, client, auth_headers, test_org, db_session
    ):
        db_session.add_all(
            [
                Notification(
                    organization_id=test_org.id,
                    notification_type="workflow_transition",
                    to_email="a@b.com",
                    subject="Unread one",
                    status="sent",
                ),
                Notification(
                    organization_id=test_org.id,
                    notification_type="workflow_transition",
                    to_email="a@b.com",
                    subject="Read one",
                    status="sent",
                    read_at=datetime.now(timezone.utc),
                ),
            ]
        )
        await db_session.commit()

        res = await client.get(
            "/api/v1/notifications/unread-count", headers=auth_headers
        )
        assert res.status_code == 200
        assert res.json()["count"] == 1

        mark = await client.post(
            "/api/v1/notifications/mark-all-read", headers=auth_headers
        )
        assert mark.status_code == 200
        assert mark.json()["marked"] == 1

        res2 = await client.get(
            "/api/v1/notifications/unread-count", headers=auth_headers
        )
        assert res2.json()["count"] == 0

    async def test_unread_isolated_per_org(
        self, client, auth_headers, test_org, test_user, db_session
    ):
        from app.models.organization import Organization

        other_org = Organization(
            name="Other Corp", slug="other-corp-2", country="US", timezone="UTC"
        )
        db_session.add(other_org)
        await db_session.flush()
        db_session.add(
            Notification(
                organization_id=other_org.id,
                notification_type="signature_completed",
                to_email="x@y.com",
                subject="Not ours",
                status="sent",
            )
        )
        await db_session.commit()

        res = await client.get(
            "/api/v1/notifications/unread-count", headers=auth_headers
        )
        assert res.json()["count"] == 0