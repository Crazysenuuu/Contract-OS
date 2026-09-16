"""Canary deployment API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from pydantic import BaseModel

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.services.canary_service import CanaryService

router = APIRouter(prefix="/canary", tags=["Canary Deployment"])

# Global canary service instance
_canary_service: Optional[CanaryService] = None


def get_canary_service(db: AsyncSession = None) -> CanaryService:
    """Get or create canary service."""
    global _canary_service
    if _canary_service is None:
        _canary_service = CanaryService(db)
    return _canary_service


# ===== Request Models =====

class CreateDeploymentRequest(BaseModel):
    service_name: str
    current_version: str
    canary_version: str
    traffic_percentage: float = 5.0
    stages: Optional[List[dict]] = None


class UpdateTrafficRequest(BaseModel):
    percentage: float


class RecordRequestModel(BaseModel):
    is_canary: bool
    latency_ms: float
    success: bool


# ===== Deployment Endpoints =====

@router.post("/deployments")
async def create_deployment(
    request: CreateDeploymentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new canary deployment."""
    service = get_canary_service(db)
    deployment = service.create_deployment(
        service_name=request.service_name,
        current_version=request.current_version,
        canary_version=request.canary_version,
        traffic_percentage=request.traffic_percentage,
        stages=request.stages,
    )
    return {
        "deployment_id": deployment.deployment_id,
        "service_name": deployment.service_name,
        "canary_version": deployment.canary_version,
        "traffic_percentage": deployment.traffic_percentage,
        "status": deployment.status,
    }


@router.get("/deployments")
async def list_deployments(
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all canary deployments."""
    service = get_canary_service(db)
    deployments = service.list_deployments(status=status)
    return [{
        "deployment_id": d.deployment_id,
        "service_name": d.service_name,
        "current_version": d.current_version,
        "canary_version": d.canary_version,
        "traffic_percentage": d.traffic_percentage,
        "status": d.status,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    } for d in deployments]


@router.get("/deployments/{deployment_id}")
async def get_deployment(
    deployment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get deployment details."""
    service = get_canary_service(db)
    deployment = service.get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    
    return {
        "deployment_id": deployment.deployment_id,
        "service_name": deployment.service_name,
        "current_version": deployment.current_version,
        "canary_version": deployment.canary_version,
        "traffic_percentage": deployment.traffic_percentage,
        "status": deployment.status,
        "current_stage": deployment.current_stage,
        "stages": deployment.stages,
        "created_at": deployment.created_at.isoformat() if deployment.created_at else None,
        "updated_at": deployment.updated_at.isoformat() if deployment.updated_at else None,
    }


@router.put("/deployments/{deployment_id}/traffic")
async def update_traffic(
    deployment_id: str,
    request: UpdateTrafficRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update canary traffic percentage."""
    service = get_canary_service(db)
    try:
        deployment = service.update_traffic_percentage(deployment_id, request.percentage)
        return {
            "deployment_id": deployment.deployment_id,
            "traffic_percentage": deployment.traffic_percentage,
            "status": deployment.status,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/deployments/{deployment_id}/promote")
async def promote_canary(
    deployment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Promote canary to stable (100% traffic)."""
    service = get_canary_service(db)
    try:
        deployment = service.promote_canary(deployment_id)
        return {
            "deployment_id": deployment.deployment_id,
            "status": deployment.status,
            "traffic_percentage": deployment.traffic_percentage,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/deployments/{deployment_id}/rollback")
async def rollback_canary(
    deployment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Rollback canary deployment."""
    service = get_canary_service(db)
    try:
        deployment = service.rollback_canary(deployment_id)
        return {
            "deployment_id": deployment.deployment_id,
            "status": deployment.status,
            "traffic_percentage": deployment.traffic_percentage,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/deployments/{deployment_id}/pause")
async def pause_deployment(
    deployment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Pause canary deployment."""
    service = get_canary_service(db)
    try:
        deployment = service.pause_deployment(deployment_id)
        return {
            "deployment_id": deployment.deployment_id,
            "status": deployment.status,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/deployments/{deployment_id}/resume")
async def resume_deployment(
    deployment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Resume canary deployment."""
    service = get_canary_service(db)
    try:
        deployment = service.resume_deployment(deployment_id)
        return {
            "deployment_id": deployment.deployment_id,
            "status": deployment.status,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ===== Traffic Routing =====

@router.get("/traffic-rules")
async def get_traffic_rules(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get all traffic routing rules."""
    service = get_canary_service(db)
    return service.get_traffic_rules()


@router.get("/route")
async def route_request(
    service_name: str,
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Route a request to canary or stable version."""
    service = get_canary_service(db)
    version = service.route_request(service_name, user_id)
    return {
        "service": service_name,
        "version": version or "stable",
        "is_canary": version is not None and version != "stable",
    }


# ===== Metrics =====

@router.post("/deployments/{deployment_id}/metrics")
async def record_metrics(
    deployment_id: str,
    request: RecordRequestModel,
    db: AsyncSession = Depends(get_db)
):
    """Record request metrics for canary evaluation."""
    service = get_canary_service(db)
    service.record_request(
        deployment_id=deployment_id,
        is_canary=request.is_canary,
        latency_ms=request.latency_ms,
        success=request.success,
    )
    return {"status": "recorded"}


@router.get("/deployments/{deployment_id}/health")
async def check_health(
    deployment_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Check health of canary deployment."""
    service = get_canary_service(db)
    return service.check_canary_health(deployment_id)


@router.get("/health")
async def overall_health(
    db: AsyncSession = Depends(get_db)
):
    """Get overall health of all canary deployments."""
    service = get_canary_service(db)
    return service.get_overall_health()


@router.get("/deployments/{deployment_id}/compare")
async def compare_versions(
    deployment_id: str,
    time_window_minutes: int = Query(60, le=1440),
    db: AsyncSession = Depends(get_db)
):
    """Compare canary vs stable version metrics."""
    service = get_canary_service(db)
    return service.compare_versions(deployment_id, time_window_minutes)


# ===== Auto-promotion =====

@router.post("/deployments/{deployment_id}/check-promotion")
async def check_promotion(
    deployment_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Check if canary should be auto-promoted."""
    service = get_canary_service(db)
    promoted = service.check_auto_promotion(deployment_id)
    deployment = service.get_deployment(deployment_id)
    
    return {
        "deployment_id": deployment_id,
        "promoted": promoted,
        "current_stage": deployment.current_stage if deployment else None,
        "traffic_percentage": deployment.traffic_percentage if deployment else 0,
    }
