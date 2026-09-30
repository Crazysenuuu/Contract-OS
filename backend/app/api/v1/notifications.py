"""
Notification & Analytics Export API Endpoints.

View notification history and export analytics data.
"""

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement
from app.models.company_policy import ComplianceReport, PolicyViolation
from app.models.notification import Notification
from app.models.obligation import Obligation
from app.models.user import User
from app.services.notification_tracking import NotificationTracking

router = APIRouter(prefix="/notifications", tags=["Notifications"])
export_router = APIRouter(prefix="/analytics", tags=["Analytics Export"])


# --- Notification Schemas ---

class NotificationResponse(BaseModel):
    id: uuid.UUID
    notification_type: str
    to_email: str
    subject: str
    status: str
    message_id: Optional[str]
    agreement_id: Optional[uuid.UUID]
    metadata_: Optional[dict]
    sent_at: Optional[datetime]
    # When the recipient acknowledged the notification (null = unread).
    # Powers the inbox read/unread state (spec 2.13).
    read_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Notification Endpoints ---

@router.get("", response_model=list[NotificationResponse])
async def list_notifications(
    agreement_id: Optional[uuid.UUID] = None,
    notification_type: Optional[str] = None,
    since: Optional[datetime] = None,
    limit: int = Query(50, le=200),
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List notification history.

    ``since`` (ISO-8601) returns only notifications created after that
    instant — the mobile client's missed-event recovery: while disconnected
    from /ws/notifications it polls this with its last received timestamp
    and replays what it missed (spec 2.02 §33).
    """
    query = select(Notification).where(
        Notification.organization_id == org_id
    )

    if agreement_id:
        query = query.where(Notification.agreement_id == agreement_id)
    if notification_type:
        query = query.where(Notification.notification_type == notification_type)
    if since is not None:
        query = query.where(Notification.created_at > since)

    query = query.order_by(Notification.created_at.desc()).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/unread-count")
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Count unread notifications for the organization (badge source)."""
    result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.organization_id == org_id,
            Notification.read_at.is_(None),
        )
    )
    return {"count": result.scalar() or 0}


@router.post("/mark-all-read")
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Mark all organization notifications as read."""
    from sqlalchemy import update

    unread = select(func.count(Notification.id)).where(
        Notification.organization_id == org_id,
        Notification.read_at.is_(None),
    )
    before = (await db.execute(unread)).scalar() or 0
    if before:
        await db.execute(
            update(Notification)
            .where(
                Notification.organization_id == org_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=datetime.now(timezone.utc))
        )
        await db.flush()
    return {"marked": before}


@router.get("/stats")
async def get_notification_stats(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get notification statistics."""
    result = await db.execute(
        select(
            Notification.notification_type,
            func.count(Notification.id).label("count"),
        )
        .where(Notification.organization_id == org_id)
        .group_by(Notification.notification_type)
    )
    stats = {row[0]: row[1] for row in result.all()}

    total = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.organization_id == org_id
        )
    )
    failed = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.organization_id == org_id,
            Notification.status == "failed",
        )
    )

    return {
        "total": total.scalar() or 0,
        "failed": failed.scalar() or 0,
        "by_type": stats,
    }


# --- Analytics Export Endpoints ---

@export_router.get("/export/agreements")
async def export_agreements_csv(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Export agreements as CSV."""
    result = await db.execute(
        select(Agreement)
        .where(Agreement.organization_id == org_id)
        .order_by(Agreement.created_at.desc())
    )
    agreements = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID",
        "Title",
        "Status",
        "Governing Law",
        "Effective Date",
        "Created At",
    ])

    for a in agreements:
        writer.writerow([
            str(a.id),
            a.title,
            a.status,
            a.governing_law or "",
            str(a.effective_date) if a.effective_date else "",
            a.created_at.isoformat() if a.created_at else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=agreements_export.csv"
        },
    )


@export_router.get("/export/compliance")
async def export_compliance_csv(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Export compliance reports as CSV."""
    result = await db.execute(
        select(ComplianceReport)
        .order_by(ComplianceReport.created_at.desc())
    )
    reports = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Report ID",
        "Agreement ID",
        "Policies Checked",
        "Violations Found",
        "Critical",
        "High",
        "Medium",
        "Low",
        "Compliance Score",
        "Summary",
        "Created At",
    ])

    for r in reports:
        writer.writerow([
            str(r.id),
            str(r.agreement_id),
            r.total_policies_checked,
            r.violations_found,
            r.critical_count,
            r.high_count,
            r.medium_count,
            r.low_count,
            f"{r.compliance_score:.1f}",
            r.summary or "",
            r.created_at.isoformat() if r.created_at else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=compliance_export.csv"
        },
    )


