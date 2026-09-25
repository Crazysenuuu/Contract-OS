"""Monitoring API (spec 3.15.46 authorization, 3.15.40 dashboard).

Scope is workspace-scoped: every query filters by ``get_current_organization_id``.
  - ``monitoring.view``        read integrations, rules, health, evaluations
  - ``monitoring.view_data``   read raw external observations
  - ``monitoring.manage``      configure integrations + credentials
  - ``monitoring.manage_rules`` create/edit/pause/run monitoring rules

The inbound webhook endpoint is mounted on a prefix-less router so the spec
path ``POST /api/v1/integrations/{integration_id}/webhook`` is preserved
without colliding with the 24.6 connector router.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.rbac import require_permission
from app.dependencies.tenant import get_current_organization_id
from app.monitoring.connectors import connector_registry
from app.monitoring.connectors.base import get_metadata
from app.monitoring.credentials import get_active_credentials
from app.monitoring.enums import IntegrationStatus, PauseReason
from app.monitoring.exceptions import MonitoringError, WebhookVerificationError
from app.monitoring.models import (
    ExternalObservationRecord,
    IntegrationConnection,
    IntegrationCredential,
    IntegrationHealth,
    MonitoringEvaluation,
    MonitoringEvidence,
    MonitoringException,
    MonitoringRun,
    ObligationMonitoring,
)
from app.monitoring.observations import (
    ensure_aware_utc,
    hash_payload,
    verify_webhook_time_delivery,
)
from app.monitoring.schemas import (
    CredentialCreateRequest,
    EvaluationOut,
    ExceptionOut,
    IntegrationCreateRequest,
    IntegrationOut,
    IntegrationUpdateRequest,
    ObservationOut,
    RuleCreateRequest,
    RuleOut,
    RuleUpdateRequest,
    RunRuleResponse,
    WebhookAck,
)
from app.monitoring.service import (
    AUDIT_INTEGRATION_AUTH_FAILED,
    AUDIT_INTEGRATION_CONNECTED,
    AUDIT_INTEGRATION_CREATED,
    AUDIT_INTEGRATION_CREDENTIAL_ROTATED,
    AUDIT_INTEGRATION_DISCONNECTED,
    AUDIT_INTEGRATION_UPDATED,
    AUDIT_MONITORING_ACTIVATED,
    AUDIT_MONITORING_CREATED,
    AUDIT_MONITORING_DISABLED,
    AUDIT_MONITORING_PAUSED,
    AUDIT_EXCEPTION_RESOLVED,
    AUDIT_OBSERVATION_REJECTED,
    compute_next_run,
    ingest_webhook_observations,
    run_obligation_monitoring,
)

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/monitoring", tags=["monitoring"])
webhook_router = APIRouter(tags=["monitoring-webhooks"])

perm_view = Depends(require_permission("monitoring.view"))
perm_view_data = Depends(require_permission("monitoring.view_data"))
perm_manage = Depends(require_permission("monitoring.manage"))
perm_rules = Depends(require_permission("monitoring.manage_rules"))


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


async def _audit_event(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    metadata_json: dict | None = None,
) -> None:
    """Append to the tenant audit hash chain (spec 3.15.47). Auditing must
    never fail the user-facing mutation, so errors are logged and swallowed."""
    from app.services.audit_service import record_event

    try:
        await record_event(
            db,
            tenant_id=org_id,
            actor_id=actor_id,
            actor_type="user" if actor_id is not None else "system",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_json=metadata_json or {},
        )
    except Exception:  # noqa: BLE001 — audit is best-effort
        _log.warning("monitoring audit failed for %s: %s", action, resource_id)


async def _get_integration(
    db: AsyncSession,
    integration_id: uuid.UUID,
    org_id: uuid.UUID,
) -> IntegrationConnection:
    integration = await db.get(IntegrationConnection, integration_id)
    if integration is None or integration.organization_id != org_id:
        raise _not_found()
    return integration


async def _get_rule(
    db: AsyncSession,
    rule_id: uuid.UUID,
    org_id: uuid.UUID,
) -> ObligationMonitoring:
    rule = await db.get(ObligationMonitoring, rule_id)
    if rule is None or rule.organization_id != org_id:
        raise _not_found()
    return rule


def _serialize_integration(integration: IntegrationConnection) -> dict:
    health = None
    if integration.health is not None:
        health = {
            "integration_id": str(integration.health.integration_id),
            "last_success_at": integration.health.last_success_at,
            "last_failure_at": integration.health.last_failure_at,
            "consecutive_failures": integration.health.consecutive_failures,
            "last_latency_ms": integration.health.last_latency_ms,
            "last_error_code": integration.health.last_error_code,
            "last_error": integration.health.last_error,
        }
    return {
        "id": str(integration.id),
        "name": integration.name,
        "integration_type": integration.integration_type,
        "provider_key": integration.provider_key,
        "status": integration.status,
        "configuration": integration.configuration,
        "created_at": integration.created_at,
        "health": health,
    }


def _serialize_rule(rule: ObligationMonitoring) -> dict:
    return {
        "id": str(rule.id),
        "obligation_id": str(rule.obligation_id),
        "integration_id": str(rule.integration_id),
        "source_version_id": str(rule.source_version_id),
        "status": rule.status,
        "pause_reason": rule.pause_reason,
        "query_definition": rule.query_definition,
        "evaluation_definition": rule.evaluation_definition,
        "schedule_definition": rule.schedule_definition,
        "automation": rule.automation,
        "next_run_at": rule.next_run_at,
        "last_run_at": rule.last_run_at,
        "last_result": rule.last_result,
        "created_at": rule.created_at,
    }


def _serialize_evaluation(evaluation: MonitoringEvaluation) -> dict:
    return {
        "id": str(evaluation.id),
        "monitoring_id": str(evaluation.monitoring_id),
        "result": evaluation.result,
        "status": evaluation.status,
        "metrics": evaluation.metrics,
        "observation_ids": evaluation.observation_ids,
        "evaluated_at": evaluation.evaluated_at,
        "details": evaluation.details,
    }


# --------------------------------------------------------------------------
# Integrations
# --------------------------------------------------------------------------

@router.get("/integrations/providers", dependencies=[perm_view])
async def list_monitoring_providers():
    providers = []
    from app.monitoring.connectors import CONNECTOR_METADATA

    for provider_key, meta in sorted(CONNECTOR_METADATA.items()):
        providers.append(
            {
                "provider_key": provider_key,
                "label": meta.label,
                "integration_types": list(meta.integration_types),
                "capabilities": sorted(c.name for c in meta.capabilities),
                "supported": connector_registry.supports(provider_key),
                "configuration_schema": meta.configuration_schema,
                "credential_schema": meta.credential_schema,
            }
        )
    return {"providers": providers}


@router.get("/integrations", dependencies=[perm_view])
async def list_integrations(
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.execute(
        select(IntegrationConnection)
        .where(IntegrationConnection.organization_id == org_id)
        .order_by(IntegrationConnection.created_at.desc())
    )
    return [_serialize_integration(i) for i in rows.scalars().all()]


@router.post("/integrations", status_code=status.HTTP_201_CREATED, dependencies=[perm_manage])
async def create_integration(
    data: IntegrationCreateRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meta = get_metadata(data.provider_key)
    known_types = meta.integration_types if meta else ()
    if meta is not None and known_types and data.integration_type not in known_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider '{data.provider_key}' does not support integration type '{data.integration_type}'",
        )
    supported = connector_registry.supports(data.provider_key)
    integration = IntegrationConnection(
        organization_id=org_id,
        name=data.name,
        integration_type=data.integration_type,
        provider_key=data.provider_key,
        status=IntegrationStatus.ACTIVE.value if supported else IntegrationStatus.FAILED.value,
        configuration=data.configuration or {},
        created_by=current_user.id,
        created_by_name=current_user.name,
    )
    db.add(integration)
    await db.flush()

    for reference in data.secret_references:
        db.add(
            IntegrationCredential(
                integration_id=integration.id,
                secret_reference=reference,
                status="ACTIVE",
            )
        )
    await db.flush()
    await db.refresh(integration)
    await _audit_event(
        db,
        org_id=org_id,
        action=AUDIT_INTEGRATION_CREATED,
        resource_type="monitoring_integration",
        resource_id=integration.id,
        actor_id=current_user.id,
        metadata_json={"provider_key": integration.provider_key},
    )
    return _serialize_integration(integration)


@router.patch("/integrations/{integration_id}", dependencies=[perm_manage])
async def update_integration(
    integration_id: uuid.UUID,
    data: IntegrationUpdateRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    integration = await _get_integration(db, integration_id, org_id)
    if data.name is not None:
        integration.name = data.name
    if data.configuration is not None:
        integration.configuration = data.configuration
    if data.status is not None:
        integration.status = data.status
    await db.flush()
    await db.refresh(integration)
    await _audit_event(
        db,
        org_id=org_id,
        action=AUDIT_INTEGRATION_UPDATED,
        resource_type="monitoring_integration",
        resource_id=integration.id,
        actor_id=current_user.id,
        metadata_json={"name": integration.name},
    )
    return _serialize_integration(integration)


@router.delete("/integrations/{integration_id}", dependencies=[perm_manage])
async def delete_integration(
    integration_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    integration = await _get_integration(db, integration_id, org_id)
    bound = await db.scalar(
        select(ObligationMonitoring.id).where(
            ObligationMonitoring.integration_id == integration.id,
            ObligationMonitoring.status.in_(["DRAFT", "ACTIVE", "PAUSED"]),
        )
    )
    if bound is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Integration is referenced by active monitoring rules; disable the rules first",
        )
    await db.delete(integration)
    await db.flush()
    await _audit_event(
        db,
        org_id=org_id,
        action=AUDIT_INTEGRATION_DISCONNECTED,
        resource_type="monitoring_integration",
        resource_id=integration.id,
        actor_id=current_user.id,
        metadata_json={"name": integration.name},
    )
    return {"id": str(integration.id), "status": "deleted"}


@router.post("/integrations/{integration_id}/credentials", status_code=status.HTTP_201_CREATED, dependencies=[perm_manage])
async def add_credential(
    integration_id: uuid.UUID,
    data: CredentialCreateRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    integration = await _get_integration(db, integration_id, org_id)
    if not (
        data.secret_reference.startswith("env://")
        or data.secret_reference.startswith("secretman://")
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="secret_reference must start with 'env://' or 'secretman://'",
        )
    credential = IntegrationCredential(
        integration_id=integration.id,
        secret_reference=data.secret_reference,
        status="ACTIVE",
        expires_at=data.expires_at,
    )
    db.add(credential)
    await db.flush()
    await db.refresh(credential)
    await _audit_event(
        db,
        org_id=org_id,
        action=AUDIT_INTEGRATION_CREDENTIAL_ROTATED,
        resource_type="monitoring_integration",
        resource_id=integration.id,
        actor_id=current_user.id,
        metadata_json={"credential_id": str(credential.id), "status": credential.status},
    )
    return {
        "id": str(credential.id),
        "integration_id": str(credential.integration_id),
        "secret_reference": credential.secret_reference,
        "status": credential.status,
        "expires_at": credential.expires_at,
        "created_at": credential.created_at,
    }


@router.get("/integrations/{integration_id}/credentials", dependencies=[perm_view])
async def list_credentials(
    integration_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Return credential metadata only — the referenced value is never
    exposed through the API (spec 3.15.6)."""
    integration = await _get_integration(db, integration_id, org_id)
    rows = await db.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.integration_id == integration.id
        )
    )
    return [
        {
            "id": str(c.id),
            "integration_id": str(c.integration_id),
            "secret_reference": c.secret_reference,
            "status": c.status,
            "expires_at": c.expires_at,
            "last_rotated_at": c.last_rotated_at,
        }
        for c in rows.scalars().all()
    ]


