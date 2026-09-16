"""Notification dispatch for the agreement lifecycle (spec 34 / 36).

Each function delivers its lifecycle email through the configured email
service (SendGrid when available, otherwise logged), returning a
``NotificationResult`` with the underlying status.
"""

import logging
from dataclasses import dataclass

from app.services.email_service import email_service

logger = logging.getLogger(__name__)


@dataclass
class NotificationResult:
    """Result of a notification send."""

    success: bool
    message: str
    message_id: str | None = None


def _send(fn, *, fallback_message, **kwargs) -> NotificationResult:
    try:
        result = fn(**kwargs)
        return NotificationResult(
            success=bool(result.success),
            message=result.message,
            message_id=result.message_id,
        )
    except Exception:  # Email failures are best-effort
        logger.exception("Failed to send notification email")
        return NotificationResult(success=False, message=fallback_message)


async def send_review_invitation(
    to_email: str,
    company_name: str,
    agreement_title: str,
    review_url: str,
    signatory_name: str,
) -> NotificationResult:
    """Send review invitation to external party."""
    return _send(
        email_service.send_review_invitation,
        fallback_message="Review invitation delivery failed",
        to_email=to_email,
        company_name=company_name,
        agreement_title=agreement_title,
        review_url=review_url,
        signatory_name=signatory_name,
    )


async def send_agreement_viewed_notification(
    to_email: str,
    agreement_title: str,
    viewer_name: str,
) -> NotificationResult:
    """Notify that the counterparty viewed the agreement."""
    return _send(
        email_service.send_agreement_viewed_notification,
        fallback_message="Viewed notification delivery failed",
        to_email=to_email,
        agreement_title=agreement_title,
        viewer_name=viewer_name,
    )


async def send_change_requested_notification(
    to_email: str,
    agreement_title: str,
    requester_name: str,
    comment_summary: str,
) -> NotificationResult:
    """Notify that the counterparty requested changes."""
    return _send(
        email_service.send_change_requested_notification,
        fallback_message="Change request notification delivery failed",
        to_email=to_email,
        agreement_title=agreement_title,
        requester_name=requester_name,
        comment_summary=comment_summary,
    )


async def send_agreement_accepted_notification(
    to_email: str,
    agreement_title: str,
    acceptor_name: str,
) -> NotificationResult:
    """Notify that the counterparty accepted the agreement."""
    return _send(
        email_service.send_agreement_accepted_notification,
        fallback_message="Acceptance notification delivery failed",
        to_email=to_email,
        agreement_title=agreement_title,
        acceptor_name=acceptor_name,
    )


async def send_signature_completed_notification(
    to_email: str,
    agreement_title: str,
    signer_name: str,
) -> NotificationResult:
    """Notify that the counterparty signed the agreement."""
    return _send(
        email_service.send_signature_completed_notification,
        fallback_message="Signature notification delivery failed",
        to_email=to_email,
        agreement_title=agreement_title,
        signer_name=signer_name,
    )


async def send_approval_request_notification(
    to_email: str,
    agreement_title: str,
    requester_name: str,
) -> NotificationResult:
    """Notify internal reviewer of approval request."""
    return _send(
        email_service.send_approval_request_notification,
        fallback_message="Approval request delivery failed",
        to_email=to_email,
        agreement_title=agreement_title,
        requester_name=requester_name,
    )


async def send_workflow_transition_notification(
    to_email: str,
    agreement_title: str,
    previous_state: str,
    current_state: str,
    actor_name: str,
) -> NotificationResult:
    """Notify of workflow state transition."""
    return _send(
        email_service.send_workflow_transition_notification,
        fallback_message="Workflow transition delivery failed",
        to_email=to_email,
        agreement_title=agreement_title,
        previous_state=previous_state,
        current_state=current_state,
        actor_name=actor_name,
    )