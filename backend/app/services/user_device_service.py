"""User device service for mobile push registration (spec M2.02 / section 25-26).

The authenticated user is always taken from the request JWT — never from a
client-supplied user_id. `register_device` upserts by (user_id, device_id)
so push-token rotation on app start/resume does not create duplicate rows.
"""

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_device import UserDevice


async def register_device(
    db: AsyncSession,
    *,
    user_id,
    device_id: str,
    platform: str,
    push_token: str | None,
    app_version: str | None,
) -> UserDevice:
    """Upsert a device record for the authenticated user."""
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(UserDevice).where(
            UserDevice.user_id == user_id,
            UserDevice.device_id == device_id,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.platform = platform
        existing.push_token = push_token
        existing.app_version = app_version
        existing.last_seen_at = now
        existing.revoked_at = None
        existing.revoked_reason = None
        device = existing
    else:
        device = UserDevice(
            user_id=user_id,
            device_id=device_id,
            platform=platform,
            push_token=push_token,
            app_version=app_version,
            last_seen_at=now,
        )
        db.add(device)
    await db.flush()
    return device


async def revoke_device(
    db: AsyncSession, *, user_id, device_id: str, reason: str = "logout"
) -> None:
    result = await db.execute(
        select(UserDevice).where(
            UserDevice.user_id == user_id,
            UserDevice.device_id == device_id,
        )
    )
    device = result.scalar_one_or_none()
    if device is None:
        return
    device.revoked_at = datetime.now(timezone.utc)
    device.revoked_reason = reason
    device.push_token = None


async def list_devices(
    db: AsyncSession, *, user_id
) -> list[UserDevice]:
    result = await db.execute(
        select(UserDevice)
        .where(
            UserDevice.user_id == user_id,
            UserDevice.revoked_at.is_(None),
        )
        .order_by(UserDevice.last_seen_at.desc())
    )
    return list(result.scalars().all())