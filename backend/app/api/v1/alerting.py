"""
Alerting API Endpoints.

Provides endpoints for:
  - Viewing alert history and statistics
  - Testing Slack/PagerDuty connectivity
  - Managing alert provider configuration
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.dependencies.auth import get_current_user
from app.models.user import User
from app.services.alerting_service import (
    AlertSeverity,
    get_alerting_service,
)

router = APIRouter(prefix="/alerting", tags=["Alerting"])


# ── Response Models ──────────────────────────────────────────────────────────

class AlertProviderStatus(BaseModel):
    name: str
    configured: bool


class AlertStatsResponse(BaseModel):
    total_alerts: int
    last_24h: int
    by_severity: dict[str, int]
    providers: list[str]
    enabled: bool


class AlertHistoryEntry(BaseModel):
    title: str
    message: str
    severity: str
    source: str
    category: str
    details: dict = {}
    runbook_url: Optional[str] = None
    timestamp: str
    provider_results: dict[str, bool] = {}
    logged_at: str


class TestAlertRequest(BaseModel):
    provider: str  # "slack", "pagerduty", or "all"
    severity: str = "info"


class TestAlertResponse(BaseModel):
    success: bool
    message: str
    provider_results: dict[str, bool] = {}


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/providers", response_model=list[AlertProviderStatus])
async def list_providers(
    current_user: User = Depends(get_current_user),
):
    """List configured alerting providers and their status."""
    service = get_alerting_service()
    all_providers = ["slack", "pagerduty"]
    configured = set(service.providers)

    return [
        AlertProviderStatus(name=p, configured=(p in configured))
        for p in all_providers
    ]


@router.get("/stats", response_model=AlertStatsResponse)
async def get_alert_stats(
    current_user: User = Depends(get_current_user),
):
    """Get alerting statistics (counts, severity breakdown)."""
    service = get_alerting_service()
    return AlertStatsResponse(**service.get_alert_stats())


@router.get("/history", response_model=list[AlertHistoryEntry])
async def get_alert_history(
    limit: int = Query(50, le=200),
    severity: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    """
    Get recent alert history.

    Query Parameters:
        - limit: Max entries (default 50)
        - severity: Filter by severity (info, warning, critical)
        - category: Filter by category (migration, compliance, etc.)
    """
    service = get_alerting_service()

    sev = None
    if severity:
        try:
            sev = AlertSeverity(severity)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid severity: {severity}. Use: info, warning, critical",
            )

    return service.get_alert_history(
        limit=limit,
        severity=sev,
        category=category,
    )


@router.post("/test", response_model=TestAlertResponse)
async def test_alert(
    request: TestAlertRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Send a test alert to verify provider connectivity.

    Body:
        - provider: "slack", "pagerduty", or "all"
        - severity: "info", "warning", or "critical" (default: info)
    """
    service = get_alerting_service()

    try:
        sev = AlertSeverity(request.severity)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid severity: {request.severity}",
        )

    # Check provider is available
    if request.provider != "all" and request.provider not in service.providers:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Provider '{request.provider}' is not configured. "
                f"Available: {service.providers or 'none'}"
            ),
        )

    from app.services.alerting_service import Alert

    alert = Alert(
        title="Test Alert",
        message=(
            f"This is a test alert from ContractOS. "
            f"Sent by {current_user.email}."
        ),
        severity=sev,
        source="contractos-test",
        category="test",
        details={
            "triggered_by": current_user.email,
            "test": True,
        },
    )

    result = service.send_alert(alert)

    return TestAlertResponse(
        success=result.successful > 0,
        message=(
            f"Alert delivered to {result.successful}/{result.total_providers} "
            f"provider(s)"
        ),
        provider_results=result.provider_results,
    )


@router.get("/config")
async def get_alerting_config(
    current_user: User = Depends(get_current_user),
):
    """
    Get current alerting configuration (safe — no secrets exposed).
    """
    import os

    return {
        "enabled": os.environ.get("ALERTING_ENABLED", "true").lower() == "true",
        "slack": {
            "configured": bool(os.environ.get("SLACK_WEBHOOK_URL")),
            "channel": os.environ.get("SLACK_CHANNEL"),
        },
        "pagerduty": {
            "configured": bool(os.environ.get("PAGERDUTY_ROUTING_KEY")),
        },
    }
