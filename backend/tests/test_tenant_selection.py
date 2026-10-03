"""Tests for deterministic tenant resolution.

The previous implementation took whichever active membership the database
returned first (``LIMIT 1`` with no ``ORDER BY``). For a user holding more
than one active membership that is not a stable answer, so the same account
could act against different organizations on different replicas or after a
plan change — reading and writing the wrong tenant's data.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.dependencies import tenant as tenant_dep


class _Result:
    def __init__(self, org_ids):
        self._org_ids = list(org_ids)

    def scalars(self):
        return self

    def all(self):
        return self._org_ids


class _Db:
    def __init__(self, org_ids):
        self._org_ids = org_ids

    async def execute(self, *a, **kw):
        return _Result(self._org_ids)


class _User:
    id = uuid.UUID("11111111-1111-1111-1111-111111111111")


class _Request:
    def __init__(self, headers=None):
        self.headers = headers or {}


async def _resolve(org_ids, header=None):
    headers = {tenant_dep.ORG_HEADER: header} if header else {}
    return await tenant_dep.get_current_organization_id(
        request=_Request(headers),
        current_user=_User(),
        db=_Db(org_ids),
    )


@pytest.fixture(autouse=True)
def _no_context_write(monkeypatch):
    """Tenant context is a PostgreSQL no-op here; don't assert on it."""
    recorded = []

    async def _record(db, org_id):
        recorded.append(org_id)

    monkeypatch.setattr(tenant_dep, "set_tenant_context", _record)
    return recorded


async def test_no_membership_is_forbidden():
    with pytest.raises(HTTPException) as exc:
        await _resolve([])
    assert exc.value.status_code == 403


async def test_single_membership_needs_no_header():
    org = uuid.uuid4()
    assert await _resolve([org]) == org


async def test_multiple_memberships_require_explicit_selection():
    """The whole point: no silent arbitrary pick."""
    a, b = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        await _resolve([a, b])

    assert exc.value.status_code == 400
    assert tenant_dep.ORG_HEADER in exc.value.detail


async def test_explicit_selection_wins_among_multiple_memberships():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert await _resolve([a, b], header=str(b)) == b


async def test_selection_must_be_an_actual_membership():
    a = uuid.uuid4()
    outsider = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        await _resolve([a], header=str(outsider))

    # Same response as "no memberships at all", so a caller cannot probe
    # which organization ids exist.
    assert exc.value.status_code == 403


async def test_malformed_header_is_a_client_error():
    a = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        await _resolve([a], header="not-a-uuid")

    assert exc.value.status_code == 400
    assert "UUID" in exc.value.detail


async def test_tenant_context_is_set_to_the_resolved_org(_no_context_write):
    a, b = uuid.uuid4(), uuid.uuid4()
    await _resolve([a, b], header=str(b))
    # The RLS GUC has to match the org the request resolved to, otherwise
    # Postgres filters on a different tenant than the queries are written for.
    assert _no_context_write == [b]