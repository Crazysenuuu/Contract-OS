"""KYC provider unit tests (spec 24.4).

Covers the provider abstraction contract: mock flow (start → complete →
decline), Stripe Identity REST adapter (mocked httpx), resolver behavior,
and Stripe webhook signature verification.
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from app.services.kyc_provider import (
    KYCProviderError,
    MockKYCProvider,
    StripeIdentityProvider,
    resolve_kyc_provider,
    reset_kyc_provider_cache,
)


@pytest.fixture(autouse=True)
def _fresh_providers():
    """Give every test fresh provider singletons."""
    reset_kyc_provider_cache()
    yield
    reset_kyc_provider_cache()


# --- MockKYCProvider ------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_start_and_complete_with_correct_code():
    provider = MockKYCProvider()
    session = await provider.create_verification_session(
        external_party_id=__import__("uuid").uuid4(),
        signatory_name="Jane Guest",
        signatory_email="jane@example.com",
    )
    assert session.session_id.startswith("vs_mock_")
    assert session.debug_code  # dev affordance present

    result = await provider.complete_verification(
        session_id=session.session_id,
        submitted_code=session.debug_code,
    )
    assert result.verified is True
    assert result.status == "verified"


@pytest.mark.asyncio
async def test_mock_complete_with_wrong_code_stays_pending():
    provider = MockKYCProvider()
    session = await provider.create_verification_session(
        external_party_id=__import__("uuid").uuid4(),
        signatory_name="Jane Guest",
        signatory_email="jane@example.com",
    )

    result = await provider.complete_verification(
        session_id=session.session_id,
        submitted_code="WRONG-CODE",
    )
    assert result.verified is False
    assert result.status == "pending"

    # The session is still completable with the right code afterwards.
    retry = await provider.complete_verification(
        session_id=session.session_id,
        submitted_code=session.debug_code,
    )
    assert retry.verified is True


@pytest.mark.asyncio
async def test_mock_decline_code_marks_failed():
    provider = MockKYCProvider()
    session = await provider.create_verification_session(
        external_party_id=__import__("uuid").uuid4(),
        signatory_name="Jane Guest",
        signatory_email="jane@example.com",
    )

    result = await provider.complete_verification(
        session_id=session.session_id,
        submitted_code=MockKYCProvider.DECLINE_CODE,
    )
    assert result.verified is False
    assert result.status == "failed"
    assert result.failure_reason


@pytest.mark.asyncio
async def test_mock_unknown_session_raises():
    provider = MockKYCProvider()
    with pytest.raises(KYCProviderError):
        await provider.complete_verification(session_id="vs_nope")


# --- StripeIdentityProvider ----------------------------------------------


def _stripe_session_response():
    return {
        "id": "vs_1NuN4zLkdIwHu7ixleE6HvkI",
        "object": "identity.verification_session",
        "status": "requires_input",
        "url": "https://verify.stripe.com/session/vs_test",
        "client_secret": "vs_secret_test",
        "livemode": False,
        "type": "document",
    }


@pytest.mark.asyncio
async def test_stripe_create_session_success():
    provider = StripeIdentityProvider(api_key="sk_test_123")
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = _stripe_session_response()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.post.return_value = response

        result = await provider.create_verification_session(
            external_party_id=__import__("uuid").uuid4(),
            signatory_name="Jane Guest",
            signatory_email="jane@example.com",
        )

    assert result.session_id == "vs_1NuN4zLkdIwHu7ixleE6HvkI"
    assert result.url == "https://verify.stripe.com/session/vs_test"
    assert result.status == "requires_input"

    # The create call must request a document check with matching selfie.
    _, kwargs = mock_client.post.call_args
    form = kwargs["data"]
    assert form["type"] == "document"
    assert form["options[document][require_matching_selfie]"] == "true"


@pytest.mark.asyncio
async def test_stripe_create_session_http_error_raises():
    provider = StripeIdentityProvider(api_key="sk_test_123")
    response = MagicMock()
    response.status_code = 401
    response.json.return_value = {"error": {"message": "Invalid API key"}}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.post.return_value = response

        with pytest.raises(KYCProviderError, match="Invalid API key"):
            await provider.create_verification_session(
                external_party_id=__import__("uuid").uuid4(),
                signatory_name="Jane",
                signatory_email="jane@example.com",
            )


@pytest.mark.asyncio
async def test_stripe_get_status_verified():
    provider = StripeIdentityProvider(api_key="sk_test_123")
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        **_stripe_session_response(),
        "status": "verified",
        "last_verification_report": {"id": "vr_123"},
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.get.return_value = response

        result = await provider.get_session_status("vs_abc")

    assert result.verified is True
    assert result.details.get("verification_report") == "vr_123"


@pytest.mark.asyncio
async def test_stripe_get_status_failed_reports_reason():
    provider = StripeIdentityProvider(api_key="sk_test_123")
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        **_stripe_session_response(),
        "status": "requires_input",
        "last_error": {"reason": "document_check_failed"},
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.get.return_value = response

        result = await provider.get_session_status("vs_abc")

    assert result.verified is False
    assert result.failure_reason == "document_check_failed"


# --- Resolver ---------------------------------------------------------------


def test_resolver_defaults_to_mock(monkeypatch):
    monkeypatch.delenv("KYC_PROVIDER", raising=False)
    provider = resolve_kyc_provider()
    assert isinstance(provider, MockKYCProvider)


def test_resolver_mock_is_cached_singleton(monkeypatch):
    monkeypatch.delenv("KYC_PROVIDER", raising=False)
    assert resolve_kyc_provider() is resolve_kyc_provider()


def test_resolver_stripe_without_credentials_falls_back(monkeypatch):
    monkeypatch.setenv("KYC_PROVIDER", "stripe_identity")
    monkeypatch.setattr(
        "app.core.config.get_settings_lazy",
        lambda: MagicMock(stripe_identity_api_key=None),
    )
    provider = resolve_kyc_provider()
    assert isinstance(provider, MockKYCProvider)


def test_resolver_unknown_name_falls_back(monkeypatch):
    monkeypatch.setenv("KYC_PROVIDER", "onfido")
    provider = resolve_kyc_provider()
    assert isinstance(provider, MockKYCProvider)


# --- Stripe webhook signature verification ---------------------------------


def _signed_header(payload: str, secret: str, ts: int | None = None) -> str:
    import hashlib
    import hmac as hmac_mod

    ts = ts if ts is not None else int(time.time())
    signed = f"{ts}.{payload}".encode()
    sig = hmac_mod.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_verify_stripe_signature_accepts_valid():
    from app.services.kyc_provider import verify_stripe_signature

    payload = json.dumps({"type": "identity.verification_session.verified"})
    secret = "whsec_test"
    header = _signed_header(payload, secret)
    assert verify_stripe_signature(payload, header, secret) is True


def test_verify_stripe_signature_rejects_tampered_payload():
    from app.services.kyc_provider import verify_stripe_signature

    payload = json.dumps({"type": "identity.verification_session.verified"})
    secret = "whsec_test"
    header = _signed_header(payload, secret)
    assert verify_stripe_signature(payload + " tampered", header, secret) is False


def test_verify_stripe_signature_rejects_stale_timestamp():
    from app.services.kyc_provider import verify_stripe_signature

    payload = "{}"
    secret = "whsec_test"
    stale_ts = int(time.time()) - 10_000  # far outside tolerance
    header = _signed_header(payload, secret, ts=stale_ts)
    assert verify_stripe_signature(payload, header, secret) is False


def test_verify_stripe_signature_rejects_malformed_header():
    from app.services.kyc_provider import verify_stripe_signature

    assert verify_stripe_signature("{}", "garbage", "whsec_test") is False
    assert verify_stripe_signature("{}", "", "whsec_test") is False
    assert verify_stripe_signature("{}", "t=123,v1=abc", "") is False
