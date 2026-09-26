"""Notification dispatch hardening (spec §3.10).

Adds the persistence layer the plain sender lacks:
- per-channel delivery records with retry bookkeeping (§3.10.7, §3.10.35-36)
- content-hash deduplication so replayed events do not double-send
  (§3.10.37-39)
- quiet-hours deferral with the mandatory-security bypass (§3.10.9, §3.10.46-48)
- DB-backed email template rendering with a variable contract (§3.10.44)
- expiry cleanup for the retention worker (§3.10.72-73)
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import (
    EmailTemplate,
    Notification,
    NotificationDelivery,
    NotificationPreference,
)

# Notification types that always bypass quiet hours and preferences
# (§3.10.9 — users cannot disable mandatory security notifications).
MANDATORY_SECURITY_TYPES = frozenset(
    {
        "security_alert",
        "suspicious_login",
        "account_locked",
        "password_changed",
        "mfa_changed",
    }
)


def _content_hash(
    notification_type: str, to_email: str, subject: str, dedup_key: str | None
) -> str:
    basis = f"{notification_type}|{to_email}|{subject}|{dedup_key or ''}"
    return hashlib.sha256(basis.encode()).hexdigest()


async def is_duplicate(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    notification_type: str,
    to_email: str,
    subject: str,
    dedup_key: str | None,
    window_minutes: int = 60,
) -> bool:
    """True when an identical notification was already sent in the window
    (§3.10.38). The hash covers type + recipient + subject + a caller-
    supplied business dedup key (e.g. obligation id + date)."""
    digest = _content_hash(notification_type, to_email, subject, dedup_key)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    recent = (
        await db.execute(
            select(Notification.id).where(
                Notification.organization_id == organization_id,
                Notification.metadata_["dedup_hash"].as_string() == digest,
                Notification.created_at >= cutoff,
            ).limit(1)
        )
    ).scalar_one_or_none()
    return recent is not None


async def create_notification(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    notification_type: str,
    to_email: str,
    subject: str,
    agreement_id: uuid.UUID | None = None,
    metadata: dict | None = None,
    dedup_key: str | None = None,
    expires_at: datetime | None = None,
) -> Notification:
    """Create a notification row with dedup stamping (§3.10.37-39)."""
    digest = _content_hash(notification_type, to_email, subject, dedup_key)
    metadata = dict(metadata or {})
    metadata["dedup_hash"] = digest
    if dedup_key:
        metadata["dedup_key"] = dedup_key
    metadata["expires_at"] = expires_at.isoformat() if expires_at else None

    notification = Notification(
        organization_id=organization_id,
        agreement_id=agreement_id,
        notification_type=notification_type,
        to_email=to_email,
        subject=subject,
        status="queued",
        metadata_=metadata,
    )
    db.add(notification)
    await db.flush()
    return notification


async def record_delivery(
    db: AsyncSession,
    *,
    notification: Notification,
    channel: str,
    status: str,
    provider: str | None = None,
    provider_message_id: str | None = None,
    error_message: str | None = None,
    attempt: int = 1,
    retry_in_minutes: int | None = None,
) -> NotificationDelivery:
    """Persist one per-channel delivery attempt (§3.10.7, §3.10.36)."""
    delivery = NotificationDelivery(
        notification_id=notification.id,
        channel=channel,
        status=status,
        provider=provider,
        provider_message_id=provider_message_id,
        error_message=error_message,
        attempt=attempt,
        next_retry_at=(
            datetime.now(timezone.utc) + timedelta(minutes=retry_in_minutes)
            if status == "failed" and retry_in_minutes
            else None
        ),
    )
    db.add(delivery)
    await db.flush()
    return delivery


def in_quiet_hours(
    preference: NotificationPreference | None, now: datetime | None = None
) -> bool:
    """Whether ``now`` falls in the user's quiet-hours window (§3.10.47).

    Windows may wrap midnight (start > end). Users without a preference or
    without configured hours are never quiet.
    """
    if preference is None:
        return False
    start, end = preference.quiet_hours_start, preference.quiet_hours_end
    if start is None or end is None or start == end:
        return False
    now = now or datetime.now(timezone.utc)
    hour = now.hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def deferred_send_time(
    preference: NotificationPreference | None, now: datetime | None = None
) -> datetime:
    """The earliest send time respecting quiet hours (§3.10.47-48)."""
    now = now or datetime.now(timezone.utc)
    if not in_quiet_hours(preference, now):
        return now
    end_hour = preference.quiet_hours_end
    target = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def can_send(
    preference: NotificationPreference | None, notification_type: str
) -> bool:
    """Preference gate: mandatory security types always pass (§3.10.9)."""
    if notification_type in MANDATORY_SECURITY_TYPES:
        return True
    if preference is None:
        return True
    return True  # type-level toggles are checked by callers via columns


async def render_email_template(
    db: AsyncSession, *, key: str, variables: dict
) -> tuple[str, str]:
    """Render a DB-backed template with a variable contract (§3.10.44)."""
    template = (
        await db.execute(
            select(EmailTemplate).where(
                EmailTemplate.key == key, EmailTemplate.is_active.is_(True)
            )
        )
    ).scalars().first()
    if template is None:
        raise ValueError(f"Email template {key!r} not found or inactive")

    required = (template.required_variables or {}).get("required", [])
    missing = [v for v in required if v not in variables]
    if missing:
        raise ValueError(
            f"Email template {key!r} missing required variables: {missing}"
        )

    subject = template.subject_template
    body = template.body_template
    for var, value in variables.items():
        subject = subject.replace("{{ " + var + " }}", str(value))
        subject = subject.replace("{{" + var + "}}", str(value))
        body = body.replace("{{ " + var + " }}", str(value))
        body = body.replace("{{" + var + "}}", str(value))
    return subject, body


async def cleanup_expired_notifications(db: AsyncSession) -> int:
    """Purge notifications past their retention timestamp (§3.10.72-73)."""
    from sqlalchemy import delete

    result = await db.execute(
        delete(Notification).where(
            Notification.metadata_["expires_at"].as_string()
            < datetime.now(timezone.utc).isoformat()
        )
    )
    return result.rowcount or 0
