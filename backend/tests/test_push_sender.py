"""Tests for the FCM push sender (spec 2.02 §24-26).

firebase_admin is never imported for real here: the module-level
``_load_fcm_module`` hook is patched with an in-memory fake of the small
SDK surface the sender uses (MulticastMessage, send_each_for_multicast).
Without FCM_CREDENTIALS_JSON the sender must be a clean no-op.
"""
import uuid
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.user_device import UserDevice
from app.services import push_sender_service as pss


# --------------------------------------------------------------------------
# Fake firebase_admin surface
# --------------------------------------------------------------------------


@dataclass
class _FakeResponse:
    success: bool
    exception: Exception | None = None


class _FakeBatchResponse:
    def __init__(self, responses):
        self.responses = responses


@dataclass
class _FakeMulticastMessage:
    tokens: list
    data: dict
    android: object = None
    apns: object = None
    sent_batches: list = field(default_factory=list)


class _FakeMessaging:
    """Records the last multicast; behavior configured per test."""

    last_message: _FakeMulticastMessage | None = None
    next_responses: list = []

    MulticastMessage = staticmethod(
        lambda **kwargs: _FakeMulticastMessage(**kwargs)
    )
    AndroidConfig = staticmethod(lambda **kwargs: kwargs)
    APNSConfig = staticmethod(lambda **kwargs: kwargs)
    APNSPayload = staticmethod(lambda **kwargs: kwargs)
    Aps = staticmethod(lambda **kwargs: kwargs)
    ApsAlert = staticmethod(lambda **kwargs: kwargs)

    @staticmethod
    def send_each_for_multicast(message):
        _FakeMessaging.last_message = message
        return _FakeBatchResponse(_FakeMessaging.next_responses)


@pytest.fixture
def fake_fcm(monkeypatch):
    """Patch the firebase_admin surface and enable push settings."""
    _FakeMessaging.last_message = None
    _FakeMessaging.next_responses = []

    mods = ("firebase_admin", object(), _FakeMessaging)
    monkeypatch.setattr(pss, "_load_fcm_module", lambda: mods)
    monkeypatch.setattr(
        pss, "_initialize_app", lambda cred_json: object()
    )
    monkeypatch.setattr(
        pss,
        "get_settings_lazy",
        lambda: type(
            "S",
            (),
            {
                "fcm_credentials_json": type(
                    "Secret", (), {"get_secret_value": lambda self: "{}"}
                )()
            },
        )(),
    )
    return _FakeMessaging


# --------------------------------------------------------------------------
# Device fixtures
# --------------------------------------------------------------------------


async def _add_device(
    db_session,
    user_id,
    token,
    *,
    device_id=None,
    revoked=False,
):
    device = UserDevice(
        user_id=user_id,
        device_id=device_id or f"dev-{token[:8]}",
        platform="android",
        push_token=None if revoked else token,
        app_version="1.0.0",
    )
    if revoked:
        device.revoked_at = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
    db_session.add(device)
    await db_session.flush()
    return device


# --------------------------------------------------------------------------
# Disabled / enabled gating
# --------------------------------------------------------------------------


async def test_disabled_without_credentials(db_session, test_user):
    """No FCM_CREDENTIALS_JSON: sender reports disabled, sends nothing."""
    result = await pss.send_push_to_user(
        db_session,
        user_id=test_user.id,
        notification_id="n1",
        event_type="signature.completed",
        notification_type="signature_completed",
        title="t",
        body="b",
    )
    assert result.disabled is True
    assert result.delivered == 0


async def test_enabled_but_no_devices_is_clean_noop(
    db_session, test_user, fake_fcm
):
    result = await pss.send_push_to_user(
        db_session,
        user_id=test_user.id,
        notification_id="n1",
        event_type="agreement.status_changed",
        notification_type="workflow_transition",
        title="t",
        body="b",
    )
    assert result.disabled is False
    assert result.delivered == 0
    assert fake_fcm.last_message is None


# --------------------------------------------------------------------------
# Targeting + payload contract
# --------------------------------------------------------------------------


async def test_sends_to_all_active_tokens_only(
    db_session, test_user, fake_fcm
):
    other_user_id = uuid.uuid4()
    await _add_device(db_session, test_user.id, "tok-A")
    await _add_device(db_session, test_user.id, "tok-B", device_id="dev-2")
    await _add_device(db_session, test_user.id, "tok-dead", revoked=True,
                      device_id="dev-3")
    await _add_device(db_session, other_user_id, "tok-other",
                      device_id="dev-4")
    fake_fcm.next_responses = [
        _FakeResponse(success=True),
        _FakeResponse(success=True),
    ]

    result = await pss.send_push_to_user(
        db_session,
        user_id=test_user.id,
        notification_id="n1",
        event_type="agreement.status_changed",
        notification_type="workflow_transition",
        title="Hello",
        body="World",
        payload={},
    )

    assert result.delivered == 2
    assert result.sent_to_tokens == ["tok-A", "tok-B"]
    msg = fake_fcm.last_message
    assert msg is not None
    assert sorted(msg.tokens) == ["tok-A", "tok-B"]
    # No cross-user leakage.
    assert "tok-other" not in msg.tokens


async def test_payload_matches_mobile_banner_contract(
    db_session, test_user, fake_fcm
):
    """The data keys are the contract push_banner_controller.dart parses."""
    await _add_device(db_session, test_user.id, "tok-A")
    fake_fcm.next_responses = [_FakeResponse(success=True)]

    agreement_id = str(uuid.uuid4())
    await pss.send_push_to_user(
        db_session,
        user_id=test_user.id,
        notification_id="evt-77",
        event_type="signature.completed",
        notification_type="signature_completed",
        title="Sign",
        body="Please review and sign",
        payload={"agreement_id": agreement_id},
    )

    data = fake_fcm.last_message.data
    assert data["notification_id"] == "evt-77"
    assert data["type"] == "signature_completed"
    assert data["title"] == "Sign"
    assert data["body"] == "Please review and sign"
    assert data["route"] == f"/agreements/{agreement_id}"
    assert data["event_type"] == "signature.completed"
    assert "token" not in data


