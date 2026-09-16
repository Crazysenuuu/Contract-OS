"""Obligation reminder notification dispatch (spec: obligation tracking).

Replaces the earlier hardcoded ``demo@contractos.lk`` delivery. For each
obligation that is upcoming or due within the look-ahead window we resolve
the agreement's owning organization members and email every active member
who has not disabled obligation-reminder emails, then record an
``ObligationReminder`` row (status=sent/failed) so reminders are idempotent
and audit-traced.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.notification import NotificationPreference
from app.models.obligation import Obligation, ObligationReminder
from app.models.rbac import OrganizationMember
from app.models.user import User
from app.services.email_service import email_service


async def _recipient_emails(
    db: AsyncSession,
    organization_id,
) -> list[str]:
    """Active org members who have not opted out of obligation emails.

    Members with an explicit preference row are filtered by its value;
    members without a row default to enabled.
    """
    rows = (
        await db.execute(
            select(User.email, NotificationPreference.email_obligation_reminder)
            .join(OrganizationMember, OrganizationMember.user_id == User.id)
            .outerjoin(
                NotificationPreference,
                (NotificationPreference.organization_id == organization_id)
                & (NotificationPreference.user_id == User.id),
            )
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.status == "active",
                User.status == "active",
            )
        )
    )
    emails = [
        email
        for email, opt_in in rows.all()
        if opt_in is None or opt_in is True
    ]
    return sorted(set(emails))


async def dispatch_obligation_reminders(
    db: AsyncSession,
    window_days: int = 7,
) -> dict:
    """Send reminders for obligations due within the window.

    Idempotent: obligations whose reminders were already sent this cycle
    are skipped. Returns counts for reporting/logging.
    """
    horizon = date.today() + timedelta(days=window_days)

    result = await db.execute(
        select(Obligation).where(
            Obligation.status.in_(
                [
                    "upcoming",
                    "due",
                    "CONFIRMED",
                    "ASSIGNED",
                    "OPEN",
                    "IN_PROGRESS",
                ]
            ),
            Obligation.due_date.is_not(None),
            Obligation.due_date <= horizon,
        )
    )
    obligations = result.scalars().all()

    sent = 0
    skipped = 0
    for obligation in obligations:
        already = (
            await db.execute(
                select(ObligationReminder.id).where(
                    ObligationReminder.obligation_id == obligation.id,
                    ObligationReminder.status == "sent",
                )
            )
        ).scalars().first()
        if already is not None:
            skipped += 1
            continue

        agreement = await db.get(Agreement, obligation.agreement_id)
        if agreement is None or agreement.organization_id is None:
            skipped += 1
            continue

        recipients = await _recipient_emails(db, agreement.organization_id)
        delivered = 0
        for to_email in recipients:
            try:
                email_service.send_obligation_reminder(
                    to_email=to_email,
                    agreement_title=agreement.title,
                    obligation_description=obligation.description,
                    due_date=str(obligation.due_date),
                    obligation_type=obligation.obligation_type,
                )
                delivered += 1
            except Exception:
                continue

        sent += delivered
        reminder_type = (
            "overdue"
            if obligation.due_date < date.today()
            else ("due" if obligation.due_date == date.today() else "advance")
        )
        reminder = ObligationReminder(
            obligation_id=obligation.id,
            reminder_date=datetime.now(timezone.utc),
            reminder_type=reminder_type,
            status="sent" if delivered else "failed",
        )
        db.add(reminder)

    await db.flush()
    return {
        "sent": sent,
        "skipped": skipped,
        "obligations_checked": len(obligations),
    }