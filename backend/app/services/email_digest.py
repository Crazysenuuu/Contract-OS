"""
Email Digest Service.

Batches notifications into digest emails instead of sending immediately.
Supports daily and weekly digest frequencies.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationPreference
from app.services.email_service import email_service

logger = logging.getLogger(__name__)


class EmailDigestService:
    """Manages email digest batching and sending."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def queue_for_digest(
        self,
        notification_type: str,
        to_email: str,
        subject: str,
        user_id: UUID,
        organization_id: UUID,
        agreement_id: Optional[UUID] = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """
        Queue a notification for digest instead of sending immediately.

        Returns True if queued for digest, False if should be sent immediately.
        """
        # Check user preferences
        result = await self.db.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.organization_id == organization_id,
            )
        )
        prefs = result.scalar_one_or_none()

        if prefs is None or not prefs.digest_enabled:
            return False  # Send immediately

        # Store as pending notification
        notification = Notification(
            organization_id=organization_id,
            agreement_id=agreement_id,
            notification_type=notification_type,
            to_email=to_email,
            subject=subject,
            status="pending",
            metadata_=metadata,
        )
        self.db.add(notification)
        await self.db.flush()

        logger.info(f"Queued notification for digest: {notification_type} to {to_email}")
        return True

    async def send_pending_digests(
        self,
        frequency: Optional[str] = None,
    ) -> int:
        """
        Send all pending digest emails.

        Args:
            frequency: Filter by frequency ('daily' or 'weekly').
                      If None, sends both.

        Returns:
            Number of digest emails sent.
        """
        # Get users with digest enabled
        query = select(NotificationPreference).where(
            NotificationPreference.digest_enabled == True,
        )
        if frequency:
            query = query.where(
                NotificationPreference.digest_frequency == frequency
            )

        result = await self.db.execute(query)
        preferences = result.scalars().all()

        sent_count = 0

        for prefs in preferences:
            # Check if it's time to send based on frequency
            if not self._should_send_now(prefs):
                continue

            # Get pending notifications for this user
            pending = await self._get_pending_notifications(
                prefs.user_id, prefs.organization_id
            )

            if not pending:
                continue

            # Group by agreement
            grouped = self._group_notifications(pending)

            # Build digest email
            html = self._build_digest_html(
                grouped, prefs.digest_frequency, to_email=pending[0].to_email
            )

            subject = f"ContractOS Digest - {len(pending)} new notification(s)"

            # Send digest. The shared _send path appends the CAN-SPAM
            # footer (unsubscribe + postal address) automatically.
            email_result = email_service._send(
                to_email=pending[0].to_email,
                subject=subject,
                html_content=html,
            )

            # Mark notifications as sent
            for notification in pending:
                notification.status = "sent" if email_result.success else "failed"
                notification.message_id = email_result.message_id

            await self.db.flush()
            sent_count += 1

            logger.info(
                f"Digest sent to {pending[0].to_email}: "
                f"{len(pending)} notifications"
            )

        return sent_count

    def _should_send_now(self, prefs: NotificationPreference) -> bool:
        """Check if it's time to send digest based on frequency."""
        now = datetime.now(timezone.utc)

        if prefs.digest_frequency == "daily":
            # Send once per day (between 8-9 AM)
            return now.hour == 8

        elif prefs.digest_frequency == "weekly":
            # Send once per week (Monday 8 AM)
            return now.weekday() == 0 and now.hour == 8

        return False

    async def _get_pending_notifications(
        self,
        user_id: UUID,
        organization_id: UUID,
    ) -> list[Notification]:
        """Get all pending notifications for a user."""
        result = await self.db.execute(
            select(Notification).where(
                Notification.organization_id == organization_id,
                Notification.status == "pending",
            ).order_by(Notification.created_at.asc())
        )
        return list(result.scalars().all())

    def _group_notifications(
        self,
        notifications: list[Notification],
    ) -> dict[Optional[UUID], list[Notification]]:
        """Group notifications by agreement_id."""
        grouped: dict[Optional[UUID], list[Notification]] = {}
        for n in notifications:
            key = n.agreement_id
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(n)
        return grouped

    def _build_digest_html(
        self,
        grouped: dict[Optional[UUID], list[Notification]],
        frequency: str,
        to_email: str = "",
    ) -> str:
        """Build HTML for digest email.

        When ``to_email`` is provided the CAN-SPAM footer (unsubscribe link +
        physical postal address) is embedded inside the styled card, marked
        with ``data-can-spam-footer`` so ``_send`` does not append it again.
        """
        footer_block = (
            f'<div data-can-spam-footer>{email_service._compliance_footer(to_email)}</div>'
            if to_email
            else ""
        )
        total = sum(len(notifs) for notifs in grouped.values())

        sections_html = ""
        for agreement_id, notifications in grouped.items():
            title = (
                f"Agreement {str(agreement_id)[:8]}..."
                if agreement_id
                else "General"
            )
            items_html = ""
            for n in notifications:
                items_html += f"""
                <div style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">
                    <div style="font-weight:500;font-size:13px;">{n.subject}</div>
                    <div style="font-size:11px;color:#6b7280;">
                        {n.created_at.strftime('%H:%M UTC') if n.created_at else ''}
                    </div>
                </div>
                """

            sections_html += f"""
            <div style="margin-bottom:24px;">
                <h3 style="font-size:14px;color:#374151;margin:0 0 8px 0;padding:8px 12px;background:#f9fafb;border-radius:4px;">
                    {title}
                </h3>
                {items_html}
            </div>
            """

        return f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="margin:0;padding:0;font-family:-apple-system,sans-serif;background:#f3f4f6;">
            <div style="max-width:600px;margin:0 auto;padding:20px;">
                <div style="background:#fff;border-radius:8px;padding:32px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
                    <h1 style="font-size:20px;color:#111827;text-align:center;margin:0 0 24px 0;">
                        📋 ContractOS {frequency.title()} Digest
                    </h1>
                    <p style="color:#374151;font-size:14px;margin:0 0 24px 0;">
                        You have <strong>{total}</strong> new notification(s).
                    </p>
                    {sections_html}
                    <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
                    <p style="font-size:12px;color:#6b7280;margin:0;">
                        Manage your notification preferences in ContractOS settings.
                    </p>
                    {footer_block}
                </div>
            </div>
        </body>
        </html>
        """

    async def get_digest_stats(
        self,
        organization_id: UUID,
    ) -> dict:
        """Get digest statistics for an organization."""
        # Count pending notifications
        pending_result = await self.db.execute(
            select(Notification).where(
                Notification.organization_id == organization_id,
                Notification.status == "pending",
            )
        )
        pending = len(pending_result.scalars().all())

        # Count digest subscribers
        digest_result = await self.db.execute(
            select(NotificationPreference).where(
                NotificationPreference.organization_id == organization_id,
                NotificationPreference.digest_enabled == True,
            )
        )
        subscribers = len(digest_result.scalars().all())

        return {
            "pending_notifications": pending,
            "digest_subscribers": subscribers,
        }


# Singleton instance factory
def get_digest_service(db: AsyncSession) -> EmailDigestService:
    """Get digest service instance."""
    return EmailDigestService(db)
