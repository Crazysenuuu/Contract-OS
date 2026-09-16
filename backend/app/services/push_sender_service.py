"""Push notification sender (spec 2.02 §24-26): outbox -> FCM -> devices.

Delivers outbox-driven notifications to the registered device tokens of a
recipient user, using the Firebase Admin SDK (FCM HTTP v1).

Design constraints, mirroring the rest of the delivery stack:

  - Optional by construction: without ``FCM_CREDENTIALS_json`` the sender
    reports itself disabled and delivery is skipped — WebSocket and email
    remain the delivery paths. A missing credential must never break the
    outbox.
  - Best-effort per token: one bad token (uninstalled app, rotated away,
    expired) must not fail the batch. Dead tokens are cleaned up in the
    devices table (push_token -> NULL) so the next dispatch skips them.
  - The data payload is the contract consumed by the mobile push-banner
    layer (push_banner_controller.dart): notification_id, type, title,
    body, route, token. ``type`` containing ``sign`` drives the mobile
    biometric step-up, so signing requests must set it accordingly.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings_lazy
from app.models.user_device import UserDevice

logger = logging.getLogger(__name__)

# FCM error strings that mark a token permanently dead. Others (timeouts,
# quota, 5xx) are transient: leave the token in place and let the next
# outbox retry deliver.
_DEAD_TOKEN_ERRORS = {
    "registration-token-not-registered",
    "invalid-registration-token",
    "invalid-argument",
    "unregistered",
    "messaging/registration-token-not-registered",
}

_APNS_BAD_REASON = "BadDeviceToken"


@dataclass
class PushDeliveryResult:
    sent_to_tokens: list[str] = field(default_factory=list)
    failed_tokens: list[str] = field(default_factory=list)
    cleaned_tokens: list[str] = field(default_factory=list)
    disabled: bool = False
    error: str | None = None

    @property
    def delivered(self) -> int:
        return len(self.sent_to_tokens)


# --------------------------------------------------------------------------
# Credential / SDK plumbing (lazy — firebase_admin imports are slow and
# optional; tests patch the module-level hooks, not the SDK itself).
# --------------------------------------------------------------------------

_credential_cache: dict[str, object] = {}
_initialize_hook = None  # tests may inject; None = use firebase_admin


def _load_fcm_module():
    """Import firebase_admin lazily; None when not installed."""
    try:
        import firebase_admin
        from firebase_admin import credentials, messaging

        return firebase_admin, credentials, messaging
    except Exception:  # noqa: BLE001 — ImportError and pyo3 panic guard
        return None


def _initialize_app(credentials_json: str):
    """Build (and cache) the firebase_admin App for this credential set."""
    cached = _credential_cache.get(credentials_json)
    if cached is not None:
        return cached

    mods = _load_fcm_module()
    if mods is None:
        raise RuntimeError("firebase_admin is not installed")
    firebase_admin, credentials, _messaging = mods

    cred = credentials.Certificate(json.loads(credentials_json))
    app = firebase_admin.initialize_app(cred, name="contractos-push")
    _credential_cache[credentials_json] = app
    return app


def is_push_enabled() -> bool:
    """True when FCM credentials are configured."""
    settings = get_settings_lazy()
    secret = settings.fcm_credentials_json
    return bool(secret and secret.get_secret_value().strip())


def _messaging_client():
    """Return the messaging module, initializing the app once, or None."""
    settings = get_settings_lazy()
    secret = settings.fcm_credentials_json
    if not secret or not secret.get_secret_value().strip():
        return None

    mods = _load_fcm_module()
    if mods is None:
        logger.warning(
            "FCM credentials configured but firebase_admin is not "
            "installed — push delivery disabled"
        )
        return None
    _firebase_admin, _credentials, messaging = mods

    if _initialize_hook is not None:  # test seam
        _initialize_hook()
    else:
        _initialize_app(secret.get_secret_value())
    return messaging


# --------------------------------------------------------------------------
# Payload contract (must match mobile push_banner_controller.dart)
# --------------------------------------------------------------------------


def build_push_payload(
    *,
    notification_id: str | None,
    event_type: str,
    notification_type: str,
    title: str,
    body: str,
    payload: dict,
) -> dict:
    """Build the FCM data payload consumed by the mobile app.

    The mobile parser reads data.notification_id / type / title / body /
    route and data.token; type containing "sign" triggers the biometric
    step-up on the device.
    """
    agreement_id = payload.get("agreement_id")
    signer_token = payload.get("signer_token") or payload.get("token")

    route = None
    if signer_token:
        route = f"/signing/{signer_token}"
    elif agreement_id:
        route = f"/agreements/{agreement_id}"

    data: dict[str, str] = {
        "notification_id": notification_id or "",
        "type": notification_type,
        "title": title,
        "body": body,
    }
    if route:
        data["route"] = route
    if signer_token:
        data["token"] = str(signer_token)

    # Keep original event type for observability.
    data["event_type"] = event_type
    return data


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------


async def get_active_device_tokens(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> list[str]:
    """Push tokens of the user's non-revoked, token-bearing devices."""
    result = await db.execute(
        select(UserDevice.push_token).where(
            UserDevice.user_id == user_id,
            UserDevice.revoked_at.is_(None),
            UserDevice.push_token.isnot(None),
            UserDevice.push_token != "",
        )
    )
    return [t for (t,) in result.all()]


