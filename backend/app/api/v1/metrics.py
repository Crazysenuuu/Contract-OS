"""Metrics API endpoints for Prometheus monitoring."""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.services.metrics_service import MetricsService

router = APIRouter(prefix="/metrics", tags=["Monitoring"])

# Global metrics service instance
_metrics_service: Optional[MetricsService] = None


def get_metrics_service(db: AsyncSession) -> MetricsService:
    """Get or create metrics service."""
    global _metrics_service
    if _metrics_service is None:
        _metrics_service = MetricsService(db)
    return _metrics_service


@router.get("/prometheus")
async def prometheus_metrics(
    db: AsyncSession = Depends(get_db)
):
    """Expose metrics in Prometheus format."""
    service = get_metrics_service(db)
    metrics_text = await service.to_prometheus_format()
    return PlainTextResponse(
        content=metrics_text,
        media_type="text/plain; version=0.0.4; charset=utf-8"
    )


@router.get("/health")
async def health_check(
    db: AsyncSession = Depends(get_db)
):
    """Health check endpoint."""
    try:
        # Check database connection
        await db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"

    service = get_metrics_service(db)
    uptime = service.get_gauge("uptime_seconds") or 0

    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        "uptime_seconds": uptime,
        "checks": {
            "database": db_status,
        },
    }


@router.get("/ready")
async def readiness_check(
    db: AsyncSession = Depends(get_db)
):
    """Readiness check endpoint."""
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        return {"status": "not ready"}, 503


@router.get("/live")
async def liveness_check():
    """Liveness check endpoint."""
    return {"status": "alive"}


@router.get("/dashboard")
async def metrics_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get comprehensive metrics dashboard."""
    service = get_metrics_service(db)
    return await service.collect_application_metrics()


@router.get("/summary")
async def metrics_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get metrics summary."""
    service = get_metrics_service(db)
    metrics = await service.collect_application_metrics()

    return {
        "uptime": metrics.get("uptime_seconds", 0),
        "requests": {
            "total": metrics.get("counters", {}).get("api_requests_total", 0),
            "errors": metrics.get("counters", {}).get("api_errors_total", 0),
        },
        "translations": {
            "queue_size": metrics.get("database", {}).get("translation_queue_size", 0),
            "completed_last_hour": metrics.get("translations", {}).get("throughput_last_hour", 0),
        },
        "database": metrics.get("database", {}),
    }


@router.get("/alerts")
async def get_alerts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get current alerts based on metrics."""
    service = get_metrics_service(db)
    metrics = await service.collect_application_metrics()
    alerts = []

    # Check translation queue size
    queue_size = metrics.get("translations", {}).get("queue_pending", 0)
    if queue_size > 100:
        alerts.append({
            "severity": "warning",
            "message": f"Translation queue size is high: {queue_size}",
            "metric": "translation_queue_size",
            "value": queue_size,
            "threshold": 100,
        })

    # Check for failed translations
    failed = metrics.get("translations", {}).get("queue_failed", 0)
    if failed > 10:
        alerts.append({
            "severity": "critical",
            "message": f"High number of failed translations: {failed}",
            "metric": "translation_failed_count",
            "value": failed,
            "threshold": 10,
        })

    # Check API error rate
    errors = metrics.get("counters", {}).get("api_errors_total", 0)
    requests = metrics.get("counters", {}).get("api_requests_total", 0)
    if requests > 0 and (errors / requests) > 0.05:
        alerts.append({
            "severity": "warning",
            "message": f"High API error rate: {errors}/{requests} ({errors/requests*100:.1f}%)",
            "metric": "api_error_rate",
            "value": errors / requests,
            "threshold": 0.05,
        })

    return {
        "alerts": alerts,
        "alert_count": len(alerts),
        "has_critical": any(a["severity"] == "critical" for a in alerts),
    }


@router.post("/increment/{name}")
async def increment_counter(
    name: str,
    value: int = 1,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Increment a custom counter."""
    service = get_metrics_service(db)
    service.increment_counter(name, value)
    return {"status": "ok", "name": name, "value": service.get_counter(name)}


@router.post("/gauge/{name}")
async def set_gauge(
    name: str,
    value: float,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Set a custom gauge."""
    service = get_metrics_service(db)
    service.set_gauge(name, value)
    return {"status": "ok", "name": name, "value": service.get_gauge(name)}
