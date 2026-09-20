"""Security monitoring API (spec §95).

GET /security/scan  — run all anomaly detection checks
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.user import User
from app.services.security_monitoring import run_all_checks

router = APIRouter(prefix="/security", tags=["Security Monitoring"])


@router.get("/scan")
async def security_scan(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Spec §95 — run anomaly detection checks and return findings."""
    import uuid

    findings = await run_all_checks(db, org_id=uuid.UUID(str(org_id)))
    return {
        "findings": [
            {
                "category": f.category,
                "severity": f.severity,
                "description": f.description,
                "actor_id": f.actor_id,
                "evidence": f.evidence,
                "detected_at": f.detected_at,
            }
            for f in findings
        ],
        "total": len(findings),
        "by_severity": {
            "critical": sum(1 for f in findings if f.severity == "critical"),
            "high": sum(1 for f in findings if f.severity == "high"),
            "medium": sum(1 for f in findings if f.severity == "medium"),
            "low": sum(1 for f in findings if f.severity == "low"),
        },
    }
