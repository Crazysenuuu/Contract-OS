"""
Notification Tracking Service.

Wraps the email service and records notifications in the database.
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.services.email_service import email_service

logger = logging.getLogger(__name__)


class NotificationTracking:
    """Tracks and records all notifications sent."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _record(
        self,
        notification_type: str,
        to_email: str,
        subject: str,
        status: str,
        message_id: Optional[str] = None,
        error_message: Optional[str] = None,
        organization_id: Optional[UUID] = None,
        agreement_id: Optional[UUID] = None,
        metadata: Optional[dict] = None,
    ) -> Notification:
        """Record a notification in the database."""
        notification = Notification(
            organization_id=organization_id or UUID(
                "00000000-0000-0000-0000-000000000001"
            ),
            agreement_id=agreement_id,
            notification_type=notification_type,
            to_email=to_email,
            subject=subject,
            status=status,
            message_id=message_id,
            error_message=error_message,
            metadata_=metadata,
            sent_at=datetime.now(timezone.utc),
        )
        self.db.add(notification)
        return notification

    async def send_review_invitation(
        self,
        to_email: str,
        company_name: str,
        agreement_title: str,
        review_url: str,
        signatory_name: str,
        organization_id: Optional[UUID] = None,
        agreement_id: Optional[UUID] = None,
    ) -> Notification:
        """Send and track review invitation."""
        result = email_service.send_review_invitation(
            to_email=to_email,
            company_name=company_name,
            agreement_title=agreement_title,
            review_url=review_url,
            signatory_name=signatory_name,
        )

        notification = await self._record(
            notification_type="review_invitation",
            to_email=to_email,
            subject=f"{company_name} sent you '{agreement_title}' for review",
            status="sent" if result.success else "failed",
            message_id=result.message_id,
            error_message=None if result.success else result.message,
            organization_id=organization_id,
            agreement_id=agreement_id,
            metadata={
                "company_name": company_name,
                "signatory_name": signatory_name,
                "review_url": review_url,
            },
        )
        return notification

    async def send_agreement_viewed_notification(
        self,
        to_email: str,
        agreement_title: str,
        viewer_name: str,
        organization_id: Optional[UUID] = None,
        agreement_id: Optional[UUID] = None,
    ) -> Notification:
        """Send and track viewed notification."""
        result = email_service.send_agreement_viewed_notification(
            to_email=to_email,
            agreement_title=agreement_title,
            viewer_name=viewer_name,
        )

        return await self._record(
            notification_type="agreement_viewed",
            to_email=to_email,
            subject=f"{viewer_name} viewed '{agreement_title}'",
            status="sent" if result.success else "failed",
            message_id=result.message_id,
            organization_id=organization_id,
            agreement_id=agreement_id,
            metadata={"viewer_name": viewer_name},
        )

    async def send_change_requested_notification(
        self,
        to_email: str,
        agreement_title: str,
        requester_name: str,
        comment_summary: str,
        organization_id: Optional[UUID] = None,
        agreement_id: Optional[UUID] = None,
    ) -> Notification:
        """Send and track change request notification."""
        result = email_service.send_change_requested_notification(
            to_email=to_email,
            agreement_title=agreement_title,
            requester_name=requester_name,
            comment_summary=comment_summary,
        )

        return await self._record(
            notification_type="change_requested",
            to_email=to_email,
            subject=f"{requester_name} requested changes to '{agreement_title}'",
            status="sent" if result.success else "failed",
            message_id=result.message_id,
            organization_id=organization_id,
            agreement_id=agreement_id,
            metadata={
                "requester_name": requester_name,
                "comment_summary": comment_summary,
            },
        )

    async def send_agreement_accepted_notification(
        self,
        to_email: str,
        agreement_title: str,
        acceptor_name: str,
        organization_id: Optional[UUID] = None,
        agreement_id: Optional[UUID] = None,
    ) -> Notification:
        """Send and track acceptance notification."""
        result = email_service.send_agreement_accepted_notification(
            to_email=to_email,
            agreement_title=agreement_title,
            acceptor_name=acceptor_name,
        )

        return await self._record(
            notification_type="agreement_accepted",
            to_email=to_email,
            subject=f"{acceptor_name} accepted '{agreement_title}'",
            status="sent" if result.success else "failed",
            message_id=result.message_id,
            organization_id=organization_id,
            agreement_id=agreement_id,
            metadata={"acceptor_name": acceptor_name},
        )

    async def send_signature_completed_notification(
        self,
        to_email: str,
        agreement_title: str,
        signer_name: str,
        organization_id: Optional[UUID] = None,
        agreement_id: Optional[UUID] = None,
    ) -> Notification:
        """Send and track signature notification."""
        result = email_service.send_signature_completed_notification(
            to_email=to_email,
            agreement_title=agreement_title,
            signer_name=signer_name,
        )

        return await self._record(
            notification_type="signature_completed",
            to_email=to_email,
            subject=f"{signer_name} signed '{agreement_title}'",
            status="sent" if result.success else "failed",
            message_id=result.message_id,
            organization_id=organization_id,
            agreement_id=agreement_id,
            metadata={"signer_name": signer_name},
        )

    async def send_approval_request_notification(
        self,
        to_email: str,
        agreement_title: str,
        requester_name: str,
        organization_id: Optional[UUID] = None,
        agreement_id: Optional[UUID] = None,
    ) -> Notification:
        """Send and track approval request notification."""
        result = email_service.send_approval_request_notification(
            to_email=to_email,
            agreement_title=agreement_title,
            requester_name=requester_name,
        )

        return await self._record(
            notification_type="approval_request",
            to_email=to_email,
            subject=f"Approval requested: '{agreement_title}'",
            status="sent" if result.success else "failed",
            message_id=result.message_id,
            organization_id=organization_id,
            agreement_id=agreement_id,
            metadata={"requester_name": requester_name},
        )

    async def send_workflow_transition_notification(
        self,
        to_email: str,
        agreement_title: str,
        previous_state: str,
        current_state: str,
        actor_name: str,
        organization_id: Optional[UUID] = None,
        agreement_id: Optional[UUID] = None,
    ) -> Notification:
        """Send and track workflow transition notification."""
        result = email_service.send_workflow_transition_notification(
            to_email=to_email,
            agreement_title=agreement_title,
            previous_state=previous_state,
            current_state=current_state,
            actor_name=actor_name,
        )

        return await self._record(
            notification_type="workflow_transition",
            to_email=to_email,
            subject=f"'{agreement_title}' moved to {current_state}",
            status="sent" if result.success else "failed",
            message_id=result.message_id,
            organization_id=organization_id,
            agreement_id=agreement_id,
            metadata={
                "previous_state": previous_state,
                "current_state": current_state,
                "actor_name": actor_name,
            },
        )

    async def send_compliance_violation_notification(
        self,
        to_email: str,
        agreement_title: str,
        violations_count: int,
        critical_count: int,
        compliance_score: float,
        organization_id: Optional[UUID] = None,
        agreement_id: Optional[UUID] = None,
    ) -> Notification:
        """Send and track compliance violation notification."""
        result = email_service.send_compliance_violation_notification(
            to_email=to_email,
            agreement_title=agreement_title,
            violations_count=violations_count,
            critical_count=critical_count,
            compliance_score=compliance_score,
        )

        return await self._record(
            notification_type="compliance_violation",
            to_email=to_email,
            subject=f"Compliance check: {violations_count} violation(s) in '{agreement_title}'",
            status="sent" if result.success else "failed",
            message_id=result.message_id,
            organization_id=organization_id,
            agreement_id=agreement_id,
            metadata={
                "violations_count": violations_count,
                "critical_count": critical_count,
                "compliance_score": compliance_score,
            },
        )

    async def get_agreement_notifications(
        self,
        agreement_id: UUID,
        limit: int = 50,
    ) -> list[Notification]:
        """Get notification history for an agreement."""
        result = await self.db.execute(
            select(Notification)
            .where(Notification.agreement_id == agreement_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_organization_notifications(
        self,
        organization_id: UUID,
        limit: int = 100,
    ) -> list[Notification]:
        """Get notification history for an organization."""
        result = await self.db.execute(
            select(Notification)
            .where(Notification.organization_id == organization_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
