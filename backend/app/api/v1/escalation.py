"""
Escalation Policy & Alerting Preferences API.

Manages:
  - Escalation policies (CRUD per organization)
  - Escalation incidents (list, acknowledge, resolve)
  - Alerting preferences (per-user notification channels)
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.services.alerting_service import AlertSeverity, get_alerting_service
from app.services.escalation_service import (
    EscalationIncident,
    EscalationLevel,
    EscalationPolicy,
    EscalationStatus,
    EscalationTier,
    get_escalation_service,
)

router = APIRouter(prefix="/escalation", tags=["Escalation"])


# ── Request / Response models ────────────────────────────────────────────────

class EscalationTierBody(BaseModel):
    level: int
    delay_seconds: int = 0
    channels: list[str] = ["slack"]
    notify_roles: list[str] = []
    notify_emails: list[str] = []
    slack_channel: Optional[str] = None


class CreatePolicyRequest(BaseModel):
    name: str
    category: str
    enabled: bool = True
    tiers: list[EscalationTierBody] = []
    min_severity: str = "warning"
    cooldown_seconds: int = 300
    max_alerts_per_hour: int = 20


class UpdatePolicyRequest(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    tiers: Optional[list[EscalationTierBody]] = None
    min_severity: Optional[str] = None
    cooldown_seconds: Optional[int] = None
    max_alerts_per_hour: Optional[int] = None


class AcknowledgeRequest(BaseModel):
    by: str = "user"


class AlertingPreferencesRequest(BaseModel):
    """Per-user alerting channel preferences."""
    slack_enabled: bool = True
    pagerduty_enabled: bool = True
    email_enabled: bool = True
    compliance_alerts: bool = True
    esignature_alerts: bool = True
    migration_alerts: bool = True
    quiet_hours_start: Optional[int] = None   # 0-23
    quiet_hours_end: Optional[int] = None     # 0-23


# ── In-memory user preferences (replace with DB in production) ──────────────

_user_preferences: dict[str, dict] = {}


# ── Policy endpoints ─────────────────────────────────────────────────────────

@router.get("/policies")
async def list_policies(
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
):
    """List all escalation policies for the organization."""
    service = get_escalation_service()
    service.ensure_default_policies(str(org_id))
    policies = service.get_policies(str(org_id))
    return {"policies": [p.to_dict() for p in policies]}


@router.post("/policies")
async def create_policy(
    request: CreatePolicyRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
):
    """Create a new escalation policy."""
    import uuid as _uuid

    service = get_escalation_service()
    policy_id = f"policy-{request.category}-{_uuid.uuid4().hex[:8]}"

    tiers = [
        EscalationTier(
            level=t.level,
            delay_seconds=t.delay_seconds,
            channels=t.channels,
            notify_roles=t.notify_roles,
            notify_emails=t.notify_emails,
            slack_channel=t.slack_channel,
        )
        for t in request.tiers
    ]

    policy = EscalationPolicy(
        id=policy_id,
        organization_id=str(org_id),
        name=request.name,
        category=request.category,
        enabled=request.enabled,
        tiers=tiers,
        min_severity=EscalationLevel(request.min_severity),
        cooldown_seconds=request.cooldown_seconds,
        max_alerts_per_hour=request.max_alerts_per_hour,
    )

    created = service.create_policy(policy)
    return {"policy": created.to_dict()}


@router.get("/policies/{policy_id}")
async def get_policy(
    policy_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get a specific escalation policy."""
    service = get_escalation_service()
    policy = service.get_policy(policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return {"policy": policy.to_dict()}


@router.patch("/policies/{policy_id}")
async def update_policy(
    policy_id: str,
    request: UpdatePolicyRequest,
    current_user: User = Depends(get_current_user),
):
    """Update an escalation policy."""
    service = get_escalation_service()
    updates = request.model_dump(exclude_none=True)

    if "min_severity" in updates:
        updates["min_severity"] = EscalationLevel(updates["min_severity"])
    if "tiers" in updates:
        updates["tiers"] = [t.model_dump() for t in updates["tiers"]]

    policy = service.update_policy(policy_id, updates)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return {"policy": policy.to_dict()}


@router.delete("/policies/{policy_id}")
async def delete_policy(
    policy_id: str,
    current_user: User = Depends(get_current_user),
):
    """Delete an escalation policy."""
    service = get_escalation_service()
    deleted = service.delete_policy(policy_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Policy not found")
    return {"deleted": True}


# ── Incident endpoints ───────────────────────────────────────────────────────

@router.get("/incidents")
async def list_incidents(
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
):
    """List escalation incidents."""
    service = get_escalation_service()
    esc_status = EscalationStatus(status) if status else None
    incidents = service.get_incidents(
        organization_id=str(org_id),
        category=category,
        status=esc_status,
        limit=limit,
    )
    return {"incidents": [i.to_dict() for i in incidents]}


@router.get("/incidents/stats")
async def incident_stats(
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
):
    """Get escalation incident statistics."""
    service = get_escalation_service()
    return service.get_incident_stats(str(org_id))


@router.post("/incidents/{incident_id}/acknowledge")
async def acknowledge_incident(
    incident_id: str,
    request: AcknowledgeRequest,
    current_user: User = Depends(get_current_user),
):
    """Acknowledge an escalation incident."""
    service = get_escalation_service()
    ok = service.acknowledge_incident(incident_id, by=current_user.email)
    if not ok:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Also resolve PagerDuty
    alerting = get_alerting_service()
    alerting.resolve_incident("escalation", incident_id)

    return {"acknowledged": True}


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident(
    incident_id: str,
    current_user: User = Depends(get_current_user),
):
    """Resolve an escalation incident."""
    service = get_escalation_service()
    ok = service.resolve_incident(incident_id, by=current_user.email)
    if not ok:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"resolved": True}


# ── Alerting preferences ─────────────────────────────────────────────────────

@router.get("/preferences")
async def get_alerting_preferences(
    current_user: User = Depends(get_current_user),
):
    """Get the current user's alerting preferences."""
    prefs = _user_preferences.get(str(current_user.id), {
        "slack_enabled": True,
        "pagerduty_enabled": True,
        "email_enabled": True,
        "compliance_alerts": True,
        "esignature_alerts": True,
        "migration_alerts": True,
        "quiet_hours_start": None,
        "quiet_hours_end": None,
    })
    return {"preferences": prefs}


@router.patch("/preferences")
async def update_alerting_preferences(
    request: AlertingPreferencesRequest,
    current_user: User = Depends(get_current_user),
):
    """Update the current user's alerting preferences."""
    _user_preferences[str(current_user.id)] = request.model_dump()
    return {"preferences": request.model_dump()}



