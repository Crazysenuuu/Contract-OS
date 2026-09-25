"""Management-API coverage for spec 3.15: integration lifecycle + connection
test, rule lifecycle (get/patch/activate/pause/disable + run gating), exception
resolution and observation listing with permission scoping."""

from __future__ import annotations

import httpx
import pytest

from app.monitoring.models import IntegrationConnection


# --------------------------------------------------------------------------
# Transport stub (per-module copy; shared one lives in the pipeline module)
# --------------------------------------------------------------------------

@pytest.fixture
def stub_http(monkeypatch):
    """Replace httpx.AsyncClient in the http connector with a scriptable client."""

    def install(items, status=200):
        def handler(method, url, params, headers):
            return httpx.Response(
                status,
                json={"items": items},
                request=httpx.Request("GET", "http://test"),
            )

        from app.monitoring.connectors.implementations import http as http_mod

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                self.handler = handler

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def request(self, method, url, params=None, headers=None):
                return self.handler(method, url, params or {}, headers or {})

        monkeypatch.setattr(http_mod.httpx, "AsyncClient", _FakeAsyncClient)

    return install


# --------------------------------------------------------------------------
# Integration lifecycle (create / patch / delete / list)
# --------------------------------------------------------------------------

async def test_api_integration_patch_and_delete(client, auth_headers, test_org):
    create = await client.post(
        "/api/v1/monitoring/integrations",
        json={
            "name": "Vendor API",
            "integration_type": "REST_API",
            "provider_key": "rest_api",
            "configuration": {"base_url": "https://vendor.example.internal"},
        },
        headers=auth_headers,
    )
    assert create.status_code == 201, create.text
    integration_id = create.json()["id"]

    patched = await client.patch(
        f"/api/v1/monitoring/integrations/{integration_id}",
        json={"name": "Renamed", "status": "DISCONNECTED"},
        headers=auth_headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Renamed"
    assert patched.json()["status"] == "DISCONNECTED"

    deleted = await client.delete(
        f"/api/v1/monitoring/integrations/{integration_id}",
        headers=auth_headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["status"] == "deleted"

    listing = await client.get("/api/v1/monitoring/integrations", headers=auth_headers)
    assert listing.status_code == 200
    assert all(item["id"] != integration_id for item in listing.json())


async def test_api_integration_invalid_patch_status_rejected(client, auth_headers, monitoring_integration):
    patched = await client.patch(
        f"/api/v1/monitoring/integrations/{monitoring_integration.id}",
        json={"status": "WARP_DRIVE"},
        headers=auth_headers,
    )
    assert patched.status_code == 422


async def test_api_integration_delete_blocked_while_bound(client, auth_headers, active_monitoring):
    response = await client.delete(
        f"/api/v1/monitoring/integrations/{active_monitoring.integration_id}",
        headers=auth_headers,
    )
    assert response.status_code == 409


# --------------------------------------------------------------------------
# Connection test endpoint (3.15.26)
# --------------------------------------------------------------------------

async def test_api_connection_test_success_marks_active(client, auth_headers, monitoring_integration, stub_http):
    stub_http([])
    response = await client.post(
        f"/api/v1/monitoring/integrations/{monitoring_integration.id}/test",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True

    listing = await client.get("/api/v1/monitoring/integrations", headers=auth_headers)
    integration = next(i for i in listing.json() if i["id"] == str(monitoring_integration.id))
    assert integration["status"] == "ACTIVE"
    assert integration["health"]["consecutive_failures"] == 0


async def test_api_connection_test_failure_degrades(client, auth_headers, monitoring_integration, stub_http):
    stub_http([], status=401)
    response = await client.post(
        f"/api/v1/monitoring/integrations/{monitoring_integration.id}/test",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is False

    listing = await client.get("/api/v1/monitoring/integrations", headers=auth_headers)
    integration = next(i for i in listing.json() if i["id"] == str(monitoring_integration.id))
    assert integration["status"] == "DEGRADED"


async def test_api_connection_test_unknown_provider_rejected(
    client,
    auth_headers,
    db_session,
    test_org,
    test_user,
):
    integration = IntegrationConnection(
        organization_id=test_org.id,
        name="Bogus",
        integration_type="REST_API",
        provider_key="no_such_provider",
        status="CONNECTING",
        configuration={},
        created_by=test_user.id,
    )
    db_session.add(integration)
    await db_session.commit()

    response = await client.post(
        f"/api/v1/monitoring/integrations/{integration.id}/test",
        headers=auth_headers,
    )
    assert response.status_code == 400, response.text


# --------------------------------------------------------------------------
# Rule lifecycle (get / patch / activate / pause / disable)
# --------------------------------------------------------------------------

async def test_api_rule_lifecycle(client, auth_headers, active_monitoring):
    rule_id = str(active_monitoring.id)

    got = await client.get(f"/api/v1/monitoring/rules/{rule_id}", headers=auth_headers)
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "ACTIVE"

    patched = await client.patch(
        f"/api/v1/monitoring/rules/{rule_id}",
        json={
            "evaluation_definition": {
                "kind": "threshold",
                "field": "amount",
                "operator": "gte",
                "expected": 100,
            }
        },
        headers=auth_headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["evaluation_definition"]["kind"] == "threshold"
    assert patched.json()["next_run_at"] is not None

    paused = await client.post(
        f"/api/v1/monitoring/rules/{rule_id}/pause?pause_reason=manual",
        headers=auth_headers,
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "PAUSED"
    assert paused.json()["pause_reason"] == "manual"
    assert paused.json()["next_run_at"] is None

    activated = await client.post(
        f"/api/v1/monitoring/rules/{rule_id}/activate",
        headers=auth_headers,
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "ACTIVE"
    assert activated.json()["next_run_at"] is not None

    disabled = await client.post(
        f"/api/v1/monitoring/rules/{rule_id}/disable",
        headers=auth_headers,
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "DISABLED"

    pause_conflict = await client.post(
        f"/api/v1/monitoring/rules/{rule_id}/pause",
        headers=auth_headers,
    )
    assert pause_conflict.status_code == 409

    reactivated = await client.post(
        f"/api/v1/monitoring/rules/{rule_id}/activate",
        headers=auth_headers,
    )
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["status"] == "ACTIVE"


async def test_api_rule_run_requires_active(
    client,
    auth_headers,
    monitoring_integration,
    monitoring_obligation,
    agreement_version,
):
    created = await client.post(
        "/api/v1/monitoring/rules",
        json={
            "obligation_id": str(monitoring_obligation.id),
            "integration_id": str(monitoring_integration.id),
            "source_version_id": str(agreement_version.id),
            "status": "DRAFT",
            "query_definition": {"resource": "claims"},
            "evaluation_definition": {"kind": "existence", "expected": True},
            "schedule_definition": {"recurrence": "interval", "minutes": 60},
            "automation": {},
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "DRAFT"

    run_conflict = await client.post(
        f"/api/v1/monitoring/rules/{created.json()['id']}/run",
        headers=auth_headers,
    )
    assert run_conflict.status_code == 409


async def test_api_rule_create_invalid_schedule_rejected(
    client,
    auth_headers,
    monitoring_integration,
    monitoring_obligation,
    agreement_version,
):
    created = await client.post(
        "/api/v1/monitoring/rules",
        json={
            "obligation_id": str(monitoring_obligation.id),
            "integration_id": str(monitoring_integration.id),
            "source_version_id": str(agreement_version.id),
            "status": "ACTIVE",
            "query_definition": {"resource": "claims"},
            "evaluation_definition": {"kind": "existence", "expected": True},
            "schedule_definition": {"recurrence": "cron", "expression": "0 0 30 2 *"},
            "automation": {},
        },
        headers=auth_headers,
    )
    assert created.status_code == 400, created.text


# --------------------------------------------------------------------------
# Observations (3.15.28) + exception resolution (3.15.24 / 3.15.29)
# --------------------------------------------------------------------------

async def test_api_observations_list_after_run(client, auth_headers, active_monitoring, stub_http):
    stub_http([{"id": "c1", "amount": 150, "observed_at": "2026-09-25T12:00:00Z"}])

    run = await client.post(
        f"/api/v1/monitoring/rules/{active_monitoring.id}/run",
        headers=auth_headers,
    )
    assert run.status_code == 200, run.text
    assert run.json()["result"] == "PASS"

    observations = await client.get(
        f"/api/v1/monitoring/rules/{active_monitoring.id}/observations",
        headers=auth_headers,
    )
    assert observations.status_code == 200, observations.text
    assert len(observations.json()) >= 1
    assert observations.json()[0]["payload"]["amount"] == 150
    assert observations.json()[0]["payload_hash"]


async def test_api_observations_requires_view_data(client, active_monitoring, get_second_org_user):
    second = await get_second_org_user(permissions=["monitoring.view"])
    no_data_perm = await client.get(
        f"/api/v1/monitoring/rules/{active_monitoring.id}/observations",
        headers=second["auth_headers"],
    )
    assert no_data_perm.status_code == 403

    second_data = await get_second_org_user(
        permissions=["monitoring.view", "monitoring.view_data"]
    )
    cross_org = await client.get(
        f"/api/v1/monitoring/rules/{active_monitoring.id}/observations",
        headers=second_data["auth_headers"],
    )
    assert cross_org.status_code == 404


async def test_api_exception_open_list_and_resolve(client, auth_headers, active_monitoring, stub_http):
    stub_http([])
    run = await client.post(
        f"/api/v1/monitoring/rules/{active_monitoring.id}/run",
        headers=auth_headers,
    )
    assert run.status_code == 200, run.text
    assert run.json()["result"] == "FAIL"

    exc_list = await client.get("/api/v1/monitoring/exceptions", headers=auth_headers)
    assert exc_list.status_code == 200, exc_list.text
    targeting = [e for e in exc_list.json() if e["monitoring_id"] == str(active_monitoring.id)]
    assert len(targeting) == 1
    assert targeting[0]["status"] == "OPEN"

    resolved = await client.post(
        f"/api/v1/monitoring/exceptions/{targeting[0]['id']}/resolve?comment=handled",
        headers=auth_headers,
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "RESOLVED"

    open_only = await client.get(
        "/api/v1/monitoring/exceptions?open_only=true",
        headers=auth_headers,
    )
    assert all(e["id"] != targeting[0]["id"] for e in open_only.json())