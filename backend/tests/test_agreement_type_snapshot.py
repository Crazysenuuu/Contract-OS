"""Agreement-type definition snapshot (spec 1.4 immutability).

Verifies that when an agreement is created it records the agreement_type
version it was created against, so later edits to the published type cannot
silently reinterpret an existing contract.
"""

import uuid

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


async def test_creation_snapshots_type_version(
    client, auth_headers, db_session, test_org, test_agreement_type
):
    from app.models.agreement import Agreement

    response = await client.post(
        "/api/v1/agreements",
        json={
            "title": "Snapshot Test Agreement",
            "agreement_type_id": str(test_agreement_type.id),
            "governing_law": "England and Wales",
            "data": {"effective_date": "2026-01-01"},
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["agreement_type_version"] == test_agreement_type.version

    from sqlalchemy import select

    result = await db_session.execute(
        select(Agreement).where(Agreement.id == response.json()["id"])
    )
    agreement = result.scalar_one()
    assert agreement.agreement_type_version == test_agreement_type.version


async def test_snapshot_survives_type_edit(
    client, auth_headers, db_session, test_org, test_agreement_type
):
    from sqlalchemy import select

    from app.models.agreement import Agreement

    response = await client.post(
        "/api/v1/agreements",
        json={
            "title": "Snapshot Persistence",
            "agreement_type_id": str(test_agreement_type.id),
            "data": {},
        },
        headers=auth_headers,
    )
    agreement_id = response.json()["id"]
    assert response.status_code == 201

    # Publish a new version of the type definition.
    test_agreement_type.version += 1
    db_session.add(test_agreement_type)
    await db_session.commit()

    result = await db_session.execute(
        select(Agreement).where(Agreement.id == agreement_id)
    )
    stored = result.scalar_one()
    assert stored.agreement_type_version == 1
    assert stored.agreement_type_version != test_agreement_type.version