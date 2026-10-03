"""Counterparty insurance / bonding expiry notifications.

Counterparty cover — public liability, professional indemnity, workers'
compensation, performance bonds — is a live contractual obligation that
expires on its own calendar, independent of the agreement's own end date.
Missing it means trading uninsured, so this sweep runs daily and notifies
the owning organisation when cover lapses or is about to.

Source of truth is the ``obligations`` table: the clause extractor
already recognises "shall maintain/carry ... insurance" and records the
renewal date as the obligation's ``due_date`` with
``obligation_type='insurance'``. Reusing obligations rather than inventing
a parallel certificate model keeps the expiry date in the same place the
rest of the platform already reads it from, and means an obligation
extracted from a manually entered agreement is covered too.

Idempotency mirrors :mod:`app.services.obligation_reminder_service`: an
``ObligationReminder`` row is written per cycle so a retry after a partial
send does not re-notify.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.notification import NotificationPreference
from app.models.obligation import Obligation, ObligationReminder
from app.models.rbac import OrganizationMember
from app.models.user import User
from app.services.email_service import email_service

logger = logging.getLogger(__name__)

#: Obligations whose due_date falls inside this window are reported as
#: "expiring soon". 30 days is the point at which renewing cover usually
#: requires a broker quote, so it is early enough to act on.
EXPIRY_LOOKAHEAD_DAYS = 30

#: Obligation types that represent insurance / bonding cover. Bonding
#: obligations are extracted with their own type when a guarantee is in
#: play, and both are handled by the same sweep.
INSURANCE_OBLIGATION_TYPES = ("insurance", "bond", "bonding", "warranty")

_OPEN_STATUSES = (
    "upcoming",
    "due",
    "CONFIRMED",
    "ASSIGNED",
    "OPEN",
    "IN_PROGRESS",
)


async def _recipient_emails(db: AsyncSession, organization_id) -> list[str]:
    """Active org members who have not opted out of obligation emails.

    Same contract as the obligation reminder service: an absent preference
    row means "enabled", an explicit False means the member asked not to be
    emailed.
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
    ).all()

    return sorted(
        {email for email, opt_in in rows if opt_in is None or opt_in is True}
    )


async def _load_pending_insurance_obligations(
    db: AsyncSession,
    horizon: date,
) -> list[Obligation]:
    """Insurance obligations that are expired or expiring inside the window."""
    result = await db.execute(
        select(Obligation).where(
            Obligation.obligation_type.in_(INSURANCE_OBLIGATION_TYPES),
            Obligation.status.in_(_OPEN_STATUSES),
            Obligation.due_date.is_not(None),
            Obligation.due_date <= horizon,
        )
    )
    return list(result.scalars().all())


async def _already_notified(db: AsyncSession, obligation_id) -> bool:
    """True when this obligation already had a reminder successfully sent.

    Mirrors :func:`app.services.obligation_reminder_service`: the guard is
    the presence of a ``sent`` reminder for the obligation, independent of
    the reminder type, so a tenant is emailed at most once for a given
    obligation row. A ``failed`` delivery is deliberately *not* treated as
    already-notified, so the next sweep retries it. Renewal is modelled by
    a new obligation (new ``due_date``), which notifies again.
    """
    existing = (
        await db.execute(
            select(ObligationReminder.id).where(
                ObligationReminder.obligation_id == obligation_id,
                ObligationReminder.status == "sent",
            )
        )
    ).scalars().first()
    return existing is not None


def _reminder_type(due: date, today: date) -> str:
    if due < today:
        return "overdue"
    if due == today:
        return "due"
    return "advance"


async def notify_insurance_expiry(
    db: AsyncSession,
    lookahead_days: int = EXPIRY_LOOKAHEAD_DAYS,
) -> dict:
    """Notify organisations about expiring or expired insurance cover.

    Returns counts for beat-schedule logging::

        {"notified": 3, "skipped": 1, "obligations_checked": 4,
         "expired": 1, "expiring_soon": 3}
    """
    today = date.today()
    horizon = today + timedelta(days=lookahead_days)

    obligations = await _load_pending_insurance_obligations(db, horizon)

    notified = 0
    skipped = 0
    expired = 0
    expiring_soon = 0

    for obligation in obligations:
        # An obligation that has already been notified for this cycle is
        # left alone so a re-run after a crash cannot spam the tenant.
        if await _already_notified(db, obligation.id):
            skipped += 1
            continue

        agreement = await db.get(Agreement, obligation.agreement_id)
        if agreement is None or agreement.organization_id is None:
            skipped += 1
            continue

        due = obligation.due_date
        reminder_type = _reminder_type(due, today)
        if reminder_type == "overdue":
            expired += 1
        else:
            expiring_soon += 1

        recipients = await _recipient_emails(db, agreement.organization_id)
        delivered = 0
        for to_email in recipients:
            try:
                email_service.send_obligation_reminder(
                    to_email=to_email,
                    agreement_title=agreement.title,
                    obligation_description=(
                        obligation.description
                        or obligation.title
                        or "Counterparty insurance / bonding cover"
                    ),
                    due_date=str(due),
                    obligation_type=f"insurance ({reminder_type})",
                )
                delivered += 1
            except Exception:
                # One bad recipient must not abort the whole sweep; the
                # remaining organisations still need their notice.
                logger.exception(
                    "Failed to send insurance expiry notice for obligation %s",
                    obligation.id,
                )
                continue

        notified += delivered
        db.add(
            ObligationReminder(
                obligation_id=obligation.id,
                reminder_date=datetime.now(timezone.utc),
                reminder_type=reminder_type,
                status="sent" if delivered else "failed",
            )
        )

    await db.flush()

    result = {
        "notified": notified,
        "skipped": skipped,
        "obligations_checked": len(obligations),
        "expired": expired,
        "expiring_soon": expiring_soon,
    }
    logger.info("Insurance expiry sweep: %s", result)
    return result
