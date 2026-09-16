"""Dashboard service (Panels.txt spec 1.8 + AgreementGen.txt spec §47).

Assembles the user portal dashboard from authorized database records:
pending tasks, unread notifications, recent activity, and the executive
KPI set (TOTAL CONTRACTS / ACTIVE / PENDING APPROVAL / PENDING SIGNATURE /
EXPIRING < 90 DAYS / HIGH RISK / OVERDUE OBLIGATIONS).

There is intentionally no hardcoded users/tasks/notifications data —
everything is read from PostgreSQL scoped to the authenticated user's
organization.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.approval import ApprovalRecord
from app.models.notification import Notification
from app.models.obligation import Obligation
from app.models.user_task import UserActivity, UserTask


async def get_dashboard(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
) -> dict:
    """Assemble the dashboard payload for one user within one organization."""

    # --- Counts ---------------------------------------------------------
    pending_task_count = (
        await db.scalar(
            select(func.count())
            .select_from(UserTask)
            .where(
                UserTask.organization_id == org_id,
                UserTask.user_id == user_id,
                UserTask.status.in_(["pending", "in_progress"]),
            )
        )
    ) or 0

    unread_notification_count = (
        await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.organization_id == org_id,
                Notification.read_at.is_(None),
            )
        )
    ) or 0

    agreement_count = (
        await db.scalar(
            select(func.count())
            .select_from(Agreement)
            .where(Agreement.organization_id == org_id)
        )
    ) or 0

    executed_count = (
        await db.scalar(
            select(func.count())
            .select_from(Agreement)
            .where(
                Agreement.organization_id == org_id,
                Agreement.status == "executed",
            )
        )
    ) or 0

    open_obligation_count = (
        await db.scalar(
            select(func.count())
            .select_from(Obligation)
            .join(Agreement, Agreement.id == Obligation.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                Obligation.status.notin_(["completed", "cancelled"]),
            )
        )
    ) or 0

    # --- Executive KPI set (spec §47) ----------------------------------
    active_count = (
        await db.scalar(
            select(func.count())
            .select_from(Agreement)
            .where(
                Agreement.organization_id == org_id,
                Agreement.status == "active",
            )
        )
    ) or 0

    pending_approval_count = (
        await db.scalar(
            select(func.count())
            .select_from(ApprovalRecord)
            .join(Agreement, Agreement.id == ApprovalRecord.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                ApprovalRecord.status.in_(["pending", "in_progress"]),
            )
        )
    ) or 0

    pending_signature_count = (
        await db.scalar(
            select(func.count())
            .select_from(Agreement)
            .where(
                Agreement.organization_id == org_id,
                Agreement.status.in_(["signing", "sent"]),
            )
        )
    ) or 0

    expiring_soon_count = (
        await db.scalar(
            select(func.count())
            .select_from(Agreement)
            .where(
                Agreement.organization_id == org_id,
                Agreement.status.in_(["active", "expiring"]),
                Agreement.expiry_date.is_not(None),
                Agreement.expiry_date < date.today() + timedelta(days=90),
            )
        )
    ) or 0

    # High risk := agreements with a clause-extracted HIGH/CRITICAL risk.
    from app.models.document_intelligence import ExtractedClause

    high_risk_count = (
        await db.scalar(
            select(func.count(func.distinct(ExtractedClause.agreement_id)))
            .select_from(ExtractedClause)
            .join(Agreement, Agreement.id == ExtractedClause.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                ExtractedClause.risk_score >= 0.5,
            )
        )
    ) or 0

    overdue_obligation_count = (
        await db.scalar(
            select(func.count())
            .select_from(Obligation)
            .join(Agreement, Agreement.id == Obligation.agreement_id)
            .where(
                Agreement.organization_id == org_id,
                Obligation.status.notin_(["completed", "cancelled"]),
                Obligation.due_date.is_not(None),
                Obligation.due_date < date.today(),
            )
        )
    ) or 0

    # --- Lists -----------------------------------------------------------
    task_rows = (
        (
            await db.scalars(
                select(UserTask)
                .where(
                    UserTask.organization_id == org_id,
                    UserTask.user_id == user_id,
                )
                .order_by(UserTask.due_at.asc().nullslast())
                .limit(10)
            )
        ).all()
    )

    notification_rows = (
        (
            await db.scalars(
                select(Notification)
                .where(Notification.organization_id == org_id)
                .order_by(Notification.created_at.desc())
                .limit(10)
            )
        ).all()
    )

    activity_rows = (
        (
            await db.scalars(
                select(UserActivity)
                .where(
                    UserActivity.organization_id == org_id,
                    UserActivity.user_id == user_id,
                )
                .order_by(UserActivity.created_at.desc())
                .limit(20)
            )
        ).all()
    )

    return {
        "user_id": str(user_id),
        "organization_id": str(org_id),
        "counts": {
            "pending_tasks": pending_task_count,
            "unread_notifications": unread_notification_count,
            "agreements": agreement_count,
            "executed_agreements": executed_count,
            "open_obligations": open_obligation_count,
        },
        "kpis": {
            "total_contracts": agreement_count,
            "active": active_count,
            "pending_approval": pending_approval_count,
            "pending_signature": pending_signature_count,
            "expiring_soon": expiring_soon_count,
            "high_risk": high_risk_count,
            "overdue_obligations": overdue_obligation_count,
        },
        "tasks": [
            {
                "id": str(t.id),
                "title": t.title,
                "description": t.description,
                "status": t.status,
                "priority": t.priority,
                "task_type": t.task_type,
                "agreement_id": str(t.agreement_id) if t.agreement_id else None,
                "due_at": t.due_at.isoformat() if t.due_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in task_rows
        ],
        "notifications": [
            {
                "id": str(n.id),
                "notification_type": n.notification_type,
                "subject": n.subject,
                "status": n.status,
                "agreement_id": str(n.agreement_id) if n.agreement_id else None,
                "read": n.read_at is not None,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notification_rows
        ],
        "recent_activity": [
            {
                "id": str(a.id),
                "action": a.action,
                "resource_type": a.resource_type,
                "resource_id": str(a.resource_id) if a.resource_id else None,
                "summary": a.summary,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in activity_rows
        ],
    }


async def record_activity(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    action: str,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    summary: str | None = None,
    metadata_: dict | None = None,
) -> UserActivity:
    """Append a row to the user's activity feed."""
    entry = UserActivity(
        organization_id=organization_id,
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        metadata_=metadata_,
    )
    db.add(entry)
    return entry