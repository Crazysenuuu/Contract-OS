"""Tests for the mobile backend (spec M2.02 / sections 25-26, 55-56).

Device registration uses the authenticated user from the JWT — never a
client-supplied user_id. Token rotation upserts the same (user,device) row
rather than creating duplicates. GET /api/v1/mobile/config is public and
returns deployment gating values.
"""

import pytest
from sqlalchemy import select

from app.models.user_device import UserDevice


async def _register(client, auth_headers, **overrides):
    body = {
        "device_id": "device-abc",
        "platform": "ios",
        "push_token": "fcm-token-1",
        "app_version": "1.2.0",
        **overrides,
    }
    return await client.post(
        "/api/v1/mobile/devices", json=body, headers=auth_headers
    )


async def test_config_is_public(client, auth_headers):
    resp = await client.get("/api/v1/mobile/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["minimum_supported_version"]
    assert body["recommended_version"]
    assert "maintenance" in body


async def test_register_device_creates_row(client, auth_headers, db_session):
    resp = await _register(client, auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["device_id"] == "device-abc"
    assert body["platform"] == "ios"
    assert body["push_token"] == "fcm-token-1"
    assert body["app_version"] == "1.2.0"
    assert body["revoked_at"] is None

    rows = (await db_session.execute(select(UserDevice))).scalars().all()
    assert len(rows) == 1


async def test_token_rotation_upserts_not_duplicates(
    client, auth_headers, db_session
):
    first = await _register(client, auth_headers, push_token="old-token")
    assert first.status_code == 201

    second = await _register(client, auth_headers, push_token="new-token")
    assert second.status_code == 201
    assert second.json()["push_token"] == "new-token"
    assert second.json()["id"] == first.json()["id"]

    rows = (await db_session.execute(select(UserDevice))).scalars().all()
    assert len(rows) == 1
    assert rows[0].push_token == "new-token"


async def test_device_bound_to_authenticated_user(
    client, auth_headers, db_session, test_user
):
    await _register(client, auth_headers)
    row = (await db_session.execute(select(UserDevice))).scalars().one()
    assert row.user_id == test_user.id


async def test_platform_validation(client, auth_headers):
    resp = await _register(client, auth_headers, platform="nokia")
    assert resp.status_code == 422


async def test_list_devices(client, auth_headers):
    await _register(client, auth_headers, device_id="d-1", push_token="t1")
    await _register(client, auth_headers, device_id="d-2", push_token="t2")
    resp = await client.get("/api/v1/mobile/devices", headers=auth_headers)
    assert resp.status_code == 200
    ids = {d["device_id"] for d in resp.json()}
    assert ids == {"d-1", "d-2"}


async def test_revoke_device(client, auth_headers, db_session):
    device_id = "device-abc"
    await _register(client, auth_headers, device_id=device_id)
    resp = await client.delete(
        f"/api/v1/mobile/devices/{device_id}", headers=auth_headers
    )
    assert resp.status_code == 204

    row = (await db_session.execute(
        select(UserDevice).where(UserDevice.device_id == device_id)
    )).scalars().one()
    assert row.revoked_at is not None
    assert row.push_token is None

    listed = await client.get("/api/v1/mobile/devices", headers=auth_headers)
    assert listed.json() == []
