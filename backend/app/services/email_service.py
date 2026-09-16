"""
Email Notification Service with SendGrid Integration.

Sends transactional emails for agreement lifecycle events.
Falls back to logging when SendGrid is not configured.
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EmailResult:
    """Result of an email send."""

    success: bool
    message: str
    message_id: Optional[str] = None


class EmailService:
    """Email service with SendGrid integration."""

    def __init__(self):
        self._client = None
        self._from_email = None
        self._from_name = None
        self._app_base_url = None

    def _get_config(self):
        """Lazy-load configuration."""
        if self._client is not None:
            return

        from app.core.config import get_settings_lazy

        settings = get_settings_lazy()
        self._app_base_url = settings.app_base_url
        self._from_email = settings.email_from_address
        self._from_name = settings.email_from_name

        api_key = settings.sendgrid_api_key
        if api_key and api_key.get_secret_value():
            try:
                from sendgrid import SendGridAPIClient

                self._client = SendGridAPIClient(
                    api_key.get_secret_value()
                )
                logger.info("SendGrid client initialized")
            except ImportError:
                logger.warning(
                    "sendgrid package not installed. "
                    "Install with: pip install sendgrid"
                )
                self._client = False
        else:
            logger.info(
                "SendGrid not configured. Emails will be logged only. "
                "Set SENDGRID_API_KEY environment variable to enable."
            )
            self._client = False

    def _send(self, to_email: str, subject: str, html_content: str) -> EmailResult:
        """Send an email via SendGrid or log it."""
        self._get_config()

        if self._client and self._client is not False:
            try:
                from sendgrid.helpers.mail import Mail

                message = Mail(
                    from_email=f"{self._from_name} <{self._from_email}>",
                    to_emails=to_email,
                    subject=subject,
                    html_content=html_content,
                )
                response = self._client.send(message)
                message_id = response.headers.get("X-Message-Id", "unknown")
                logger.info(f"Email sent to {to_email}: {subject} (id={message_id})")
                return EmailResult(
                    success=True,
                    message="Email sent successfully",
                    message_id=message_id,
                )
            except Exception as e:
                logger.error(f"Failed to send email to {to_email}: {e}")
                return EmailResult(success=False, message=str(e))
        else:
            # Log-only mode
            logger.info(
                f"[EMAIL LOG] To: {to_email}\n"
                f"Subject: {subject}\n"
                f"---\n{html_content[:500]}...\n---"
            )
            return EmailResult(
                success=True,
                message="Email logged (SendGrid not configured)",
                message_id="log-only",
            )

    def send_email(
        self,
        to_email: str,
        subject: str,
        template_name: str,
        template_data: Optional[dict] = None,
        attachments: Optional[list] = None,
    ) -> EmailResult:
        """Generic dispatch used by the email task.

        Maps a template name to the specialized send_* method so the task
        never needs to know about individual templates.
        """
        template_data = template_data or {}
        try:
            if template_name == "welcome_email":
                verify_url = template_data.get("verify_url", "")
                body = (
                    f"<p>Welcome, <strong>{template_data.get('name', '')}</strong>!</p>"
                    f"<p>Please verify your email address to activate your account:</p>"
                    f"<div style='text-align:center;margin:24px 0;'>"
                    f"<a href='{verify_url}' "
                    f"style='background-color:#3b82f6;color:#ffffff;padding:12px 24px;"
                    f"border-radius:6px;text-decoration:none;font-weight:500;display:inline-block;'>"
                    f"Verify Email</a></div>"
                )
                return self._send(
                    to_email, subject, self._build_base_template("Welcome to ContractOS", body)
                )
            if template_name == "review_invitation":
                return self.send_review_invitation(
                    to_email,
                    company_name=template_data.get("company_name", ""),
                    agreement_title=template_data.get("agreement_title", ""),
                    review_url=template_data.get("review_url", ""),
                    signatory_name=template_data.get("signatory_name", ""),
                )
            if template_name == "obligation_reminder":
                return self.send_obligation_reminder(
                    to_email,
                    agreement_title=template_data.get("agreement_title", ""),
                    obligation_description=template_data.get("obligation_description", ""),
                    due_date=template_data.get("due_date", ""),
                    obligation_type=template_data.get("obligation_type", ""),
                )
            if template_name == "approval_request":
                return self.send_approval_request_notification(
                    to_email,
                    agreement_title=template_data.get("agreement_title", ""),
                    requester_name=template_data.get("requester_name", ""),
                )
            if template_name == "verification_code":
                code = template_data.get("code", "")
                body = (
                    "<p>Your one-time verification code is:</p>"
                    f"<div style='text-align:center;margin:24px 0;'>"
                    f"<span style='font-size:28px;font-weight:700;letter-spacing:4px;"
                    f"color:#111827;'>{code}</span></div>"
                    "<p>This code expires in 10 minutes. If you did not request it, "
                    "please ignore this message.</p>"
                )
                return self._send(
                    to_email, subject, self._build_base_template("Verification Code", body)
                )
        except TypeError:
            # Template method signature mismatch — fall through to generic body.
            pass

        # Generic fallback so unknown templates never crash the worker.
        return self._send(
            to_email,
            subject,
            self._build_base_template("ContractOS Notification", f"<p>{subject}</p>"),
        )

    def _build_base_template(self, title: str, body_html: str) -> str:
        """Wrap content in a base email template."""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background-color:#f3f4f6;">
            <div style="max-width:600px;margin:0 auto;padding:20px;">
                <div style="background-color:#ffffff;border-radius:8px;padding:32px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
                    <div style="text-align:center;margin-bottom:24px;">
                        <h1 style="font-size:20px;color:#111827;margin:0;">📋 ContractOS</h1>
                    </div>
                    <h2 style="font-size:18px;color:#111827;margin:0 0 16px 0;">{title}</h2>
                    {body_html}
                    <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
                    <p style="font-size:12px;color:#6b7280;margin:0;">
                        This is an automated notification from ContractOS.<br>
                        <a href="{self._app_base_url}" style="color:#3b82f6;">Open ContractOS</a>
                    </p>
                </div>
            </div>
        </body>
        </html>
        """

    # --- Lifecycle Notifications ---

    def send_review_invitation(
        self,
        to_email: str,
        company_name: str,
        agreement_title: str,
        review_url: str,
        signatory_name: str,
    ) -> EmailResult:
        """Send review invitation to external party."""
        body = f"""
        <p style="color:#374151;margin:0 0 12px 0;">Dear {signatory_name},</p>
        <p style="color:#374151;margin:0 0 12px 0;">
            <strong>{company_name}</strong> has sent you
            <strong>{agreement_title}</strong> for review.
        </p>
        <p style="color:#374151;margin:0 0 20px 0;">
            Please review the agreement and provide your feedback or signature.
        </p>
        <div style="text-align:center;margin:24px 0;">
            <a href="{review_url}"
               style="background-color:#3b82f6;color:#ffffff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:500;display:inline-block;">
                Review Agreement
            </a>
        </div>
        <p style="color:#6b7280;font-size:13px;margin:0;">
            This link is unique to you and does not require an account.
        </p>
        """
        return self._send(
            to_email,
            f"{company_name} sent you '{agreement_title}' for review",
            self._build_base_template("Review Invitation", body),
        )

    def send_agreement_viewed_notification(
        self,
        to_email: str,
        agreement_title: str,
        viewer_name: str,
    ) -> EmailResult:
        """Notify that the counterparty viewed the agreement."""
        body = f"""
        <p style="color:#374151;margin:0 0 12px 0;">
            <strong>{viewer_name}</strong> has viewed
            <strong>{agreement_title}</strong>.
        </p>
        <p style="color:#374151;margin:0;">
            You will be notified when they provide feedback or sign.
        </p>
        """
        return self._send(
            to_email,
            f"{viewer_name} viewed '{agreement_title}'",
            self._build_base_template("Agreement Viewed", body),
        )

    def send_change_requested_notification(
        self,
        to_email: str,
        agreement_title: str,
        requester_name: str,
        comment_summary: str,
    ) -> EmailResult:
        """Notify that the counterparty requested changes."""
        body = f"""
        <p style="color:#374151;margin:0 0 12px 0;">
            <strong>{requester_name}</strong> has requested changes to
            <strong>{agreement_title}</strong>.
        </p>
        <div style="background-color:#f9fafb;border-left:4px solid #f59e0b;padding:12px 16px;margin:16px 0;border-radius:4px;">
            <p style="color:#374151;margin:0;font-style:italic;">
                "{comment_summary}"
            </p>
        </div>
        <div style="text-align:center;margin:24px 0;">
            <a href="{self._app_base_url}/agreements"
               style="background-color:#3b82f6;color:#ffffff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:500;display:inline-block;">
                View Agreement
            </a>
        </div>
        """
        return self._send(
            to_email,
            f"{requester_name} requested changes to '{agreement_title}'",
            self._build_base_template("Changes Requested", body),
        )

    def send_agreement_accepted_notification(
        self,
        to_email: str,
        agreement_title: str,
        acceptor_name: str,
    ) -> EmailResult:
        """Notify that the counterparty accepted the agreement."""
        body = f"""
        <p style="color:#374151;margin:0 0 12px 0;">
            <strong>{acceptor_name}</strong> has accepted
            <strong>{agreement_title}</strong>.
        </p>
        <div style="background-color:#ecfdf5;border-left:4px solid #10b981;padding:12px 16px;margin:16px 0;border-radius:4px;">
            <p style="color:#065f46;margin:0;font-weight:500;">
                ✅ Agreement accepted — ready for signing
            </p>
        </div>
        """
        return self._send(
            to_email,
            f"{acceptor_name} accepted '{agreement_title}'",
            self._build_base_template("Agreement Accepted", body),
        )

    def send_signature_completed_notification(
        self,
        to_email: str,
        agreement_title: str,
        signer_name: str,
    ) -> EmailResult:
        """Notify that the counterparty signed the agreement."""
        body = f"""
        <p style="color:#374151;margin:0 0 12px 0;">
            <strong>{signer_name}</strong> has signed
            <strong>{agreement_title}</strong>.
        </p>
        <div style="background-color:#ecfdf5;border-left:4px solid #10b981;padding:12px 16px;margin:16px 0;border-radius:4px;">
            <p style="color:#065f46;margin:0;font-weight:500;">
                🖊️ Agreement signed and executed
            </p>
        </div>
        """
        return self._send(
            to_email,
            f"{signer_name} signed '{agreement_title}'",
            self._build_base_template("Agreement Signed", body),
        )

    def send_approval_request_notification(
        self,
        to_email: str,
        agreement_title: str,
        requester_name: str,
    ) -> EmailResult:
        """Notify internal reviewer of approval request."""
        body = f"""
        <p style="color:#374151;margin:0 0 12px 0;">
            <strong>{requester_name}</strong> has submitted
            <strong>{agreement_title}</strong> for your approval.
        </p>
        <p style="color:#374151;margin:0 0 20px 0;">
            Please review and approve or reject this agreement.
        </p>
        <div style="text-align:center;margin:24px 0;">
            <a href="{self._app_base_url}/agreements"
               style="background-color:#8b5cf6;color:#ffffff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:500;display:inline-block;">
                Review for Approval
            </a>
        </div>
        """
        return self._send(
            to_email,
            f"Approval requested: '{agreement_title}'",
            self._build_base_template("Approval Requested", body),
        )

    def send_workflow_transition_notification(
        self,
        to_email: str,
        agreement_title: str,
        previous_state: str,
        current_state: str,
        actor_name: str,
    ) -> EmailResult:
        """Notify of workflow state transition."""
        body = f"""
        <p style="color:#374151;margin:0 0 12px 0;">
            <strong>{agreement_title}</strong> has been updated.
        </p>
        <div style="background-color:#eff6ff;border-left:4px solid #3b82f6;padding:12px 16px;margin:16px 0;border-radius:4px;">
            <p style="color:#1e40af;margin:0;">
                <strong>{previous_state}</strong> → <strong>{current_state}</strong>
            </p>
            <p style="color:#6b7280;margin:8px 0 0 0;font-size:13px;">
                By {actor_name}
            </p>
        </div>
        """
        return self._send(
            to_email,
            f"'{agreement_title}' moved to {current_state}",
            self._build_base_template("Agreement Status Updated", body),
        )

    def send_compliance_violation_notification(
        self,
        to_email: str,
        agreement_title: str,
        violations_count: int,
        critical_count: int,
        compliance_score: float,
    ) -> EmailResult:
        """Notify of compliance violations found."""
        severity = "critical" if critical_count > 0 else "warning"
        emoji = "🚨" if critical_count > 0 else "⚠️"

        body = f"""
        <p style="color:#374151;margin:0 0 12px 0;">
            {emoji} Compliance check completed for <strong>{agreement_title}</strong>.
        </p>
        <div style="background-color:{'#fef2f2' if critical_count > 0 else '#fffbeb'};border-left:4px solid {'#ef4444' if critical_count > 0 else '#f59e0b'};padding:12px 16px;margin:16px 0;border-radius:4px;">
            <p style="margin:0;font-weight:500;color:{'#991b1b' if critical_count > 0 else '#92400e'};">
                Compliance Score: {compliance_score:.0f}%
            </p>
            <p style="margin:4px 0 0 0;font-size:13px;color:#6b7280;">
                {violations_count} violation(s) found
                {f' ({critical_count} critical)' if critical_count else ''}
            </p>
        </div>
        <div style="text-align:center;margin:24px 0;">
            <a href="{self._app_base_url}/agreements"
               style="background-color:#3b82f6;color:#ffffff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:500;display:inline-block;">
                View Report
            </a>
        </div>
        """
        return self._send(
            to_email,
            f"Compliance {severity}: {violations_count} violation(s) in '{agreement_title}'",
            self._build_base_template(f"Compliance {severity.title()}", body),
        )

    def send_obligation_reminder(
        self,
        to_email: str,
        agreement_title: str,
        obligation_description: str,
        due_date: str,
        obligation_type: str,
    ) -> EmailResult:
        """Send obligation reminder."""
        body = f"""
        <p style="color:#374151;margin:0 0 12px 0;">
            Reminder: You have an upcoming obligation for
            <strong>{agreement_title}</strong>.
        </p>
        <div style="background-color:#fffbeb;border-left:4px solid #f59e0b;padding:12px 16px;margin:16px 0;border-radius:4px;">
            <p style="color:#92400e;margin:0;font-weight:500;">
                📅 Due: {due_date}
            </p>
            <p style="color:#374151;margin:8px 0 0 0;">
                {obligation_description}
            </p>
        </div>
        <div style="text-align:center;margin:24px 0;">
            <a href="{self._app_base_url}/agreements"
               style="background-color:#3b82f6;color:#ffffff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:500;display:inline-block;">
                View Obligations
            </a>
        </div>
        """
        return self._send(
            to_email,
            f"Obligation reminder: {obligation_type} due {due_date}",
            self._build_base_template("Obligation Reminder", body),
        )


# Singleton instance
email_service = EmailService()
