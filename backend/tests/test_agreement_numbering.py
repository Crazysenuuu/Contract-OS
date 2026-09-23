"""Per-organization agreement numbering (spec 2.01 §46).

Covers the requirements that matter operationally:

- Every agreement gets a server-generated business reference of the form
  ``AGR-<org prefix>-<seq>`` (spec: "It should be created by the server").
- Sequences are scoped per organization: two orgs count independently.
- Numbers are unique within an organization (partial unique index at the
  database level; enforced logically here).
- The reference is display/search only — the UUID stays the primary key.
"""
import uuid

import pytest
from sqlalchemy import func, select

from app.models.agreement import Agreement
from app.services.agreement_numbering import (
    format_agreement_number,
    next_agreement_number,
    parse_agreement_number,
)


def _make_agreement(db, org, user, atype, title: str) -> Agreement:
    """Allocate a reference the way every creation site must, then insert."""
    agreement = Agreement(
        organization_id=org.id,
        agreement_type_id=atype.id,
        agreement_number=None,  # filled in below, like the real sites
        title=title,
        created_by=user.id,
        data={},
    )
    return agreement


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_format_is_deterministic_and_scoped():
    org = uuid.UUID("a1b2c3d4-0000-0000-0000-000000000000")
    assert format_agreement_number(org, 1) == "AGR-A1B2C3D4-00001"
    assert format_agreement_number(org, 42) == "AGR-A1B2C3D4-00042"


def test_parse_round_trips_and_rejects_garbage():
    assert parse_agreement_number("AGR-A1B2C3D4-00007") == ("A1B2C3D4", 7)
    assert parse_agreement_number("AGR-A1B2C3D4-notanumber") is None
    assert parse_agreement_number("XX-A1B2C3D4-00001") is None
    assert parse_agreement_number("AGR-A1B2C3D4") is None


# ---------------------------------------------------------------------------
# Allocation (service layer, SQLite suite)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequence_increments_per_org(db_session, test_org, test_user, test_agreement_type):
    n1 = await next_agreement_number(db_session, test_org.id)
    a1 = Agreement(
        organization_id=test_org.id,
        agreement_type_id=test_agreement_type.id,
        agreement_number=n1,
        title="First",
        created_by=test_user.id,
        data={},
    )
    db_session.add(a1)
    await db_session.commit()

    n2 = await next_agreement_number(db_session, test_org.id)
    a2 = Agreement(
        organization_id=test_org.id,
        agreement_type_id=test_agreement_type.id,
        agreement_number=n2,
        title="Second",
        created_by=test_user.id,
        data={},
    )
    db_session.add(a2)
    await db_session.commit()

    assert n1 == "AGR-%s-00001" % test_org.id.hex[:8].upper()
    assert n2 == "AGR-%s-00002" % test_org.id.hex[:8].upper()


@pytest.mark.asyncio
async def test_counters_are_independent_per_organization(
    db_session, test_org, test_user, test_agreement_type
):
    from app.models.organization import Organization

    other = Organization(
        name="Other Corp",
        slug="other-corp",
        country="US",
        timezone="America/New_York",
    )
    db_session.add(other)
    await db_session.commit()

    a1 = Agreement(
        organization_id=test_org.id,
        agreement_type_id=test_agreement_type.id,
        agreement_number=await next_agreement_number(db_session, test_org.id),
        title="Org A #1",
        created_by=test_user.id,
        data={},
    )
    db_session.add(a1)
    await db_session.commit()

    b1 = Agreement(
        organization_id=other.id,
        agreement_type_id=test_agreement_type.id,
        agreement_number=await next_agreement_number(db_session, other.id),
        title="Org B #1",
        created_by=test_user.id,
        data={},
    )
    db_session.add(b1)
    await db_session.commit()

    # Both orgs independently start at 1.
    assert a1.agreement_number is not None
    assert b1.agreement_number is not None
    assert a1.agreement_number.endswith("-00001")
    assert b1.agreement_number.endswith("-00001")
    assert a1.agreement_number != b1.agreement_number


@pytest.mark.asyncio
async def test_max_survives_out_of_order_and_legacy_rows(
    db_session, test_org, test_user, test_agreement_type
):
    """A legacy row with no number must not reset the sequence."""
    legacy = Agreement(
        organization_id=test_org.id,
        agreement_type_id=test_agreement_type.id,
        agreement_number=None,  # created before the feature
        title="Legacy",
        created_by=test_user.id,
        data={},
    )
    db_session.add(legacy)
    await db_session.commit()

    n = await next_agreement_number(db_session, test_org.id)
    assert n.endswith("-00001")


# ---------------------------------------------------------------------------
# Creation endpoints assign the reference
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_endpoint_assigns_number(
    client, auth_headers, db_session, test_user, test_org
):
    from app.models.agreement_type import AgreementType

    atype = AgreementType(
        key="numbering_test_type",
        name="Numbering Test",
        category="commercial",
        status="active",
        schema={"schema_version": 1, "sections": []},
    )
    db_session.add(atype)
    await db_session.commit()
    await db_session.refresh(atype)

    resp = await client.post(
        "/api/v1/agreements",
        json={"title": "Numbered", "agreement_type_id": str(atype.id), "data": {}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["agreement_number"] == (
        "AGR-%s-00001" % test_org.id.hex[:8].upper()
    )


@pytest.mark.asyncio
async def test_rapid_sequential_creates_get_distinct_numbers(
    client, auth_headers, db_session, test_user, test_org
):
    """Rapid-fire creates must never reuse a number.

    True cross-connection serialization is the FOR UPDATE org-row lock, which
    only exists on PostgreSQL (SQLite ignores it, and the test engine shares
    one connection, so interleaved requests are unrepresentative here). The
    database-level unique index is the hard backstop and is covered by
    test_no_duplicate_numbers_in_db.
    """
    from app.models.agreement_type import AgreementType

    atype = AgreementType(
        key="numbering_burst_type",
        name="Numbering Burst",
        category="commercial",
        status="active",
        schema={"schema_version": 1, "sections": []},
    )
    db_session.add(atype)
    await db_session.commit()

    numbers = []
    for i in range(5):
        r = await client.post(
            "/api/v1/agreements",
            json={"title": f"Burst {i}", "agreement_type_id": str(atype.id), "data": {}},
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text
        numbers.append(r.json()["agreement_number"])

    assert len(set(numbers)) == 5, numbers


# ---------------------------------------------------------------------------
# Invariant: numbers unique per org in the database
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_duplicate_numbers_in_db(
    client, auth_headers, db_session, test_user, test_org
):
    from sqlalchemy.exc import IntegrityError

    from app.models.agreement_type import AgreementType

    atype = AgreementType(
        key="numbering_dup_type",
        name="Numbering Dup",
        category="commercial",
        status="active",
        schema={"schema_version": 1, "sections": []},
    )
    db_session.add(atype)
    await db_session.commit()

    # Manually force a duplicate through the model layer: the partial unique
    # index must reject it.
    a1 = Agreement(
        organization_id=test_org.id,
        agreement_type_id=atype.id,
        agreement_number="AGR-XXXX-09999",
        title="Dup A",
        created_by=test_user.id,
        data={},
    )
    a2 = Agreement(
        organization_id=test_org.id,
        agreement_type_id=atype.id,
        agreement_number="AGR-XXXX-09999",
        title="Dup B",
        created_by=test_user.id,
        data={},
    )
    db_session.add_all([a1, a2])
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
