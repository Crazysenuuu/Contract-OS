"""Feature flag API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.services.feature_flags import (
    FeatureFlagService, FeatureFlag, FlagType, FlagStatus
)

router = APIRouter(prefix="/feature-flags", tags=["Feature Flags"])

# Global feature flag service instance
_feature_flag_service: Optional[FeatureFlagService] = None


def get_feature_flag_service() -> FeatureFlagService:
    """Get or create feature flag service."""
    global _feature_flag_service
    if _feature_flag_service is None:
        _feature_flag_service = FeatureFlagService()
    return _feature_flag_service


# ===== Request Models =====

class CreateFlagRequest(BaseModel):
    name: str
    description: str
    flag_type: str = "boolean"
    enabled: bool = False
    percentage: float = 0.0
    allowed_users: List[str] = []
    allowed_groups: List[str] = []
    denied_users: List[str] = []
    rollout_percentage: float = 0.0
    tags: List[str] = []


class UpdateFlagRequest(BaseModel):
    description: Optional[str] = None
    enabled: Optional[bool] = None
    status: Optional[str] = None
    percentage: Optional[float] = None
    allowed_users: Optional[List[str]] = None
    allowed_groups: Optional[List[str]] = None
    denied_users: Optional[List[str]] = None
    rollout_percentage: Optional[float] = None
    tags: Optional[List[str]] = None


class EvaluateRequest(BaseModel):
    user_id: Optional[str] = None
    user_groups: Optional[List[str]] = None
    context: Optional[dict] = None


class BulkEvaluateRequest(BaseModel):
    user_id: Optional[str] = None
    user_groups: Optional[List[str]] = None


class UserOverrideRequest(BaseModel):
    user_id: str
    enabled: bool


# ===== Flag CRUD Endpoints =====

@router.post("/flags")
async def create_flag(
    request: CreateFlagRequest,
    current_user: User = Depends(get_current_user)
):
    """Create a new feature flag."""
    service = get_feature_flag_service()
    
    try:
        flag = FeatureFlag(
            name=request.name,
            description=request.description,
            flag_type=FlagType(request.flag_type),
            enabled=request.enabled,
            percentage=request.percentage,
            allowed_users=request.allowed_users,
            allowed_groups=request.allowed_groups,
            denied_users=request.denied_users,
            rollout_percentage=request.rollout_percentage,
            tags=request.tags,
            created_by=str(current_user.id),
        )
        service.create_flag(flag)
        
        return {
            "name": flag.name,
            "flag_type": flag.flag_type.value,
            "status": flag.status.value,
            "enabled": flag.enabled,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/flags")
async def list_flags(
    status: Optional[str] = None,
    tag: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """List all feature flags."""
    service = get_feature_flag_service()
    
    status_enum = FlagStatus(status) if status else None
    flags = service.list_flags(status=status_enum, tag=tag)
    
    return [{
        "name": f.name,
        "description": f.description,
        "flag_type": f.flag_type.value,
        "status": f.status.value,
        "enabled": f.enabled,
        "percentage": f.percentage,
        "rollout_percentage": f.rollout_percentage,
        "tags": f.tags,
        "created_at": f.created_at.isoformat() if f.created_at else None,
        "updated_at": f.updated_at.isoformat() if f.updated_at else None,
    } for f in flags]


@router.get("/flags/{flag_name}")
async def get_flag(
    flag_name: str,
    current_user: User = Depends(get_current_user)
):
    """Get a feature flag."""
    service = get_feature_flag_service()
    flag = service.get_flag(flag_name)
    
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")
    
    return {
        "name": flag.name,
        "description": flag.description,
        "flag_type": flag.flag_type.value,
        "status": flag.status.value,
        "enabled": flag.enabled,
        "percentage": flag.percentage,
        "allowed_users": flag.allowed_users,
        "allowed_groups": flag.allowed_groups,
        "denied_users": flag.denied_users,
        "rollout_start": flag.rollout_start.isoformat() if flag.rollout_start else None,
        "rollout_end": flag.rollout_end.isoformat() if flag.rollout_end else None,
        "rollout_percentage": flag.rollout_percentage,
        "tags": flag.tags,
        "created_by": flag.created_by,
        "created_at": flag.created_at.isoformat() if flag.created_at else None,
        "updated_at": flag.updated_at.isoformat() if flag.updated_at else None,
    }


@router.patch("/flags/{flag_name}")
async def update_flag(
    flag_name: str,
    request: UpdateFlagRequest,
    current_user: User = Depends(get_current_user)
):
    """Update a feature flag."""
    service = get_feature_flag_service()
    
    updates = {k: v for k, v in request.dict().items() if v is not None}
    
    # Convert status string to enum
    if "status" in updates:
        updates["status"] = FlagStatus(updates["status"])
    
    try:
        flag = service.update_flag(flag_name, updates)
        return {
            "name": flag.name,
            "status": flag.status.value,
            "enabled": flag.enabled,
            "updated_at": flag.updated_at.isoformat(),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/flags/{flag_name}")
async def delete_flag(
    flag_name: str,
    current_user: User = Depends(get_current_user)
):
    """Delete a feature flag."""
    service = get_feature_flag_service()
    
    if service.delete_flag(flag_name):
        return {"status": "deleted", "name": flag_name}
    else:
        raise HTTPException(status_code=404, detail="Flag not found")


# ===== Flag Operations =====

@router.post("/flags/{flag_name}/enable")
async def enable_flag(
    flag_name: str,
    current_user: User = Depends(get_current_user)
):
    """Enable a feature flag."""
    service = get_feature_flag_service()
    try:
        flag = service.enable_flag(flag_name)
        return {"name": flag.name, "enabled": True, "status": flag.status.value}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/flags/{flag_name}/disable")
async def disable_flag(
    flag_name: str,
    current_user: User = Depends(get_current_user)
):
    """Disable a feature flag."""
    service = get_feature_flag_service()
    try:
        flag = service.disable_flag(flag_name)
        return {"name": flag.name, "enabled": False, "status": flag.status.value}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ===== Evaluation Endpoints =====

@router.post("/flags/{flag_name}/evaluate")
async def evaluate_flag(
    flag_name: str,
    request: EvaluateRequest = EvaluateRequest()
):
    """Evaluate a feature flag."""
    service = get_feature_flag_service()
    result = service.evaluate(
        flag_name,
        user_id=request.user_id,
        user_groups=request.user_groups,
        context=request.context,
    )
    
    return {
        "flag_name": result.flag_name,
        "enabled": result.enabled,
        "variant": result.variant,
        "reason": result.reason,
        "metadata": result.metadata,
    }


@router.post("/evaluate-all")
async def evaluate_all_flags(
    request: BulkEvaluateRequest = BulkEvaluateRequest()
):
    """Evaluate all feature flags for a user."""
    service = get_feature_flag_service()
    results = service.evaluate_all_flags(
        user_id=request.user_id,
        user_groups=request.user_groups,
    )
    return results


@router.get("/enabled")
async def get_enabled_flags(
    user_id: Optional[str] = None,
    user_groups: Optional[str] = None
):
    """Get list of enabled flags for a user."""
    service = get_feature_flag_service()
    groups = user_groups.split(",") if user_groups else None
    enabled = service.get_enabled_flags(user_id=user_id, user_groups=groups)
    return {"enabled_flags": enabled}


# ===== Quick Check Endpoint =====

@router.get("/check/{flag_name}")
async def check_flag(
    flag_name: str,
    user_id: Optional[str] = None,
    user_groups: Optional[str] = None
):
    """Quick check if a flag is enabled."""
    service = get_feature_flag_service()
    groups = user_groups.split(",") if user_groups else None
    enabled = service.is_enabled(flag_name, user_id=user_id, user_groups=groups)
    return {"flag": flag_name, "enabled": enabled}


# ===== User Overrides =====

@router.post("/flags/{flag_name}/overrides")
async def set_user_override(
    flag_name: str,
    request: UserOverrideRequest,
    current_user: User = Depends(get_current_user)
):
    """Set a user-specific override for a flag."""
    service = get_feature_flag_service()
    service.set_user_override(flag_name, request.user_id, request.enabled)
    return {
        "flag_name": flag_name,
        "user_id": request.user_id,
        "enabled": request.enabled,
    }


@router.delete("/flags/{flag_name}/overrides/{user_id}")
async def clear_user_override(
    flag_name: str,
    user_id: str,
    current_user: User = Depends(get_current_user)
):
    """Clear a user-specific override."""
    service = get_feature_flag_service()
    service.clear_user_override(flag_name, user_id)
    return {"status": "cleared", "flag_name": flag_name, "user_id": user_id}


@router.get("/flags/{flag_name}/overrides")
async def get_user_overrides(
    flag_name: str,
    current_user: User = Depends(get_current_user)
):
    """Get all user overrides for a flag."""
    service = get_feature_flag_service()
    overrides = service.get_user_overrides(flag_name)
    return {"flag_name": flag_name, "overrides": overrides}


# ===== Statistics =====

@router.get("/stats")
async def get_flag_stats(
    current_user: User = Depends(get_current_user)
):
    """Get feature flag statistics."""
    service = get_feature_flag_service()
    return service.get_flag_stats()


@router.get("/flags/{flag_name}/history")
async def get_flag_history(
    flag_name: str,
    current_user: User = Depends(get_current_user)
):
    """Get evaluation history for a flag."""
    service = get_feature_flag_service()
    history = service.get_flag_history(flag_name)
    return {"flag_name": flag_name, "history": history}


# ===== Export/Import =====

@router.get("/export")
async def export_flags(
    current_user: User = Depends(get_current_user)
):
    """Export all feature flags."""
    service = get_feature_flag_service()
    return {"flags": service.export_flags()}


@router.post("/import")
async def import_flags(
    flags: List[CreateFlagRequest],
    current_user: User = Depends(get_current_user)
):
    """Import feature flags."""
    service = get_feature_flag_service()
    
    flags_data = [f.dict() for f in flags]
    imported = service.import_flags(flags_data)
    
    return {"imported": imported}
