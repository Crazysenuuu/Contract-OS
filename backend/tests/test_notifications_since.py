"""Tests for GET /notifications?since= — the mobile client's missed-event
recovery source (spec 2.02 §33): while disconnected from the WebSocket it
polls this endpoint with its last received timestamp and replays what it
missed."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.notification import Notification
from app.services.event_outbox_service import enqueue_event


async def _notify(db_session, org_id, email, subject, *, minutes_ago=0):
    n = Notification(
        organization_id=org_id,
        notification_type="workflow_transition",
        to_email=email,
        subject=subject,
        status="sent",
        sent_at=datetime.now(timezone.utc),
    )
    if minutes_ago:
        n.created_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    db_session.add(n)
    await db_session.flush()
    return n


@pytest.mark.asyncio
async def test_since_filters_old_notifications(
    client, db_session, test_user, test_org, auth_headers
):
    await _notify(db_session, test_org.id, "a@x.com", "old", minutes_ago=30)
    await _notify(db_session, test_org.id, "a@x.com", "new")

    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    resp = await client.get(
        "/api/v1/notifications",
        params={"since": since.isoformat()},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    subjects = [n["subject"] for n in resp.json()]
    assert subjects == ["new"]


@pytest.mark.asyncio
async def test_since_boundary_is_exclusive(
    client, db_session, test_user, test_org, auth_headers
):
    """created_at > since strictly: the client already has the boundary
    event, so replaying it would duplicate a banner."""
    n = await _notify(db_session, test_org.id, "a@x.com", "boundary")
    await db_session.refresh(n)

    resp = await client.get(
        "/api/v1/notifications",
        params={"since": n.created_at.isoformat()},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_since_respects_org_isolation(
    client, db_session, test_user, test_org, auth_headers
):
    other_org = uuid.uuid4()
    await _notify(db_session, other_org, "b@x.com", "other org")

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    resp = await client.get(
        "/api/v1/notifications",
        params={"since": since.isoformat()},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_without_since_returns_full_history(
    client, db_session, test_user, test_org, auth_headers
):
    await _notify(db_session, test_org.id, "a@x.com", "old", minutes_ago=30)
    await _notify(db_session, test_org.id, "a@x.com", "new")

    resp = await client.get("/api/v1/notifications", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 2
