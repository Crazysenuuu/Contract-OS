"""
Health Check API Endpoints.

Provides system health status including database and migration health.
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.migration_monitor import get_migration_monitor

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Basic health check endpoint.

    Returns system status including:
    - API status
    - Database connectivity
    - Migration health
    """
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {},
    }

    # Check database
    try:
        result = await db.execute(text("SELECT 1"))
        health_status["services"]["database"] = {
            "status": "healthy",
            "message": "PostgreSQL connection OK",
        }
    except Exception as e:
        health_status["status"] = "degraded"
        health_status["services"]["database"] = {
            "status": "unhealthy",
            "message": str(e),
        }

    # Check migration health
    monitor = get_migration_monitor()
    migration_health = monitor.get_health_status()
    health_status["services"]["migrations"] = {
        "status": migration_health["status"],
        "consecutive_failures": migration_health["consecutive_failures"],
        "recent_failures_24h": migration_health["recent_failures"],
    }

    if migration_health["consecutive_failures"] >= 3:
        health_status["status"] = "degraded"

    return health_status


@router.get("/health/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """
    Readiness check for load balancers.

    Returns 200 if service is ready to accept traffic.
    """
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        return {"status": "not_ready"}, 503


@router.get("/health/live")
async def liveness_check():
    """
    Liveness check for container orchestrators.

    Returns 200 if service is alive.
    """
    return {"status": "alive"}