@export_router.get("/export/violations")
async def export_violations_csv(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Export policy violations as CSV."""
    result = await db.execute(
        select(PolicyViolation)
        .order_by(PolicyViolation.created_at.desc())
    )
    violations = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Violation ID",
        "Agreement ID",
        "Policy ID",
        "Type",
        "Description",
        "Severity",
        "Confidence",
        "Reviewer Status",
        "Created At",
    ])

    for v in violations:
        writer.writerow([
            str(v.id),
            str(v.agreement_id),
            str(v.policy_id),
            v.violation_type,
            v.description,
            v.severity,
            f"{v.confidence:.2f}",
            v.reviewer_status,
            v.created_at.isoformat() if v.created_at else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=violations_export.csv"
        },
    )


@export_router.get("/export/obligations")
async def export_obligations_csv(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Export obligations as CSV."""
    result = await db.execute(
        select(Obligation)
        .order_by(Obligation.created_at.desc())
    )
    obligations = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Obligation ID",
        "Agreement ID",
        "Owner Party",
        "Description",
        "Type",
        "Amount",
        "Frequency",
        "Due Date",
        "Status",
        "Clause Reference",
        "Created At",
    ])

    for o in obligations:
        writer.writerow([
            str(o.id),
            str(o.agreement_id),
            o.owner_party,
            o.description,
            o.obligation_type,
            o.amount or "",
            o.frequency or "",
            str(o.due_date) if o.due_date else "",
            o.status,
            o.clause_identifier or "",
            o.created_at.isoformat() if o.created_at else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=obligations_export.csv"
        },
    )


@export_router.get("/summary")
async def get_analytics_summary(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get comprehensive analytics summary for export."""
    # Agreement stats
    total_agreements = await db.execute(
        select(func.count(Agreement.id)).where(
            Agreement.organization_id == org_id
        )
    )
    executed = await db.execute(
        select(func.count(Agreement.id)).where(
            Agreement.organization_id == org_id,
            Agreement.status == "executed",
        )
    )

    # Compliance stats
    compliance_result = await db.execute(
        select(
            func.count(ComplianceReport.id),
            func.avg(ComplianceReport.compliance_score),
            func.sum(ComplianceReport.violations_found),
        )
    )
    compliance_row = compliance_result.one()

    # Violation stats
    violations_result = await db.execute(
        select(
            PolicyViolation.severity,
            func.count(PolicyViolation.id),
        ).group_by(PolicyViolation.severity)
    )
    violation_stats = {row[0]: row[1] for row in violations_result.all()}

    # Obligation stats
    obligation_result = await db.execute(
        select(
            Obligation.status,
            func.count(Obligation.id),
        ).group_by(Obligation.status)
    )
    obligation_stats = {row[0]: row[1] for row in obligation_result.all()}

    return {
        "agreements": {
            "total": total_agreements.scalar() or 0,
            "executed": executed.scalar() or 0,
            "execution_rate": (
                round(
                    (executed.scalar() or 0)
                    / max(total_agreements.scalar() or 1, 1)
                    * 100,
                    1,
                )
            ),
        },
        "compliance": {
            "reports_count": compliance_row[0] or 0,
            "avg_score": round(float(compliance_row[1] or 0), 1),
            "total_violations": compliance_row[2] or 0,
            "by_severity": violation_stats,
        },
        "obligations": obligation_stats,
    }
