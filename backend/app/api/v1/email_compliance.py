"""
Email compliance endpoints (CAN-SPAM).

One-click opt-out for every email address, reachable two ways:
- GET  /email/opt-out?email=...&sig=...   — human-clickable footer link
- POST /email/opt-out?email=...&sig=...   — RFC 8058 List-Unsubscribe=One-Click

The request is authorized by an HMAC-SHA256 of the recipient address keyed
with EMAIL_OPT_OUT_TOKEN, so the link cannot be used to opt out arbitrary
third parties. Opting out is org-agnostic: it disables every email channel
in the user's notification preferences across all of their memberships.
"""

import hashlib
import hmac
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import EmailStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings_lazy
from app.core.database import get_db
from app.models.notification import NotificationPreference
from app.models.user import User

router = APIRouter(prefix="/email", tags=["Email Compliance"])

logger = logging.getLogger(__name__)


def _valid_signature(email: str, sig: str) -> bool:
    settings = get_settings_lazy()
    token = settings.email_opt_out_token
    if token == "change-me-opt-out-secret":
        # Fail loud in logs: with the default token the HMAC is forgeable and
        # anyone could opt out arbitrary addresses. Deployment must set a
        # real EMAIL_OPT_OUT_TOKEN before sending mail.
        logger.warning(
            "EMAIL_OPT_OUT_TOKEN is still the default value; unsubscribe "
            "links are NOT tamper-proof. Set a strong secret in production."
        )
    expected = hmac.new(
        token.encode(),
        email.lower().encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig or "")


async def _apply_opt_out(db: AsyncSession, email: str) -> int:
    """Turn off every email channel for the address. Returns rows updated."""
    result = await db.execute(
        select(User.id).where(User.email == email.lower())
    )
    user_ids = [row[0] for row in result.all()]
    if not user_ids:
        return 0

    res = await db.execute(
        update(NotificationPreference)
        .where(NotificationPreference.user_id.in_(user_ids))
        .values(
            email_review_invitation=False,
            email_agreement_viewed=False,
            email_change_requested=False,
            email_agreement_accepted=False,
            email_signature_completed=False,
            email_approval_request=False,
            email_workflow_transition=False,
            email_compliance_violation=False,
            email_obligation_reminder=False,
            # A user who opts out must also stop receiving digests —
            # digest_enabled bypasses the per-type toggles.
            digest_enabled=False,
        )
    )
    return res.rowcount or 0


def _one_click_response() -> Response:
    # RFC 8058: a 2xx with a short body acknowledges the one-click POST.
    return Response(status_code=status.HTTP_200_OK)


@router.get("/opt-out")
async def opt_out_get(
    email: str,
    sig: str,
    db: AsyncSession = Depends(get_db),
):
    """Human-clickable unsubscribe link from the email footer."""
    if not _valid_signature(email, sig):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid unsubscribe link",
        )
    updated = await _apply_opt_out(db, email)
    await db.commit()
    return {
        "message": (
            "You have been unsubscribed. You will no longer receive "
            "non-account emails from ContractOS."
        ),
        "preferences_updated": updated,
    }


@router.post("/opt-out")
async def opt_out_one_click(
    request: Request,
    email: Optional[EmailStr] = None,
    sig: str = "",
    db: AsyncSession = Depends(get_db),
):
    """RFC 8058 one-click unsubscribe (List-Unsubscribe-Post).

    Mail providers POST here with the list header's URL — the address and
    signature may arrive as query parameters or as a form body.
    """
    resolved_email = email
    if resolved_email is None:
        try:
            form = await request.form()
            resolved_email = form.get("list-unsubscribe") or form.get("email")
        except Exception:
            resolved_email = None
    if not resolved_email or not _valid_signature(str(resolved_email), sig):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid unsubscribe request",
        )
    updated = await _apply_opt_out(db, str(resolved_email))
    await db.commit()
    logger.info("One-click opt-out applied for %s", resolved_email)
    return _one_click_response()
