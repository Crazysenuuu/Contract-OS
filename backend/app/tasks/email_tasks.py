from app.worker import celery_app
from app.services.email_service import EmailService


@celery_app.task(name="send_email_async")
def send_email_async(
    to_email: str,
    subject: str,
    template_name: str,
    template_data: dict | None = None,
    attachments: list | None = None,
):
    """
    Celery task to send emails asynchronously.

    EmailService.send_email is synchronous (SendGrid calls are blocking), so
    the task invokes it directly — no event-loop juggling, which also makes
    the task safe under pytest's eager mode where a loop is already running.
    """
    email_service = EmailService()
    return email_service.send_email(
        to_email=to_email,
        subject=subject,
        template_name=template_name,
        template_data=template_data,
        attachments=attachments,
    )