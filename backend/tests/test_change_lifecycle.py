"""Change-set lifecycle tests (spec 2.05).

Covers the full change chain: propose → counter → accept, the supersede
semantics of countering, and the spec-compliant /change-sets URL surface.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.agreement import AgreementVersion
from app.models.negotiation import AgreementChange, AgreementChangeItem


@pytest_asyncio.fixture(autouse=True)
async def _base_version(db_session, test_agreement, test_user):
    from app.models.agreement import AgreementVersion as AV

    version = AV(
        agreement_id=test_agreement.id,
        version_number=1,
        content="ORIGINAL TERMS",
        content_hash="orig-hash",
        status="current",
        created_by=test_user.id,
    )
    db_session.add(version)
    await db_session.commit()
    await db_session.refresh(version)
    return version


async def _props(client, auth_headers, agreement_id):
    return await client.post(
        f"/api/v1/agreements/{agreement_id}/change-sets",
        json={
            "change_type": "redline",
            "explanation": "Update payment terms",
            "modifications": [
                {
                    "clause_identifier": "payment_terms",
                    "change_type": "modify",
                    "new_content": "Payment within 45 days",
                    "reason": "Cash flow alignment",
                }
            ],
        },
        headers=auth_headers,
    )


async def test_propose_change_creates_version_and_items(
    client, auth_headers, db_session, test_agreement, test_user
):
    resp = await _props(client, auth_headers, test_agreement.id)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "proposed"
    assert body["change_type"] == "redline"

    items = (
        await db_session.execute(
            select(AgreementChangeItem).where(
                AgreementChangeItem.change_id == body["id"]
            )
        )
    ).scalars().all()
    assert len(items) == 1
    assert items[0].clause_identifier == "payment_terms"
    assert items[0].change_type == "modify"


async def test_counter_supersedes_original_and_creates_new(
    client, auth_headers, db_session, test_agreement, test_user
):
    first = await _props(client, auth_headers, test_agreement.id)
    assert first.status_code == 201
    original_id = first.json()["id"]

    counter = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/change-sets/{original_id}/counter",
        json={
            "change_type": "counter",
            "explanation": "Counter: 60 days is acceptable",
            "modifications": [
                {
                    "clause_identifier": "payment_terms",
                    "change_type": "modify",
                    "new_content": "Payment within 60 days",
                    "reason": "Counter proposal",
                }
            ],
        },
        headers=auth_headers,
    )
    assert counter.status_code == 201, counter.text
    counter_body = counter.json()
    assert counter_body["change_type"] == "counter"
    assert counter_body["status"] == "proposed"
    assert counter_body["id"] != original_id

    # Original is superseded, counter references the same base version.
    original = (
        await db_session.execute(
            select(AgreementChange).where(AgreementChange.id == original_id)
        )
    ).scalars().one()
    assert original.status == "superseded"
    assert counter_body["base_version_id"] == first.json()["base_version_id"]

    versions = (
        await db_session.execute(
            select(AgreementVersion).where(
                AgreementVersion.agreement_id == test_agreement.id
            )
        )
    ).scalars().all()
    contents = [v.content for v in versions]
    assert any("45 days" in c for c in contents)   # original proposal
    assert any("60 days" in c for c in contents)   # counter content


async def test_accept_counter_promotes_version(
    client, auth_headers, db_session, test_agreement, test_user
):
    first = await _props(client, auth_headers, test_agreement.id)
    original_id = first.json()["id"]

    counter = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/change-sets/{original_id}/counter",
        json={
            "change_type": "counter",
            "modifications": [
                {
                    "clause_identifier": "payment_terms",
                    "change_type": "modify",
                    "new_content": "Payment within 60 days",
                }
            ],
        },
        headers=auth_headers,
    )
    counter_id = counter.json()["id"]

    accepted = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/change-sets/{counter_id}/accept",
        headers=auth_headers,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"

    # The counter's proposed version was promoted to the current one.
    current = (
        await db_session.execute(
            select(AgreementVersion)
            .where(
                AgreementVersion.agreement_id == test_agreement.id,
                AgreementVersion.status == "current",
            )
            .order_by(AgreementVersion.version_number.desc())
        )
    ).scalars().first()
    assert current is not None
    assert "60 days" in current.content


async def test_counter_rejects_on_already_resolved(client, auth_headers, test_agreement):
    first = await _props(client, auth_headers, test_agreement.id)
    change_id = first.json()["id"]

    await client.post(
        f"/api/v1/agreements/{test_agreement.id}/change-sets/{change_id}/reject",
        headers=auth_headers,
    )

    counter = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/change-sets/{change_id}/counter",
        json={"change_type": "counter", "modifications": []},
        headers=auth_headers,
    )
    assert counter.status_code == 400


async def test_change_set_url_alias_and_changes_url_are_equivalent(
    client, auth_headers, test_agreement
):
    via_changes = await client.post(
        f"/api/v1/agreements/{test_agreement.id}/changes",
        json={
            "change_type": "redline",
            "modifications": [
                {
                    "clause_identifier": "confidentiality",
                    "change_type": "modify",
                    "new_content": "Confidentiality for 5 years",
                }
            ],
        },
        headers=auth_headers,
    )
    assert via_changes.status_code == 201, via_changes.text
    change_id = via_changes.json()["id"]

    listed = await client.get(
        f"/api/v1/agreements/{test_agreement.id}/change-sets",
        headers=auth_headers,
    )
    assert listed.status_code == 200
    assert any(d["id"] == change_id for d in listed.json())

# --- accept/reject with items loaded through the non-eager path ----------


async def test_accept_change_via_service_function_is_async_safe(
    db_session, test_agreement, test_user
):
    """Regression: accept/reject iterate ``change.items``, which raises
    MissingGreenlet if the relationship was never loaded. All current HTTP
    callers load it via get_change (selectinload); this pins the service
    functions themselves to be safe with a fresh-from-DB instance."""
    from app.services.agreement_changes import accept_change, create_change

    change = await create_change(
        db_session,
        agreement=test_agreement,
        base_version_id=await _base_version_id(db_session, test_agreement),
        proposed_by=test_user.id,
        change_type="redline",
        explanation="Extend payment terms",
        items=[
            {
                "clause_identifier": "payment_terms",
                "change_type": "modify",
                "new_content": "Payment within 45 days",
            }
        ],
    )

    # Simulate a caller that fetched the row without eager loading.
    from sqlalchemy import select

    fresh = (
        await db_session.execute(
            select(AgreementChange).where(AgreementChange.id == change.id)
        )
    ).scalar_one()

    result = await accept_change(db_session, fresh)
    assert result.status == "accepted"
    assert all(item.status == "accepted" for item in result.items)


async def test_reject_change_via_service_function_is_async_safe(
    db_session, test_agreement, test_user
):
    from sqlalchemy import select

    from app.services.agreement_changes import create_change, reject_change

    change = await create_change(
        db_session,
        agreement=test_agreement,
        base_version_id=await _base_version_id(db_session, test_agreement),
        proposed_by=test_user.id,
        change_type="redline",
        explanation="Shorten confidentiality",
        items=[
            {
                "clause_identifier": "confidentiality",
                "change_type": "modify",
                "new_content": "Confidentiality for 2 years",
            }
        ],
    )
    fresh = (
        await db_session.execute(
            select(AgreementChange).where(AgreementChange.id == change.id)
        )
    ).scalar_one()

    result = await reject_change(db_session, fresh)
    assert result.status == "rejected"
    assert all(item.status == "rejected" for item in result.items)


async def _base_version_id(db_session, agreement):
    from sqlalchemy import select

    row = await db_session.execute(
        select(AgreementVersion.id)
        .where(AgreementVersion.agreement_id == agreement.id)
        .order_by(AgreementVersion.version_number.desc())
        .limit(1)
    )
    return row.scalar_one()
