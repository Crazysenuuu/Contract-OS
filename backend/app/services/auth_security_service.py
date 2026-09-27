"""Auth security service (spec 1.22).

Refresh-token rotation with hashed storage, and app-level login rate
limiting that works without Redis (SQL window counting).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings_lazy
from app.models.user import LoginAttempt, RefreshToken, User

settings = get_settings_lazy()

# Rate limiting configuration. Tight in production; relaxed in development
# where hydration-retrying login forms and repeated smoke-test logins easily
# accumulate 5+ failures and lock the developer out for the window.
if settings.environment == "production":
    MAX_FAILED_ATTEMPTS_PER_EMAIL = 5
    MAX_FAILED_ATTEMPTS_PER_IP = 20
    RATE_LIMIT_WINDOW_MINUTES = 15
else:  # development / test
    MAX_FAILED_ATTEMPTS_PER_EMAIL = 50
    MAX_FAILED_ATTEMPTS_PER_IP = 200
    RATE_LIMIT_WINDOW_MINUTES = 15


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _naive_utc() -> datetime:
    """UTC datetime without tzinfo.

    Login attempts are stored in UTC-naive form so the SQL window count
    compares consistently on SQLite (string comparison) and Postgres.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ensure_aware(dt: datetime) -> datetime:
    """Normalize naive datetimes (SQLite returns naive) to aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Refresh tokens
# --------------------------------------------------------------------------

async def create_refresh_token(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[RefreshToken, str]:
    """Create a refresh token; returns (row, raw_token).

    The raw token is returned exactly once and never stored — only its
    hash is persisted.
    """
    raw_token = secrets.token_urlsafe(48)
    expires_at = now_utc() + timedelta(days=settings.refresh_token_expire_days)
    row = RefreshToken(
        user_id=user_id,
        session_id=session_id,
        token_hash=_hash_token(raw_token),
        expires_at=expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(row)
    await db.flush()
    return row, raw_token


async def validate_refresh_token(
    db: AsyncSession,
    raw_token: str,
) -> tuple[User, RefreshToken] | None:
    """Validate a refresh token; returns (user, token_row) or None."""
    token_hash = _hash_token(raw_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    if row.revoked_at is not None:
        return None
    if _ensure_aware(row.expires_at) < now_utc():
        return None
    user_result = await db.execute(
        select(User).where(User.id == row.user_id)
    )
    user = user_result.scalar_one_or_none()
    if user is None or user.status not in ("active", "pending_verification"):
        return None
    return user, row


async def rotate_refresh_token(
    db: AsyncSession,
    *,
    token_row: RefreshToken,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[RefreshToken, str]:
    """Rotate a refresh token: revoke the old, issue a new one.

    If the old token was ALREADY revoked (reuse after rotation), the
    session is treated as compromised: revoke all of the user's refresh
    tokens and raise 401.
    """
    if token_row.revoked_at is not None:
        # Reuse of a rotated token — suspected theft. Kill all sessions.
        await revoke_all_for_user(db, token_row.user_id, reason="suspected_reuse")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token reuse detected; all sessions revoked",
        )

    new_row, raw_token = await create_refresh_token(
        db,
        user_id=token_row.user_id,
        session_id=token_row.session_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    token_row.revoked_at = now_utc()
    token_row.revoked_reason = "rotated"
    token_row.replaced_by_token_id = new_row.id
    await db.flush()
    return new_row, raw_token


async def revoke_all_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    reason: str = "logout",
) -> None:
    await db.execute(
        select(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    for row in result.scalars().all():
        row.revoked_at = now_utc()
        row.revoked_reason = reason
    await db.flush()


async def revoke_refresh_token(
    db: AsyncSession,
    raw_token: str,
    reason: str = "logout",
) -> None:
    token_hash = _hash_token(raw_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    row = result.scalar_one_or_none()
    if row is not None and row.revoked_at is None:
        row.revoked_at = now_utc()
        row.revoked_reason = reason
        await db.flush()


# --------------------------------------------------------------------------
# Login rate limiting
# --------------------------------------------------------------------------

async def check_login_rate_limit(
    db: AsyncSession,
    email: str,
    ip_address: str | None,
) -> None:
    """Raise 429 if the email or IP has too many recent failures."""
    window_start = _naive_utc() - timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)

    email_result = await db.execute(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.email == email,
            LoginAttempt.success.is_(False),
            LoginAttempt.attempted_at >= window_start,
        )
    )
    email_failures = email_result.scalar_one() or 0
    if email_failures >= MAX_FAILED_ATTEMPTS_PER_EMAIL:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts for this account; try again later",
        )

    if ip_address:
        ip_result = await db.execute(
            select(func.count())
            .select_from(LoginAttempt)
            .where(
                LoginAttempt.ip_address == ip_address,
                LoginAttempt.success.is_(False),
                LoginAttempt.attempted_at >= window_start,
            )
        )
        ip_failures = ip_result.scalar_one() or 0
        if ip_failures >= MAX_FAILED_ATTEMPTS_PER_IP:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts from this address; try again later",
            )


async def record_login_attempt(
    db: AsyncSession,
    *,
    email: str,
    ip_address: str | None,
    success: bool,
    reason: str | None = None,
    tenant_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> LoginAttempt:
    attempt = LoginAttempt(
        email=email,
        ip_address=ip_address,
        success=success,
        attempted_at=_naive_utc(),
        reason=reason,
    )
    db.add(attempt)
    await db.flush()

    # Mirror the attempt into the tenant audit chain (spec §23/§95) so the
    # security-monitoring detectors see login events. Login attempts occur
    # before the org context is known, so tenant_id/actor_id may be None —
    # they are attached only when the user was successfully identified.
    if tenant_id is not None:
        from app.services.audit_service import record_event

        await record_event(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_type="user",
            action="LOGIN" if success else "LOGIN_FAILED",
            resource_type="session",
            metadata_json={"email": email, "ip_address": ip_address, "reason": reason},
            ip_address=ip_address,
        )

    return attempt