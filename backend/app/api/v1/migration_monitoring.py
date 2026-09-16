"""
Migration Monitoring API Endpoints.

Provides API access to migration health status, metrics, and event history.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.dependencies.auth import get_current_user
from app.models.user import User
from app.services.migration_monitor import get_migration_monitor

router = APIRouter(prefix="/migration-monitor", tags=["Migration Monitoring"])


# ===== Response Models =====

class MigrationMetricsResponse(BaseModel):
    """Migration metrics response."""
    total_migrations: int
    successful_migrations: int
    failed_migrations: int
    rollbacks: int
    avg_duration_seconds: float
    last_migration_time: Optional[str] = None
    last_failure_time: Optional[str] = None


class MigrationHealthResponse(BaseModel):
    """Migration health status response."""
    status: str
    metrics: MigrationMetricsResponse
    recent_failures: int
    consecutive_failures: int
    last_24h_events: int


class MigrationEventResponse(BaseModel):
    """Migration event response."""
    event_id: str
    event_type: str
    migration_id: str
    migration_name: str
    database_name: str
    started_at: str
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    status: str
    error_message: Optional[str] = None
    tables_affected: int = 0
    rows_affected: int = 0


class PrometheusMetricsResponse(BaseModel):
    """Prometheus-compatible metrics response."""
    content: str
    content_type: str = "text/plain; version=0.0.4; charset=utf-8"


# ===== Endpoints =====

@router.get("/health", response_model=MigrationHealthResponse)
async def get_migration_health(
    current_user: User = Depends(get_current_user),
):
    """
    Get migration health status.

    Returns:
        - Overall health status (healthy/degraded)
        - Key metrics (total, success, failure counts)
        - Recent failure information
    """
    monitor = get_migration_monitor()
    health = monitor.get_health_status()
    return MigrationHealthResponse(**health)


@router.get("/metrics", response_model=MigrationMetricsResponse)
async def get_migration_metrics(
    current_user: User = Depends(get_current_user),
):
    """
    Get detailed migration metrics.

    Returns comprehensive statistics about migration operations.
    """
    monitor = get_migration_monitor()
    metrics = monitor.metrics
    return MigrationMetricsResponse(
        total_migrations=metrics["total_migrations"],
        successful_migrations=metrics["successful_migrations"],
        failed_migrations=metrics["failed_migrations"],
        rollbacks=metrics["rollbacks"],
        avg_duration_seconds=metrics["avg_duration_seconds"],
        last_migration_time=(
            metrics["last_migration_time"].isoformat()
            if metrics["last_migration_time"]
            else None
        ),
        last_failure_time=(
            metrics["last_failure_time"].isoformat()
            if metrics["last_failure_time"]
            else None
        ),
    )


@router.get("/events", response_model=list[MigrationEventResponse])
async def get_migration_events(
    limit: int = Query(50, le=200),
    event_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    """
    Get migration event history.

    Query Parameters:
        - limit: Maximum number of events to return (default: 50)
        - event_type: Filter by type (upgrade, downgrade, rollback)
        - status: Filter by status (success, failed, pending)
    """
    monitor = get_migration_monitor()
    events = monitor.get_event_history(
        limit=limit,
        event_type=event_type,
        status=status,
    )
    return events


@router.get("/slow")
async def get_slow_migrations(
    threshold_seconds: float = Query(60.0, description="Duration threshold"),
    current_user: User = Depends(get_current_user),
):
    """
    Get migrations that exceeded duration threshold.

    Useful for identifying performance issues.
    """
    monitor = get_migration_monitor()
    slow = monitor.get_slow_migrations(threshold_seconds)
    return {
        "threshold_seconds": threshold_seconds,
        "count": len(slow),
        "migrations": slow,
    }


@router.get("/prometheus")
async def get_prometheus_metrics():
    """
    Get metrics in Prometheus format.

    No authentication required for scraping.
    """
    monitor = get_migration_monitor()
    metrics = monitor.export_metrics()

    # Format as Prometheus text
    lines = []
    for key, value in metrics.items():
        lines.append(f"# HELP {key} Migration operation counter")
        lines.append(f"# TYPE {key} counter")
        lines.append(f"{key} {value}")

    content = "\n".join(lines)
    return PrometheusMetricsResponse(content=content)


@router.get("/summary")
async def get_migration_summary(
    current_user: User = Depends(get_current_user),
):
    """
    Get a comprehensive migration summary.

    Returns a human-readable summary of migration status.
    """
    monitor = get_migration_monitor()
    health = monitor.get_health_status()
    metrics = monitor.metrics

    # Calculate success rate
    total = metrics["total_migrations"]
    successful = metrics["successful_migrations"]
    success_rate = (successful / total * 100) if total > 0 else 100

    return {
        "status": health["status"],
        "summary": {
            "total_migrations": total,
            "success_rate": f"{success_rate:.1f}%",
            "avg_duration": f"{metrics['avg_duration_seconds']:.2f}s",
            "rollbacks": metrics["rollbacks"],
            "recent_failures_24h": health["recent_failures"],
        },
        "recommendations": _get_recommendations(metrics, health),
    }


def _get_recommendations(metrics: dict, health: dict) -> list[str]:
    """Generate recommendations based on metrics."""
    recommendations = []

    if health["consecutive_failures"] >= 3:
        recommendations.append(
            "⚠️ Multiple consecutive failures detected. "
            "Review recent migration changes."
        )

    if metrics["rollbacks"] > metrics["successful_migrations"] * 0.1:
        recommendations.append(
            "⚠️ High rollback rate. Consider improving migration testing."
        )

    if metrics["avg_duration_seconds"] > 120:
        recommendations.append(
            "⚠️ Average migration duration is high. "
            "Consider optimizing large table migrations."
        )

    if not recommendations:
        recommendations.append("✅ Migration health looks good!")

    return recommendations
