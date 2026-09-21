"""API tests for the orchestration endpoints (spec §2.11.45)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

WORKFLOW_BODY = {
    "code": "api_review",
    "name": "API review",
    "description": "exercised through the HTTP API",
    "scope": "global",
    "trigger": {"event_type": "api.event"},
    "configuration": {},
    "steps": [
        {
            "step_key": "review",
            "step_type": "task",
            "configuration": {"title": "Review request"},
        }
    ],
    "transitions": [],
}


async def test_create_workflow_requires_manage_permission(client, auth_headers):
    # No org membership -> tenant gate 403s before the permission check.
    response = await client.post(
        "/api/v1/workflows",
        headers=auth_headers,
        json=WORKFLOW_BODY,
    )
    assert response.status_code == 403


async def test_workflow_lifecycle_via_admin(
    client, auth_headers, test_org, test_user, db_session
):
    test_user.is_admin = True
    await db_session.commit()
    response = await client.post(
        "/api/v1/workflows",
        headers=auth_headers,
        json=WORKFLOW_BODY,
    )
    assert response.status_code == 200, response.text
    workflow_id = response.json()["id"]
    assert response.json()["status"] == "draft"

    validate = await client.post(
        f"/api/v1/workflows/{workflow_id}/validate", headers=auth_headers
    )
    assert validate.status_code == 200
    assert validate.json()["valid"] is True, validate.json()

    simulate = await client.post(
        f"/api/v1/workflows/{workflow_id}/simulate",
        headers=auth_headers,
        json={"context": {"amount": 2500}},
    )
    assert simulate.status_code == 200
    assert simulate.json()["completed"] is False
    assert simulate.json()["waiting_at"] == "review"

    publish = await client.post(
        f"/api/v1/workflows/{workflow_id}/publish", headers=auth_headers
    )
    assert publish.status_code == 200, publish.text
    assert publish.json()["status"] == "active"

    dispatched = await client.post(
        "/api/v1/workflow-events/dispatch",
        headers=auth_headers,
        json={
            "event_id": str(uuid.uuid4()),
            "event_type": "api.event",
            "organization_id": str(test_org.id),
            "payload": {"amount": 2500},
        },
    )
    assert dispatched.status_code == 200
    assert len(dispatched.json()["started_instances"]) == 1
    instance_id = dispatched.json()["started_instances"][0]

    instances = await client.get("/api/v1/workflow-instances", headers=auth_headers)
    assert instances.status_code == 200
    assert instances.json()["instances"][0]["id"] == instance_id

    detail = await client.get(
        f"/api/v1/workflow-instances/{instance_id}", headers=auth_headers
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "waiting"

    trace = await client.get(
        f"/api/v1/workflow-instances/{instance_id}/trace", headers=auth_headers
    )
    assert trace.status_code == 200
    assert trace.json()["trace"][0]["step_key"] == "review"

    events = await client.get(
        f"/api/v1/workflow-instances/{instance_id}/events", headers=auth_headers
    )
    assert events.status_code == 200
    assert events.json()["events"]


async def test_dispatch_requires_execute_permission(client, test_user):
    from app.core.security import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(user_id=test_user.id)}"}
    # No org membership -> tenant gate 403s before the permission check.
    response = await client.post(
        "/api/v1/workflow-events/dispatch",
        headers=headers,
        json={
            "event_id": str(uuid.uuid4()),
            "event_type": "api.event",
            "payload": {},
        },
    )
    assert response.status_code == 403


async def test_list_workflows_requires_membership(client, test_user):
    from app.core.security import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(user_id=test_user.id)}"}
    response = await client.get("/api/v1/workflows", headers=headers)
    assert response.status_code == 403


async def test_dead_letters_endpoint_returns_list(client, auth_headers, test_org):
    response = await client.get("/api/v1/workflow-dead-letters", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["dead_letters"] == []


async def test_incidents_endpoint_empty(client, auth_headers, test_org):
    response = await client.get("/api/v1/workflow-incidents", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["incidents"] == []