async def send_push_to_user(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    notification_id: str | None,
    event_type: str,
    notification_type: str,
    title: str,
    body: str,
    payload: dict | None = None,
) -> PushDeliveryResult:
    """Deliver one notification to all of a user's registered devices.

    Never raises: every failure mode is reported in the result so the
    outbox dispatcher can keep its own lifecycle (event status/audit)
    independent of FCM's health.
    """
    payload = payload or {}
    result = PushDeliveryResult()

    messaging = _messaging_client()
    if messaging is None:
        result.disabled = True
        return result

    tokens = await get_active_device_tokens(db, user_id=user_id)
    if not tokens:
        return result

    data = build_push_payload(
        notification_id=notification_id,
        event_type=event_type,
        notification_type=notification_type,
        title=title,
        body=body,
        payload=payload,
    )

    # FCM accepts up to 500 tokens per batch send; device count per user is
    # far below that in practice.
    message = messaging.MulticastMessage(
        tokens=tokens,
        data=data,
        android=messaging.AndroidConfig(
            priority="high",
            # Collapse updates about the same notification into one tray
            # entry while the app is backgrounded.
            collapse_key=f"notification-{notification_id}"
            if notification_id
            else None,
        ),
        apns=messaging.APNSConfig(
            headers={"apns-push-type": "alert", "apns-priority": "10"},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    alert=messaging.ApsAlert(title=title, body=body),
                    # Banner content also lives in data for the in-app
                    # renderer; the aps alert covers the system tray.
                    sound="default",
                ),
            ),
        ),
    )

    try:
        response = messaging.send_each_for_multicast(message)
    except Exception as exc:  # noqa: BLE001 — transient FCM outage
        result.error = f"{type(exc).__name__}: {exc}"
        logger.warning("FCM batch send failed: %s", result.error)
        return result

    dead: list[str] = []
    for idx, resp in enumerate(response.responses):
        token = tokens[idx]
        if resp.success:
            result.sent_to_tokens.append(token)
            continue
        result.failed_tokens.append(token)
        err = getattr(resp.exception, "code", "") or ""
        msg = str(resp.exception)
        if err in _DEAD_TOKEN_ERRORS or _APNS_BAD_REASON in msg:
            dead.append(token)

    if dead:
        cleaned = await _clear_dead_tokens(db, user_id=user_id, tokens=dead)
        result.cleaned_tokens = cleaned
        logger.info(
            "Cleared %d dead push token(s) for user %s", len(cleaned), user_id
        )

    return result


async def _clear_dead_tokens(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    tokens: list[str],
) -> list[str]:
    """Null out dead push tokens (device row survives, future pushes skip)."""
    result = await db.execute(
        select(UserDevice).where(
            UserDevice.user_id == user_id,
            UserDevice.push_token.in_(tokens),
        )
    )
    cleaned: list[str] = []
    for device in result.scalars().all():
        cleaned.append(device.push_token)
        device.push_token = None
    await db.flush()
    return cleaned
