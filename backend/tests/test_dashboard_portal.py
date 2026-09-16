"""Tests for the user portal dashboard (Panels.txt spec 1.1-1.8)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.user_task import UserActivity, UserTask
from app.services.dashboard_service import get_dashboard, record_activity


@pytest.mark.asyncio
async def test_dashboard_empty_state(db_session, test_user, test_org):
    payload = await get_dashboard(
        db_session,
        user_id=test_user.id,
        org_id=test_org.id,
    )
    assert payload["counts"]["pending_tasks"] == 0
    assert payload["counts"]["unread_notifications"] == 0
    assert payload["tasks"] == []
    assert payload["recent_activity"] == []


@pytest.mark.asyncio
async def test_dashboard_counts_tasks(db_session, test_user, test_org):
    for i in range(3):
        db_session.add(
            UserTask(
                organization_id=test_org.id,
                user_id=test_user.id,
                title=f"Task {i}",
                status="pending",
                priority="normal",
                task_type="manual",
            )
        )
    await db_session.flush()

    payload = await get_dashboard(
        db_session,
        user_id=test_user.id,
        org_id=test_org.id,
    )
    assert payload["counts"]["pending_tasks"] == 3
    assert len(payload["tasks"]) == 3


@pytest.mark.asyncio
async def test_dashboard_excludes_other_users_tasks(db_session, test_user, test_org):
    from app.models.user import User

    other = User(email="other@example.com", name="Other", password_hash="x", status="active")
    db_session.add(other)
    await db_session.flush()

    db_session.add(
        UserTask(
            organization_id=test_org.id,
            user_id=other.id,
            title="Other's task",
            status="pending",
            priority="normal",
            task_type="manual",
        )
    )
    await db_session.flush()

    payload = await get_dashboard(
        db_session,
        user_id=test_user.id,
        org_id=test_org.id,
    )
    assert payload["counts"]["pending_tasks"] == 0


@pytest.mark.asyncio
async def test_record_activity_appends(db_session, test_user, test_org):
    await record_activity(
        db_session,
        organization_id=test_org.id,
        user_id=test_user.id,
        action="AGREEMENT_CREATED",
        resource_type="agreement",
        resource_id=uuid.uuid4(),
        summary="Created test agreement",
    )
    await db_session.flush()

    rows = (await db_session.execute(select(UserActivity))).scalars().all()
    assert len(rows) == 1
    assert rows[0].action == "AGREEMENT_CREATED"

    payload = await get_dashboard(
        db_session,
        user_id=test_user.id,
        org_id=test_org.id,
    )
    assert len(payload["recent_activity"]) == 1


@pytest.mark.asyncio
async def test_task_crud_via_api(client, auth_headers, test_org, test_user):
    # Create
    res = await client.post(
        "/api/v1/dashboard/tasks",
        json={"title": "Review NDA", "priority": "high"},
        headers=auth_headers,
    )
    assert res.status_code == 201
    task_id = res.json()["id"]

    # List
    res = await client.get("/api/v1/dashboard/tasks", headers=auth_headers)
    assert res.status_code == 200
    assert any(t["id"] == task_id for t in res.json())

    # Dashboard payload
    res = await client.get("/api/v1/dashboard", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["counts"]["pending_tasks"] >= 1

    # Update to completed
    res = await client.patch(
        f"/api/v1/dashboard/tasks/{task_id}",
        json={"status": "completed"},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "completed"
    assert res.json()["completed_at"] is not None

    # Delete
    res = await client.delete(f"/api/v1/dashboard/tasks/{task_id}", headers=auth_headers)
    assert res.status_code == 200

    # 404 after delete
    res = await client.patch(
        f"/api/v1/dashboard/tasks/{task_id}",
        json={"status": "completed"},
        headers=auth_headers,
    )
    assert res.status_code == 404