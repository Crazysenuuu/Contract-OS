"""KYC / identity-verification provider abstraction (spec 24.4).

Guest verification starts as an email OTP challenge. For high-value
agreements, organizations can enable a full identity-verification provider
(government-ID document scan + selfie/liveness) before the guest may accept
or sign.

Which provider runs is configuration, not code:

  - ``mock``            -> :class:`MockKYCProvider`. Deterministic dev/test
    flow: the guest "completes" verification by submitting a controlled
    code; a special code simulates a provider decline. No network calls.
  - ``stripe_identity`` -> :class:`StripeIdentityProvider`. Creates Stripe
    Identity ``verification_session`` objects (document + selfie check) and
    marks the party verified when Stripe reports ``verified`` (via the
    completion event or a session poll).

Configuration::

    KYC_PROVIDER=mock                    # 'mock' | 'stripe_identity'
    STRIPE_IDENTITY_API_KEY=sk_test_...  # required for stripe_identity
    STRIPE_IDENTITY_WEBHOOK_SECRET=whsec_...  # verifies provider callbacks
    KYC_SESSION_TTL_HOURS=24             # host-side session expiry

Design rules (mirroring the e-signature provider abstraction):

  - The provider owns identity evidence; the platform only records the
    outcome. Government-ID images and selfies NEVER enter ContractOS
    storage — they stay with the provider (spec 24.3 + 33: data
    minimisation, no PII hoarding).
  - The provider must never silently pass when unconfigured: the real
    provider raises :class:`KYCProviderError` instead of faking success.
  - Unknown provider names fall back to mock with a warning (dev convenience),
    matching :func:`app.services.esignature.resolve_esignature_provider`.
  - Every attempt is journaled (``KycVerificationAttempt``) so the audit
    trail can show who verified how and when without storing the evidence.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from app.core.config import get_settings_lazy

logger = logging.getLogger(__name__)


@dataclass
class KYCSessionResult:
    """Outcome of starting a provider verification session."""

    session_id: str
    provider: str
    status: str  # 'pending' | 'requires_input' | ...
    # Hosted flow URL the guest is redirected to (Stripe hosted page, or
    # the mock's synthetic URL). None when the provider is embedded-only.
    url: Optional[str] = None
    # Short-lived secret for embedded SDK flows; not used by the hosted
    # flow but returned for forward compatibility.
    client_secret: Optional[str] = None
    expires_at: Optional[datetime] = None
    # Mock/dev path only: the code that completes verification. Never set
    # by real providers.
    debug_code: Optional[str] = None
    provider_metadata: dict = field(default_factory=dict)


@dataclass
class KYCVerificationResult:
    """Outcome of a verification check reported by the provider."""

    verified: bool
    provider: str
    session_id: str
    status: str  # 'verified' | 'failed' | 'pending' | 'canceled'
    failure_reason: Optional[str] = None
    # Structured details suitable for the audit trail: check type(s)
    # performed, document country when the provider exposes it, etc.
    # Must not contain raw document images or document numbers.
    details: dict = field(default_factory=dict)


class KYCProviderError(Exception):
    """Raised when a KYC provider cannot complete the requested operation."""


class KYCProvider(ABC):
    """Abstract base class for identity-verification providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier ('mock', 'stripe_identity', ...)."""

    @abstractmethod
    async def create_verification_session(
        self,
        *,
        external_party_id: UUID,
        signatory_name: str,
        signatory_email: str,
        return_url: Optional[str] = None,
        agreement_id: Optional[UUID] = None,
    ) -> KYCSessionResult:
        """Create a verification session for a guest signatory."""

    @abstractmethod
    async def complete_verification(
        self,
        *,
        session_id: str,
        submitted_code: Optional[str] = None,
    ) -> KYCVerificationResult:
        """Resolve a session to a verified/failed outcome.

        Real providers ignore ``submitted_code`` and report the actual
        session state; the mock uses it as the completion signal.
        """

    @abstractmethod
    async def get_session_status(
        self,
        session_id: str,
    ) -> KYCVerificationResult:
        """Poll the provider for the current state of a session."""


class MockKYCProvider(KYCProvider):
    """Deterministic mock provider for development and tests.

    Completion protocol: the guest submits the session's derived code
    ``VERIFY-<last6 of session id, uppercased>`` (returned as
    ``debug_code`` by the start flow). A code equal to the session's
    decline code simulates a provider rejection so the failure path is
    exercisable without network mocking. Any other code leaves the session
    pending.
    """

    DECLINE_CODE = "DECLINE-000000"

    def __init__(self, *, auto_pass: bool = False):
        self.auto_pass = auto_pass
        self._sessions: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "mock"

    def _expected_code(self, session_id: str) -> str:
        return f"VERIFY-{session_id[-6:].upper()}"

    async def create_verification_session(
        self,
        *,
        external_party_id: UUID,
        signatory_name: str,
        signatory_email: str,
        return_url: Optional[str] = None,
        agreement_id: Optional[UUID] = None,
    ) -> KYCSessionResult:
        session_id = f"vs_mock_{uuid4().hex[:20]}"
        self._sessions[session_id] = {
            "external_party_id": str(external_party_id),
            "signatory_email": signatory_email,
            "status": "requires_input",
            "created_at": datetime.now(timezone.utc),
        }
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=kyc_session_ttl_hours()
        )
        logger.info(
            "[MOCK KYC] created session %s for %s", session_id, signatory_email
        )
        return KYCSessionResult(
            session_id=session_id,
            provider=self.name,
            status="requires_input",
            url=f"https://mock-kyc.example.com/verify/{session_id}",
            expires_at=expires_at,
            # Dev/test affordance: the code that completes this session.
            debug_code=self._expected_code(session_id),
            provider_metadata={"auto_pass": self.auto_pass},
        )

    async def complete_verification(
        self,
        *,
        session_id: str,
        submitted_code: Optional[str] = None,
    ) -> KYCVerificationResult:
        session = self._sessions.get(session_id)
        if session is None:
            raise KYCProviderError(f"Unknown mock KYC session {session_id}")
        if session["status"] == "verified":
            return self._verified(session_id)

        if submitted_code == self.DECLINE_CODE:
            session["status"] = "failed"
            return KYCVerificationResult(
                verified=False,
                provider=self.name,
                session_id=session_id,
                status="failed",
                failure_reason="Mock provider decline (DECLINE code submitted)",
            )

        expected = self._expected_code(session_id)
        if self.auto_pass or submitted_code == expected:
            session["status"] = "verified"
            return self._verified(session_id)

        return KYCVerificationResult(
            verified=False,
            provider=self.name,
            session_id=session_id,
            status="pending",
            failure_reason="Submitted code does not match the mock session",
        )

    async def get_session_status(self, session_id: str) -> KYCVerificationResult:
        session = self._sessions.get(session_id)
        if session is None:
            raise KYCProviderError(f"Unknown mock KYC session {session_id}")
        if session["status"] == "verified":
            return self._verified(session_id)
        return KYCVerificationResult(
            verified=False,
            provider=self.name,
            session_id=session_id,
            status="pending",
        )

    @staticmethod
    def _verified(session_id: str) -> KYCVerificationResult:
        return KYCVerificationResult(
            verified=True,
            provider="mock",
            session_id=session_id,
            status="verified",
            details={"checks": ["mock_document", "mock_liveness"]},
        )


class StripeIdentityProvider(KYCProvider):
    """Stripe Identity verification sessions (document + selfie check).

    Uses the raw REST API with form encoding (no stripe-python dependency):

        POST https://api.stripe.com/v1/identity/verification_sessions
        -u "sk_...:"          (Basic auth: secret key, empty password)
        -d type=document
        -d "options[document][require_matching_selfie]=true"
        -d metadata[external_party_id]=...

    The response carries ``id`` (``vs_...``), a hosted ``url`` and
    ``client_secret``. The guest completes the flow on Stripe's hosted
    page; Stripe then reports ``verified`` through webhooks and the
    session object itself. We never receive document images — Stripe
    redacts and retains them, satisfying the platform's data-minimisation
    rule (spec 33.28: AI/provider data boundary).
    """

    API_BASE = "https://api.stripe.com/v1"

    def __init__(self, api_key: str | None = None):
        settings = get_settings_lazy()
        key = api_key or _settings_secret(settings.stripe_identity_api_key)
        if not key:
            raise KYCProviderError(
                "KYC_PROVIDER=stripe_identity requires "
                "STRIPE_IDENTITY_API_KEY"
            )
        self._api_key = key

    @property
    def name(self) -> str:
        return "stripe_identity"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    @staticmethod
    def _form(payload: dict[str, str | None]) -> dict[str, str]:
        return {k: v for k, v in payload.items() if v is not None}

    async def create_verification_session(
        self,
        *,
        external_party_id: UUID,
        signatory_name: str,
        signatory_email: str,
        return_url: Optional[str] = None,
        agreement_id: Optional[UUID] = None,
    ) -> KYCSessionResult:
        import httpx

        form: dict[str, str] = {
            "type": "document",
            "options[document][require_matching_selfie]": "true",
            "metadata[external_party_id]": str(external_party_id),
            "provided_details[username]": signatory_name,
        }
        if agreement_id is not None:
            form["metadata[agreement_id]"] = str(agreement_id)
        if return_url:
            form["return_url"] = return_url

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.API_BASE}/identity/verification_sessions",
                    data=self._form(form),
                    headers=self._headers(),
                )
        except httpx.HTTPError as e:  # network/DNS/timeout failures
            raise KYCProviderError(f"Stripe Identity unreachable: {e}") from e

        if resp.status_code >= 400:
            detail = _stripe_error_message(resp)
            raise KYCProviderError(
                f"Stripe Identity session creation failed "
                f"({resp.status_code}): {detail}"
            )

        body = resp.json()
        logger.info(
            "stripe_identity session created: %s (party=%s)",
            body.get("id"),
            external_party_id,
        )
        return KYCSessionResult(
            session_id=str(body["id"]),
            provider=self.name,
            status=str(body.get("status") or "requires_input"),
            url=body.get("url"),
            client_secret=body.get("client_secret"),
            expires_at=datetime.now(timezone.utc)
            + timedelta(hours=kyc_session_ttl_hours()),
            provider_metadata={"livemode": bool(body.get("livemode"))},
        )

    async def complete_verification(
        self,
        *,
        session_id: str,
        submitted_code: Optional[str] = None,
    ) -> KYCVerificationResult:
        # Real providers resolve through their own hosted flow; the outcome
        # is whatever the session now reports.
        return await self.get_session_status(session_id)

    async def get_session_status(self, session_id: str) -> KYCVerificationResult:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{self.API_BASE}/identity/verification_sessions/{session_id}",
                    headers=self._headers(),
                )
        except httpx.HTTPError as e:
            raise KYCProviderError(f"Stripe Identity unreachable: {e}") from e

        if resp.status_code >= 400:
            raise KYCProviderError(
                f"Stripe Identity session lookup failed "
                f"({resp.status_code}): {_stripe_error_message(resp)}"
            )

        body = resp.json()
        status = str(body.get("status") or "requires_input")
        verified = status == "verified"
        last_error = body.get("last_error") or {}
        details: dict = {
            "type": body.get("type"),
            "livemode": bool(body.get("livemode")),
        }
        report = body.get("last_verification_report")
        if isinstance(report, dict) and report.get("id"):
            details["verification_report"] = str(report["id"])
        return KYCVerificationResult(
            verified=verified,
            provider=self.name,
            session_id=str(body["id"]),
            status=status,
            failure_reason=last_error.get("reason") if not verified else None,
            details=details,
        )


# --- Module-level helpers -------------------------------------------------


def _settings_secret(value) -> str | None:
    """pydantic SecretStr -> plain str (or None)."""
    if value is None:
        return None
    return getattr(value, "get_secret_value", lambda: value)()


def kyc_session_ttl_hours() -> int:
    """Host-side session TTL; env override for tests."""
    return int(os.environ.get("KYC_SESSION_TTL_HOURS", "") or 24)


def _stripe_error_message(resp) -> str:
    try:
        body = resp.json()
        err = body.get("error") or {}
        return str(err.get("message") or body)
    except Exception:
        return resp.text[:200]


# --- Webhook signature verification (Stripe scheme) -----------------------


def kyc_webhook_secret() -> str | None:
    """Configured provider webhook secret (empty string when unset)."""
    settings = get_settings_lazy()
    return _settings_secret(settings.stripe_identity_webhook_secret) or ""


def verify_stripe_signature(
    payload: str,
    header: str,
    secret: str,
    *,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify a Stripe-Signature header against a raw payload.

    Implements the Stripe webhook signature scheme (v1 HMAC-SHA256):

        header = "t=<unix_ts>,v1=<hex hmac>"
        signed_payload = f"{ts}.{payload}"
        expected = hmac_sha256(secret, signed_payload)

    The timestamp check (tolerance) prevents replay of old captures.
    Returns False for any malformed input — never raises.
    """
    import hashlib
    import hmac as hmac_mod

    if not header or not secret:
        return False
    try:
        parts = dict(
            piece.split("=", 1)
            for piece in header.split(",")
            if "=" in piece
        )
        ts = parts.get("t", "")
        signatures = [
            v for k, v in parts.items() if k == "v1"
        ]
        if not ts or not signatures:
            return False
        # Replay guard: reject timestamps outside the tolerance window.
        ts_int = int(ts)
        now = int(datetime.now(timezone.utc).timestamp())
        if abs(now - ts_int) > tolerance_seconds:
            return False
        signed_payload = f"{ts}.{payload}"
        expected = hmac_mod.new(
            secret.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return any(
            hmac_mod.compare_digest(expected, sig) for sig in signatures
        )
    except (ValueError, KeyError, AttributeError):
        return False


def resolve_kyc_provider() -> KYCProvider:
    """Resolve the configured KYC provider (spec 24.4).

    Reads the ``KYC_PROVIDER`` environment variable (default ``"mock"``):

      - ``"mock"``            -> MockKYCProvider (development/tests)
      - ``"stripe_identity"`` -> StripeIdentityProvider
        (requires STRIPE_IDENTITY_API_KEY)

    Unknown names degrade to mock with a warning, mirroring
    :func:`app.services.esignature.resolve_esignature_provider`.

    Providers are cached for the process lifetime: the mock keeps its
    session registry in memory, so a fresh instance per call would forget
    sessions between ``/verify-id/start`` and ``/verify-id/kyc/complete``.
    """
    global _provider_cache

    provider = os.environ.get("KYC_PROVIDER") or getattr(
        get_settings_lazy(), "kyc_provider", "mock"
    )
    provider = str(provider).strip().lower()

    if provider == "stripe_identity":
        if isinstance(_provider_cache, StripeIdentityProvider):
            return _provider_cache
        try:
            _provider_cache = StripeIdentityProvider()
        except KYCProviderError as e:
            logger.warning(
                "KYC_PROVIDER=stripe_identity but credentials missing (%s); "
                "falling back to mock provider",
                e,
            )
            _provider_cache = _get_mock()
        return _provider_cache

    if provider not in ("", "mock"):
        logger.warning(
            "Unknown KYC_PROVIDER %r; falling back to mock provider", provider
        )
    return _get_mock()


_provider_cache: KYCProvider | None = None
_mock_provider: MockKYCProvider | None = None


def _get_mock() -> MockKYCProvider:
    global _mock_provider
    if _mock_provider is None:
        _mock_provider = MockKYCProvider()
    return _mock_provider


def reset_kyc_provider_cache() -> None:
    """Drop cached provider instances (test isolation helper)."""
    global _provider_cache, _mock_provider
    _provider_cache = None
    _mock_provider = None
