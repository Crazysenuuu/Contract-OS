"""Portfolio analytics & intelligence API (spec §3.17.53-56).

GET  /analytics/portfolio          — snapshot-based executive dashboard
POST /analytics/portfolio/aggregate — on-demand aggregation (admin/testing)
GET  /analytics/anomalies          — open anomaly records
GET  /analytics/insights           — current executive insights

All metrics are served from stored snapshots (metrics are snapshots, not
facts — §3.17.3); when no snapshot exists the endpoint says so explicitly
instead of computing untracked numbers on the fly.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_admin, get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.analytics import AnomalyRecord, ExecutiveInsight
from app.models.user import User
from app.services.analytics_service import (
    get_portfolio_dashboard,
    run_daily_aggregation,
)

router = APIRouter(prefix="/analytics", tags=["Portfolio Analytics"])


@router.get("/portfolio")
async def portfolio_dashboard(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Snapshot-based portfolio dashboard with trend, anomalies, insights."""
    return await get_portfolio_dashboard(db, org_id=uuid.UUID(str(org_id)))


@router.post("/portfolio/aggregate")
async def aggregate_now(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
    org_id: str = Depends(get_current_organization_id),
):
    """Run the daily aggregation immediately (admin action / CI hook).

    Mirrors the nightly Celery beat task so deployments can seed snapshots
    without waiting for the scheduler.
    """
    result = await run_daily_aggregation(db)
    await db.commit()
    return {"status": "ok", **result}


@router.get("/anomalies")
async def list_anomalies(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Open anomaly records for the workspace."""
    rows = (
        await db.execute(
            select(AnomalyRecord)
            .where(
                AnomalyRecord.organization_id == uuid.UUID(str(org_id)),
                AnomalyRecord.status == "open",
            )
            .order_by(AnomalyRecord.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return [
        {
            "id": str(a.id),
            "metric_key": a.metric_key,
            "snapshot_date": a.snapshot_date.isoformat(),
            "observed_value": a.observed_value,
            "baseline_mean": a.baseline_mean,
            "baseline_stddev": a.baseline_stddev,
            "deviation": a.deviation,
            "direction": a.direction,
            "status": a.status,
        }
        for a in rows
    ]


@router.get("/insights")
async def list_insights(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Current executive insights with their metric evidence."""
    rows = (
        await db.execute(
            select(ExecutiveInsight)
            .where(
                ExecutiveInsight.organization_id == uuid.UUID(str(org_id)),
                ExecutiveInsight.status == "current",
            )
            .order_by(ExecutiveInsight.insight_date.desc())
            .limit(50)
        )
    ).scalars().all()
    return [
        {
            "id": str(i.id),
            "insight_date": i.insight_date.isoformat(),
            "category": i.category,
            "title": i.title,
            "body": i.body,
            "severity": i.severity,
            "generated_by": i.generated_by,
            "evidence": i.evidence,
        }
        for i in rows
    ]
