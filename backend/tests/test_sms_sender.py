"""Tests for the SMS notification channel (spec §44).

The gateway is never contacted for real: ``send_sms`` is exercised with a
patched settings object (disabled/enabled) and monkeypatched httpx behavior
is avoided entirely — instead we test the gating logic, user resolution,
preference opt-out, and outbox integration, mirroring the push-sender suite.
"""
import uuid
from dataclasses import dataclass

import pytest

from app.models.notification import NotificationPreference
from app.models.user import User
from app.services import sms_sender_service as sss


# --------------------------------------------------------------------------
# Settings fakes
# --------------------------------------------------------------------------


def _settings(*, base_url=None, api_key=None):
    return type(
        "S",
        (),
        {
            "sms_api_base_url": base_url,
            "sms_api_key": (
                type("Secret", (), {"get_secret_value": lambda self: api_key})()
                if api_key
                else None
            ),
            "sms_sender_id": "ContractOS",
            "sms_timeout_seconds": 5.0,
        },
    )()


# --------------------------------------------------------------------------
# send_sms gating
# --------------------------------------------------------------------------


async def test_send_sms_disabled_without_gateway(monkeypatch):
    """No gateway credentials: reported disabled, never an error."""
    monkeypatch.setattr(sss, "get_settings_lazy", lambda: _settings())

    result = await sss.send_sms("+94771234567", "Hello")
    assert result.disabled is True
    assert result.sent is False


async def test_send_sms_rejects_missing_recipient(monkeypatch):
    monkeypatch.setattr(
        sss,
        "get_settings_lazy",
        lambda: _settings(base_url="https://sms.example.com", api_key="k"),
    )
    result = await sss.send_sms("", "Hello")
    assert result.sent is False
    assert result.error == "missing_recipient_or_body"


async def test_send_sms_truncates_long_body(monkeypatch):
    """Bodies are capped inside one SMS segment."""
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message_id": "m-1"}

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            captured["payload"] = json
            return _FakeResponse()

    class _FakeHttpx:
        AsyncClient = _FakeClient
        HTTPStatusError = Exception
        HTTPError = Exception

    monkeypatch.setattr(
        sss,
        "get_settings_lazy",
        lambda: _settings(base_url="https://sms.example.com", api_key="k"),
    )
    monkeypatch.setattr(sss, "httpx", _FakeHttpx)

    result = await sss.send_sms("+94771234567", "x" * 1000)
    assert result.sent is True
    assert result.provider_message_id == "m-1"
    assert len(captured["payload"]["text"]) == 300


# --------------------------------------------------------------------------
# send_sms_to_user resolution + preferences
# --------------------------------------------------------------------------


async def test_user_without_phone_is_skipped(db_session, test_user):
    result = await sss.send_sms_to_user(
        db_session, user_id=test_user.id, body="Hello"
    )
    assert result.sent is False
    assert result.error == "no_phone_number"


async def test_user_with_phone_and_opt_in_sends(db_session, test_user):
    db_session.add(NotificationPreference(
        user_id=test_user.id,
        organization_id=uuid.uuid4(),
        sms_enabled=True,
    ))
    test_user.phone = "+94771234567"
    await db_session.flush()

    calls = []

    async def fake_send(phone, body, **kwargs):
        calls.append((phone, body))
        return sss.SmsDeliveryResult(sent=True, provider_message_id="m-2")

    import unittest.mock as mock

    with mock.patch.object(sss, "send_sms", fake_send):
        result = await sss.send_sms_to_user(
            db_session, user_id=test_user.id, body="Sign now"
        )

    assert result.sent is True
    assert calls == [("+94771234567", "Sign now")]


async def test_user_opted_out_is_skipped(db_session, test_user):
    test_user.phone = "+94771234567"
    db_session.add(NotificationPreference(
        user_id=test_user.id,
        organization_id=uuid.uuid4(),
        sms_enabled=False,
    ))
    await db_session.flush()

    result = await sss.send_sms_to_user(
        db_session, user_id=test_user.id, body="Hello"
    )
    assert result.sent is False
    assert result.error == "user_opted_out"


# --------------------------------------------------------------------------
# Outbox integration
# --------------------------------------------------------------------------


async def test_outbox_dispatch_calls_sms_sender(db_session, test_user, monkeypatch):
    """dispatch_event's step 2.6 reaches the SMS sender; lifecycle intact."""
    from app.models.event_outbox import OutboxEvent
    from app.services import event_outbox_service as eos

    calls = {}

    async def fake_send(db, **kwargs):
        calls.update(kwargs)
        return sss.SmsDeliveryResult(sent=True)

    monkeypatch.setattr(sss, "send_sms_to_user", fake_send)

    event = OutboxEvent(
        tenant_id=None,
        event_type="signature.requested",
        aggregate_type="signing_session",
        aggregate_id=None,
        payload={
            "recipient_user_id": str(test_user.id),
            "subject": "Please sign",
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
    assert calls["body"] == "Please sign"


async def test_outbox_dispatch_survives_sms_failure(db_session, test_user, monkeypatch):
    """An SMS gateway outage must never fail the outbox event."""

    async def boom(db, **kwargs):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(sss, "send_sms_to_user", boom)

    from app.models.event_outbox import OutboxEvent
    from app.services import event_outbox_service as eos

    event = OutboxEvent(
        tenant_id=None,
        event_type="approval.required",
        aggregate_type="approval",
        aggregate_id=None,
        payload={"recipient_user_id": str(test_user.id), "subject": "Approve"},
        status="pending",
    )
    db_session.add(event)
    await db_session.flush()

    ok = await eos.dispatch_event(db_session, event)
    await db_session.flush()

    assert ok is True
    assert event.status == "published"