async def test_signing_payload_carries_token_and_signing_route(
    db_session, test_user, fake_fcm
):
    """Signing requests drive the mobile biometric step-up: type must
    contain 'sign' and the payload must carry the signer token + route."""
    await _add_device(db_session, test_user.id, "tok-A")
    fake_fcm.next_responses = [_FakeResponse(success=True)]

    await pss.send_push_to_user(
        db_session,
        user_id=test_user.id,
        notification_id="evt-9",
        event_type="signature.requested",
        notification_type="signature_request",
        title="Signature requested",
        body="Tap to sign",
        payload={"signer_token": "st-123"},
    )

    data = fake_fcm.last_message.data
    assert "sign" in data["type"]
    assert data["token"] == "st-123"
    assert data["route"] == "/signing/st-123"


# --------------------------------------------------------------------------
# Failure handling / dead tokens
# --------------------------------------------------------------------------


async def test_partial_failure_keeps_live_tokens_and_cleans_dead(
    db_session, test_user, fake_fcm
):
    await _add_device(db_session, test_user.id, "tok-live")
    await _add_device(db_session, test_user.id, "tok-dead", device_id="dev-2")
    fake_fcm.next_responses = [
        _FakeResponse(success=True),
        _FakeResponse(
            success=False,
            exception=type(
                "E", (Exception,), {"code": "registration-token-not-registered"}
            )("deleted"),
        ),
    ]

    result = await pss.send_push_to_user(
        db_session,
        user_id=test_user.id,
        notification_id="n1",
        event_type="agreement.status_changed",
        notification_type="workflow_transition",
        title="t",
        body="b",
    )

    assert result.sent_to_tokens == ["tok-live"]
    assert result.failed_tokens == ["tok-dead"]
    assert result.cleaned_tokens == ["tok-dead"]

    # The dead token is nulled in the DB; the device row survives.
    rows = (
        await db_session.execute(
            select(UserDevice).where(UserDevice.user_id == test_user.id)
        )
    ).scalars().all()
    by_device = {d.device_id: d.push_token for d in rows}
    assert by_device["dev-tok-live"] == "tok-live"
    assert by_device["dev-2"] is None


async def test_transient_error_keeps_token(db_session, test_user, fake_fcm):
    """Quota/5xx-style errors must NOT wipe the token."""
    await _add_device(db_session, test_user.id, "tok-1")
    fake_fcm.next_responses = [
        _FakeResponse(
            success=False,
            exception=type("E", (Exception,), {"code": "internal-error"})(
                "backend busy"
            ),
        )
    ]

    result = await pss.send_push_to_user(
        db_session,
        user_id=test_user.id,
        notification_id="n1",
        event_type="agreement.status_changed",
        notification_type="workflow_transition",
        title="t",
        body="b",
    )

    assert result.failed_tokens == ["tok-1"]
    assert result.cleaned_tokens == []
    row = (
        await db_session.execute(
            select(UserDevice).where(UserDevice.user_id == test_user.id)
        )
    ).scalars().one()
    assert row.push_token == "tok-1"


async def test_batch_sdk_failure_reported_not_raised(
    db_session, test_user, fake_fcm
):
    """A whole-batch FCM outage must not raise into the outbox loop."""
    await _add_device(db_session, test_user.id, "tok-1")

    def boom(message):
        raise RuntimeError("fcm down")

    with patch.object(fake_fcm, "send_each_for_multicast", boom):
        result = await pss.send_push_to_user(
            db_session,
            user_id=test_user.id,
            notification_id="n1",
            event_type="agreement.status_changed",
            notification_type="workflow_transition",
            title="t",
            body="b",
        )

    assert result.delivered == 0
    assert "fcm down" in result.error


# --------------------------------------------------------------------------
# Outbox integration
# --------------------------------------------------------------------------


async def test_outbox_dispatch_calls_push_sender(
    db_session, test_user, fake_fcm, monkeypatch
):
    """dispatch_event's step 2.5 reaches the push sender with the mapped
    notification type and recipient; outbox lifecycle is untouched."""
    from app.models.event_outbox import OutboxEvent
    from app.services import event_outbox_service as eos

    await _add_device(db_session, test_user.id, "tok-A")
    fake_fcm.next_responses = [_FakeResponse(success=True)]

    calls = {}

    async def fake_send(db, **kwargs):
        calls.update(kwargs)
        return pss.PushDeliveryResult(sent_to_tokens=["tok-A"])

    monkeypatch.setattr(pss, "send_push_to_user", fake_send)

    event = OutboxEvent(
        tenant_id=None,  # no tenant: skips Notification row, still pushes
        event_type="signature.requested",
        aggregate_type="signing_session",
        aggregate_id=None,
        payload={
            "recipient_user_id": str(test_user.id),
            "subject": "Please sign",
            "signer_token": "st-1",
        },
        status="pending",
    )
    db_session.add(event)
    await db_session.flush()

    ok = await eos.dispatch_event(db_session, event)
    await db_session.flush()

    assert ok is True
    assert event.status == "published"
    assert calls["user_id"] == test_user.id
    assert calls["notification_id"] == str(event.id)
    assert calls["notification_type"] == "signature_request"
    assert calls["title"] == "Please sign"