@router.post("/integrations/{integration_id}/test", dependencies=[perm_manage])
async def test_integration(
    integration_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    integration = await _get_integration(db, integration_id, org_id)
    try:
        credentials = await get_active_credentials(db, integration.id)
    except MonitoringError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Credential resolution failed: {exc}",
        )
    try:
        connector = connector_registry.create(
            integration.provider_key,
            credentials=credentials,
            configuration=integration.configuration or {},
        )
        ok = await connector.validate_connection()
    except MonitoringError as exc:
        health = await db.get(IntegrationHealth, integration.id)
        if health is None:
            health = IntegrationHealth(
                integration_id=integration.id,
                last_failure_at=datetime.now(timezone.utc),
                consecutive_failures=1,
                last_error_code=type(exc).__name__,
                last_error=str(exc),
            )
            db.add(health)
        else:
            health.last_failure_at = datetime.now(timezone.utc)
            health.consecutive_failures += 1
            health.last_error_code = type(exc).__name__
            health.last_error = str(exc)
        integration.status = IntegrationStatus.DEGRADED.value
        await db.flush()
        await _audit_event(
            db,
            org_id=org_id,
            action=AUDIT_INTEGRATION_AUTH_FAILED,
            resource_type="monitoring_integration",
            resource_id=integration.id,
            actor_id=current_user.id,
            metadata_json={"error": str(exc)[:1000], "error_code": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Connection test failed: {exc}",
        )
    finally:
        pass

    if ok:
        integration.status = IntegrationStatus.ACTIVE.value
        health = await db.get(IntegrationHealth, integration.id)
        if health is None:
            health = IntegrationHealth(
                integration_id=integration.id,
                last_success_at=datetime.now(timezone.utc),
                consecutive_failures=0,
            )
            db.add(health)
        else:
            health.last_success_at = datetime.now(timezone.utc)
            health.consecutive_failures = 0
            health.last_error_code = None
            health.last_error = None
    else:
        integration.status = IntegrationStatus.DEGRADED.value
    await db.flush()
    await _audit_event(
        db,
        org_id=org_id,
        action=AUDIT_INTEGRATION_CONNECTED if ok else AUDIT_INTEGRATION_AUTH_FAILED,
        resource_type="monitoring_integration",
        resource_id=integration.id,
        actor_id=current_user.id,
        metadata_json={"ok": ok},
    )
    return {"integration_id": str(integration.id), "ok": ok}


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

@router.get("/rules", dependencies=[perm_view])
async def list_rules(
    obligation_id: uuid.UUID | None = None,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    query = select(ObligationMonitoring).where(
        ObligationMonitoring.organization_id == org_id
    )
    if obligation_id is not None:
        query = query.where(ObligationMonitoring.obligation_id == obligation_id)
    rows = await db.execute(query.order_by(ObligationMonitoring.created_at.desc()))
    return [_serialize_rule(r) for r in rows.scalars().all()]


@router.post("/rules", status_code=status.HTTP_201_CREATED, dependencies=[perm_rules])
async def create_rule(
    data: RuleCreateRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models.agreement import AgreementVersion
    from app.models.obligation import Obligation

    integration = await _get_integration(db, data.integration_id, org_id)
    obligation = await db.get(Obligation, data.obligation_id)
    if obligation is None or obligation.organization_id != org_id:
        raise _not_found()
    version = await db.scalar(
        select(AgreementVersion).where(
            AgreementVersion.id == data.source_version_id,
            AgreementVersion.agreement_id == obligation.agreement_id,
        )
    )
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source_version_id must reference an agreement version of the obligation's agreement",
        )

    now = datetime.now(timezone.utc)
    next_run = None
    if data.status == "ACTIVE":
        try:
            next_run = compute_next_run(data.schedule_definition, now)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid schedule_definition: {exc}",
            )

    rule = ObligationMonitoring(
        organization_id=org_id,
        obligation_id=data.obligation_id,
        source_version_id=data.source_version_id,
        integration_id=integration.id,
        status=data.status,
        pause_reason=None if data.status == "ACTIVE" else None,
        query_definition=data.query_definition,
        evaluation_definition=data.evaluation_definition,
        schedule_definition=data.schedule_definition,
        automation=data.automation,
        next_run_at=next_run,
        created_by=current_user.id,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    await _audit_event(
        db,
        org_id=org_id,
        action=AUDIT_MONITORING_CREATED,
        resource_type="monitoring_rule",
        resource_id=rule.id,
        actor_id=current_user.id,
        metadata_json={
            "obligation_id": str(rule.obligation_id),
            "integration_id": str(rule.integration_id),
            "status": rule.status,
        },
    )
    return _serialize_rule(rule)


@router.get("/rules/{rule_id}", dependencies=[perm_view])
async def get_rule(
    rule_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    rule = await _get_rule(db, rule_id, org_id)
    return _serialize_rule(rule)


@router.patch("/rules/{rule_id}", dependencies=[perm_rules])
async def update_rule(
    rule_id: uuid.UUID,
    data: RuleUpdateRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await _get_rule(db, rule_id, org_id)
    if rule.status in ("PAUSED", "DISABLED"):
        pass  # definitions can still be maintained while paused/disabled
    changed = False
    for field in ("query_definition", "evaluation_definition", "schedule_definition", "automation"):
        value = getattr(data, field)
        if value is not None:
            setattr(rule, field, value)
            changed = True
    if changed and rule.status == "ACTIVE":
        now = datetime.now(timezone.utc)
        try:
            rule.next_run_at = compute_next_run(rule.schedule_definition, now)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid schedule_definition: {exc}",
            )
    await db.flush()
    await db.refresh(rule)
    return _serialize_rule(rule)


@router.post("/rules/{rule_id}/activate", dependencies=[perm_rules])
async def activate_rule(
    rule_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await _get_rule(db, rule_id, org_id)
    if rule.status == "ACTIVE":
        return _serialize_rule(rule)
    now = datetime.now(timezone.utc)
    try:
        next_run = compute_next_run(rule.schedule_definition, now)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid schedule_definition: {exc}",
        )
    rule.status = "ACTIVE"
    rule.pause_reason = None
    rule.next_run_at = next_run
    await db.flush()
    await db.refresh(rule)
    await _audit_event(
        db,
        org_id=org_id,
        action=AUDIT_MONITORING_ACTIVATED,
        resource_type="monitoring_rule",
        resource_id=rule.id,
        actor_id=current_user.id,
        metadata_json={"obligation_id": str(rule.obligation_id)},
    )
    return _serialize_rule(rule)


@router.post("/rules/{rule_id}/pause", dependencies=[perm_rules])
async def pause_rule(
    rule_id: uuid.UUID,
    pause_reason: str | None = None,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await _get_rule(db, rule_id, org_id)
    if rule.status == "DISABLED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Rule is DISABLED; cannot pause",
        )
    rule.status = "PAUSED"
    rule.pause_reason = pause_reason or PauseReason.MANUAL.value
    rule.next_run_at = None
    await db.flush()
    await db.refresh(rule)
    await _audit_event(
        db,
        org_id=org_id,
        action=AUDIT_MONITORING_PAUSED,
        resource_type="monitoring_rule",
        resource_id=rule.id,
        actor_id=current_user.id,
        metadata_json={"pause_reason": rule.pause_reason},
    )
    return _serialize_rule(rule)


@router.post("/rules/{rule_id}/disable", dependencies=[perm_rules])
async def disable_rule(
    rule_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await _get_rule(db, rule_id, org_id)
    rule.status = "DISABLED"
    rule.pause_reason = None
    rule.next_run_at = None
    await db.flush()
    await db.refresh(rule)
    await _audit_event(
        db,
        org_id=org_id,
        action=AUDIT_MONITORING_DISABLED,
        resource_type="monitoring_rule",
        resource_id=rule.id,
        actor_id=current_user.id,
        metadata_json={"obligation_id": str(rule.obligation_id)},
    )
    return _serialize_rule(rule)


@router.post("/rules/{rule_id}/run", response_model=RunRuleResponse, dependencies=[perm_rules])
async def run_rule_now(
    rule_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await _get_rule(db, rule_id, org_id)
    if rule.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only ACTIVE rules can be run manually; activate or reactivate first",
        )
    try:
        evaluation = await run_obligation_monitoring(db, monitoring=rule)
    except MonitoringError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Monitoring run failed: {exc}",
        )
    await db.flush()
    return RunRuleResponse(evaluation_id=evaluation.id, result=evaluation.result)


@router.get("/rules/{rule_id}/evaluations", dependencies=[perm_view])
async def list_evaluations(
    rule_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    rule = await _get_rule(db, rule_id, org_id)
    rows = await db.execute(
        select(MonitoringEvaluation)
        .where(MonitoringEvaluation.monitoring_id == rule.id)
        .order_by(MonitoringEvaluation.evaluated_at.desc())
        .limit(200)
    )
    return [_serialize_evaluation(e) for e in rows.scalars().all()]


@router.get("/rules/{rule_id}/observations", dependencies=[perm_view_data])
async def list_observations(
    rule_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    rule = await _get_rule(db, rule_id, org_id)
    rows = await db.execute(
        select(ExternalObservationRecord)
        .where(ExternalObservationRecord.monitoring_id == rule.id)
        .order_by(ExternalObservationRecord.observed_at.desc())
        .limit(200)
    )
    return [
        {
            "id": str(o.id),
            "integration_id": str(o.integration_id),
            "monitoring_id": str(o.monitoring_id),
            "external_id": o.external_id,
            "resource_type": o.resource_type,
            "observed_at": o.observed_at,
            "payload": o.payload,
            "payload_hash": o.payload_hash,
            "status": o.status,
        }
        for o in rows.scalars().all()
    ]


@router.get("/rules/{rule_id}/evidence", dependencies=[perm_view])
async def list_evidence(
    rule_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Evidence records backing the rule's evaluations (spec 3.15.37-38).

    Returns integrity metadata + the value reference only — raw observation
    payloads remain behind the stricter ``monitoring.view_data`` permission.
    """
    rule = await _get_rule(db, rule_id, org_id)
    rows = await db.execute(
        select(MonitoringEvidence)
        .where(MonitoringEvidence.monitoring_id == rule.id)
        .order_by(MonitoringEvidence.received_at.desc())
        .limit(200)
    )
    return [
        {
            "id": str(e.id),
            "monitoring_id": str(e.monitoring_id),
            "integration_id": str(e.integration_id),
            "monitoring_run_id": str(e.monitoring_run_id) if e.monitoring_run_id else None,
            "obligation_id": str(e.obligation_id) if e.obligation_id else None,
            "evidence_type": e.evidence_type,
            "source": e.source,
            "source_identifier": e.source_identifier,
            "observed_at": e.observed_at,
            "received_at": e.received_at,
            "payload_hash": e.payload_hash,
            "value": e.value,
            "attached_to_obligation": e.attached_to_obligation,
        }
        for e in rows.scalars().all()
    ]


@router.get("/exceptions", dependencies=[perm_view])
async def list_exceptions(
    open_only: bool = True,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    query = select(MonitoringException).where(
        MonitoringException.organization_id == org_id
    )
    if open_only:
        query = query.where(MonitoringException.status == "OPEN")
    rows = await db.execute(query.order_by(MonitoringException.created_at.desc()).limit(200))
    return [
        {
            "id": str(e.id),
            "monitoring_id": str(e.monitoring_id),
            "evaluation_id": str(e.evaluation_id),
            "status": e.status,
            "reason": e.reason,
            "resolved_at": e.resolved_at,
            "resolution_comment": e.resolution_comment,
        }
        for e in rows.scalars().all()
    ]


@router.post("/exceptions/{exception_id}/resolve", dependencies=[perm_rules])
async def resolve_exception(
    exception_id: uuid.UUID,
    comment: str = "",
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    exception = await db.get(MonitoringException, exception_id)
    if exception is None or exception.organization_id != org_id:
        raise _not_found()
    exception.status = "RESOLVED"
    exception.resolved_by = current_user.id
    exception.resolved_at = datetime.now(timezone.utc)
    exception.resolution_comment = comment or None
    await db.flush()
    await _audit_event(
        db,
        org_id=org_id,
        action=AUDIT_EXCEPTION_RESOLVED,
        resource_type="monitoring_exception",
        resource_id=exception.id,
        actor_id=current_user.id,
        metadata_json={"monitoring_id": str(exception.monitoring_id), "comment": comment or None},
    )
    return {
        "id": str(exception.id),
        "status": exception.status,
        "resolved_at": exception.resolved_at,
    }


@router.get("/dashboard", dependencies=[perm_view])
async def monitoring_dashboard(
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Summary used by the monitoring surface (spec 3.15.40)."""
    rules = list(
        (
            await db.execute(
                select(ObligationMonitoring).where(
                    ObligationMonitoring.organization_id == org_id
                )
            )
        )
        .scalars()
        .all()
    )
    status_counts = {"DRAFT": 0, "ACTIVE": 0, "PAUSED": 0, "DISABLED": 0}
    for rule in rules:
        status_counts[rule.status] = status_counts.get(rule.status, 0) + 1

    open_exceptions = (
        await db.scalar(
            select(MonitoringException.id).where(
                MonitoringException.organization_id == org_id,
                MonitoringException.status == "OPEN",
            ).limit(1)
        )
    ) is not None

    last_evaluation = await db.execute(
        select(MonitoringEvaluation)
        .where(MonitoringEvaluation.organization_id == org_id)
        .order_by(MonitoringEvaluation.evaluated_at.desc())
        .limit(1)
    )
    last_evaluation = last_evaluation.scalar_one_or_none()

    return {
        "rules_total": len(rules),
        "rules_by_status": status_counts,
        "monitoring_active": status_counts["ACTIVE"],
        "exceptions_open": open_exceptions,
        "last_evaluation_at": getattr(last_evaluation, "evaluated_at", None) if last_evaluation else None,
        "last_evaluation_result": getattr(last_evaluation, "result", None) if last_evaluation else None,
    }


# --------------------------------------------------------------------------
# Inbound webhook (spec 3.15.28-30): mirror of the billing webhook security
# chain — signature verification, timestamp freshness, replay dedup.
# --------------------------------------------------------------------------

@webhook_router.post("/integrations/{integration_id}/webhook")
async def monitoring_webhook(
    integration_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()

    integration_row = await db.get(IntegrationConnection, integration_id)
    if integration_row is None:
        raise _not_found()

    webhook_secret = None
    configuration = integration_row.configuration or {}
    if configuration.get("webhook_secret"):
        try:
            reference = configuration["webhook_secret"]
            if reference.startswith("env://"):
                import os

                webhook_secret = os.environ.get(reference.split("://", 1)[1])
        except Exception:  # noqa: BLE001
            webhook_secret = None
    if not webhook_secret and settings.monitoring_webhook_secret:
        webhook_secret = settings.monitoring_webhook_secret.get_secret_value()
    if not webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Monitoring webhook is not configured for this integration",
        )

    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except ValueError:
        await _audit_event(
            db,
            org_id=integration_row.organization_id,
            action=AUDIT_OBSERVATION_REJECTED,
            resource_type="monitoring_integration",
            resource_id=integration_row.id,
            metadata_json={"reason": "malformed_payload"},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed webhook payload",
        )

    signature = request.headers.get("x-monitoring-signature", "")
    expected = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        await _audit_event(
            db,
            org_id=integration_row.organization_id,
            action=AUDIT_OBSERVATION_REJECTED,
            resource_type="monitoring_integration",
            resource_id=integration_row.id,
            metadata_json={"reason": "invalid_signature", "has_signature": bool(signature)},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    provider_event_id = str(payload.get("event_id") or payload.get("id") or "")
    payload_hash = hash_payload(payload)
    from app.monitoring.models import MonitoringWebhookEvent

    duplicate = await db.scalar(
        select(MonitoringWebhookEvent.id).where(
            MonitoringWebhookEvent.integration_id == integration_id,
            MonitoringWebhookEvent.payload_hash == payload_hash,
        )
    )
    if duplicate is not None:
        return WebhookAck(received=True, duplicate=True)

    try:
        received_at = ensure_aware_utc(payload.get("received_at") or payload.get("timestamp"))
        await verify_webhook_time_delivery(
            received_at,
            datetime.now(timezone.utc),
            max_skew_seconds=settings.monitoring_webhook_max_skew_seconds,
        )
    except WebhookVerificationError as exc:
        await _audit_event(
            db,
            org_id=integration_row.organization_id,
            action=AUDIT_OBSERVATION_REJECTED,
            resource_type="monitoring_integration",
            resource_id=integration_row.id,
            metadata_json={"reason": "stale_delivery", "detail": str(exc)[:1000]},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )

    observations = await ingest_webhook_observations(
        db,
        integration_id=integration_id,
        payload=payload,
        provider_event_id=provider_event_id or None,
    )
    await db.flush()
    return WebhookAck(received=True, duplicate=False, observations=observations)