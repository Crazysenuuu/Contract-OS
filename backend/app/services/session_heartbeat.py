"""
Session heartbeat middleware.

Updates a user's `last_seen_at` on the most recent active session as they
use the API. Throttled in-memory (one DB write per user per HEARTBEAT_INTERVAL)
so it stays cheap even under heavy traffic.
"""
import time
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import decode_access_token
from app.models.user import UserSession

HEARTBEAT_INTERVAL_SECONDS = 60

# user_id (str) -> last time we wrote last_seen_at
_last_heartbeat: dict[str, float] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def track_last_seen(authorization_header: str | None) -> None:
    if not authorization_header or not authorization_header.startswith("Bearer "):
        return

    token = authorization_header[7:]
    try:
        payload = decode_access_token(token)
    except Exception:
        return

    user_id = payload.get("sub")
    if not user_id:
        return

    now_ts = time.time()
    if now_ts - _last_heartbeat.get(user_id, 0) < HEARTBEAT_INTERVAL_SECONDS:
        return
    _last_heartbeat[user_id] = now_ts

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserSession)
                .where(
                    UserSession.user_id == user_id,
                    UserSession.status == "active",
                )
                .order_by(UserSession.login_at.desc())
                .limit(1)
            )
            session = result.scalar_one_or_none()
            if session is not None:
                session.last_seen_at = _utcnow()
                await db.commit()
    except Exception:
        # Heartbeat must never break the request it runs inside.
        pass