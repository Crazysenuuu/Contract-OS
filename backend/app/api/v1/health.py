"""
Health Check API Endpoints.

Provides system health status including database and migration health.
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.distributed_state import get_state_store
from app.services.migration_monitor import get_migration_monitor

router = APIRouter(tags=["Health"])


def _redis_status() -> dict:
    """Report whether distributed state is really Redis.

    The store degrades to an in-process implementation when Redis is
    unreachable, which keeps requests working but means rate limits and locks
    are enforced separately per replica. Surfacing that matters more than
    reporting a plain "healthy".
    """
    store = get_state_store()
    if store.backend == "redis" and store.ping():
        return {"status": "healthy", "message": "Redis connection OK"}
    if store.backend == "redis":
        return {
            "status": "unhealthy",
            "message": "Redis configured but not responding",
        }
    return {
        "status": "degraded",
        "message": (
            "Distributed state is in-process; rate limits and locks are not "
            "shared across replicas"
        ),
    }


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

    # Check distributed state (Redis)
    redis_status = _redis_status()
    health_status["services"]["redis"] = redis_status
    if redis_status["status"] == "unhealthy":
        health_status["status"] = "degraded"

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

    A database that cannot answer is fatal — the process cannot do useful
    work. Redis is fatal only when it is configured but not responding:
    without a URL the deployment is single-instance and in-process state is
    correct, but a configured-yet-unreachable Redis silently breaks rate
    limiting and locking across every replica.
    """
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        return {"status": "not_ready", "database": str(exc)}, 503

    redis_status = _redis_status()
    if redis_status["status"] == "unhealthy":
        return {"status": "not_ready", "redis": redis_status["message"]}, 503

    return {"status": "ready", "redis": redis_status["status"]}


@router.get("/health/live")
async def liveness_check():
    """
    Liveness check for container orchestrators.

    Returns 200 if service is alive.
    """
    return {"status": "alive"}
