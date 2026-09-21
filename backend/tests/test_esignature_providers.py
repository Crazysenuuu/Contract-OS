"""Unit tests for e-signature providers (DocuSign + Adobe Sign).

All external API calls are mocked so the tests run without credentials.
"""

import base64
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.esignature import (
    AdobeSignProvider,
    DocuSignProvider,
    EnvelopeResult,
    MockESignatureProvider,
    SignerInfo,
    get_esignature_provider,
    resolve_esignature_provider,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def signers():
    return [
        SignerInfo(name="Alice", email="alice@example.com", role="signer", order=1),
        SignerInfo(name="Bob", email="bob@example.com", role="signer", order=2),
    ]


@pytest.fixture
def sample_pdf():
    """Minimal valid PDF bytes."""
    return b"%PDF-1.4 fake-pdf-content-for-testing"


# ===========================================================================
# MockESignatureProvider
# ===========================================================================


class TestMockESignatureProvider:
    """Verify the mock provider works end-to-end without any external calls."""

    @pytest.mark.asyncio
    async def test_create_envelope(self, signers, sample_pdf):
        provider = MockESignatureProvider()
        result = await provider.create_envelope(
            document_bytes=sample_pdf,
            document_name="test.pdf",
            signers=signers,
            subject="Please sign",
            message="Sign this",
        )
        assert isinstance(result, EnvelopeResult)
        assert result.envelope_id.startswith("mock-")
        assert result.status == "sent"
        assert result.signing_url is not None

    @pytest.mark.asyncio
    async def test_get_status(self, signers, sample_pdf):
        provider = MockESignatureProvider()
        result = await provider.create_envelope(
            document_bytes=sample_pdf,
            document_name="test.pdf",
            signers=signers,
            subject="Subject",
            message="Msg",
        )
        status = await provider.get_envelope_status(result.envelope_id)
        assert status["status"] == "sent"
        assert len(status["signers"]) == 2

    @pytest.mark.asyncio
    async def test_get_status_not_found(self):
        provider = MockESignatureProvider()
        status = await provider.get_envelope_status("nonexistent")
        assert status["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_get_signing_url(self, signers, sample_pdf):
        provider = MockESignatureProvider()
        result = await provider.create_envelope(
            document_bytes=sample_pdf,
            document_name="test.pdf",
            signers=signers,
            subject="Subject",
            message="Msg",
        )
        url = await provider.get_signing_url(result.envelope_id, "alice@example.com")
        assert url is not None
        assert "alice@example.com" in url

    @pytest.mark.asyncio
    async def test_get_signing_url_wrong_email(self, signers, sample_pdf):
        """Mock provider returns a URL for any email (no validation)."""
        provider = MockESignatureProvider()
        result = await provider.create_envelope(
            document_bytes=sample_pdf,
            document_name="test.pdf",
            signers=signers,
            subject="Subject",
            message="Msg",
        )
        url = await provider.get_signing_url(result.envelope_id, "nobody@example.com")
        # Mock always returns a URL — real providers validate the email
        assert url is not None

    @pytest.mark.asyncio
    async def test_cancel(self, signers, sample_pdf):
        provider = MockESignatureProvider()
        result = await provider.create_envelope(
            document_bytes=sample_pdf,
            document_name="test.pdf",
            signers=signers,
            subject="Subject",
            message="Msg",
        )
        cancelled = await provider.cancel_envelope(result.envelope_id)
        assert cancelled is True
        status = await provider.get_envelope_status(result.envelope_id)
        assert status["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_not_found(self):
        provider = MockESignatureProvider()
        cancelled = await provider.cancel_envelope("nonexistent")
        assert cancelled is False

    @pytest.mark.asyncio
    async def test_simulate_signing(self, signers, sample_pdf):
        provider = MockESignatureProvider()
        result = await provider.create_envelope(
            document_bytes=sample_pdf,
            document_name="test.pdf",
            signers=signers,
            subject="Subject",
            message="Msg",
        )
        # First signer
        r1 = await provider.simulate_signing(result.envelope_id, "alice@example.com")
        assert r1["status"] == "partially_signed"
        assert r1["signed_count"] == 1

        # Second signer → completed
        r2 = await provider.simulate_signing(result.envelope_id, "bob@example.com")
        assert r2["status"] == "completed"
        assert r2["signed_count"] == 2

    @pytest.mark.asyncio
    async def test_simulate_signing_not_found(self):
        provider = MockESignatureProvider()
        r = await provider.simulate_signing("nonexistent", "x@y.com")
        assert "error" in r


# ===========================================================================
# DocuSignProvider
# ===========================================================================


def _make_docusign_envelope_result():
    """Build a mock EnvelopesApi.create_envelope return value."""
    result = MagicMock()
    result.envelope_id = f"ds-{uuid.uuid4().hex[:12]}"
    result.status = "sent"
    return result


def _make_docusign_envelope_detail():
    """Build a mock EnvelopesApi.get_envelope return value."""
    result = MagicMock()
    result.envelope_id = "ds-abc123"
    result.status = "completed"
    result.email_subject = "Please sign"
    result.created_date_time = "2025-01-01T00:00:00Z"
    result.sent_date_time = "2025-01-01T00:01:00Z"
    result.completed_date_time = "2025-01-02T00:00:00Z"

    signer1 = MagicMock()
    signer1.name = "Alice"
    signer1.email = "alice@example.com"
    signer1.status = "completed"
    signer1.signed_datetime = "2025-01-01T12:00:00Z"

    signer2 = MagicMock()
    signer2.name = "Bob"
    signer2.email = "bob@example.com"
    signer2.status = "completed"
    signer2.signed_datetime = "2025-01-01T13:00:00Z"

    result.recipients.signers = [signer1, signer2]
    return result


def _make_docusign_signing_view():
    """Build a mock create_recipient_view return value."""
    result = MagicMock()
    result.url = "https://demo.docusign.net/Signing/startinsession.aspx?t=abc123"
    return result


class TestDocuSignProvider:
    """Tests for DocuSignProvider with mocked docusign_esign client."""

    def _make_provider(self):
        """Build a provider with a pre-configured mock client."""
        provider = DocuSignProvider()
        mock_api_client = MagicMock()
        mock_api_client.user_id = "test-user-id"
        provider._client = mock_api_client
        provider._account_id = "test-account-id"
        return provider, mock_api_client

    def _mock_envelopes_api(self, mock_api_client):
        """Set up a mock EnvelopesApi on the given client and return it."""
        mock_envelopes_api = MagicMock()
        mock_api_client._envelopes_api = mock_envelopes_api
        return mock_envelopes_api

    @pytest.mark.asyncio
    async def test_create_envelope(self, signers, sample_pdf):
        provider, mock_client = self._make_provider()
        mock_ea = self._mock_envelopes_api(mock_client)
        mock_ea.create_envelope.return_value = _make_docusign_envelope_result()

        with patch(
            "docusign_esign.EnvelopesApi", return_value=mock_ea
        ), patch(
            "docusign_esign.Document", side_effect=lambda **kw: MagicMock(**kw)
        ), patch(
            "docusign_esign.EnvelopeDefinition", side_effect=lambda **kw: MagicMock(**kw)
        ), patch(
            "docusign_esign.Recipients", side_effect=lambda **kw: MagicMock(**kw)
        ), patch(
            "docusign_esign.Signer", side_effect=lambda **kw: MagicMock(**kw)
        ):
            result = await provider.create_envelope(
                document_bytes=sample_pdf,
                document_name="test.pdf",
                signers=signers,
                subject="Please sign",
                message="Sign this agreement",
            )

        assert isinstance(result, EnvelopeResult)
        assert result.envelope_id.startswith("ds-")
        assert result.status == "sent"
        assert result.signing_url is None
        mock_ea.create_envelope.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_envelope_with_agreement_id(self, signers, sample_pdf):
        provider, mock_client = self._make_provider()
        mock_ea = self._mock_envelopes_api(mock_client)
        mock_ea.create_envelope.return_value = _make_docusign_envelope_result()

        with patch(
            "docusign_esign.EnvelopesApi", return_value=mock_ea
        ), patch(
            "docusign_esign.Document", side_effect=lambda **kw: MagicMock(**kw)
        ), patch(
            "docusign_esign.EnvelopeDefinition", side_effect=lambda **kw: MagicMock(**kw)
        ), patch(
            "docusign_esign.Recipients", side_effect=lambda **kw: MagicMock(**kw)
        ), patch(
            "docusign_esign.Signer", side_effect=lambda **kw: MagicMock(**kw)
        ):
            result = await provider.create_envelope(
                document_bytes=sample_pdf,
                document_name="test.pdf",
                signers=signers,
                subject="Subject",
                message="Msg",
                agreement_id=uuid.uuid4(),
            )

        assert result.envelope_id.startswith("ds-")

    @pytest.mark.asyncio
    async def test_get_envelope_status(self):
        provider, mock_client = self._make_provider()
        mock_ea = self._mock_envelopes_api(mock_client)
        mock_ea.get_envelope.return_value = _make_docusign_envelope_detail()

        with patch("docusign_esign.EnvelopesApi", return_value=mock_ea):
            status = await provider.get_envelope_status("ds-abc123")

        assert status["envelope_id"] == "ds-abc123"
        assert status["status"] == "completed"
        assert status["subject"] == "Please sign"
        assert len(status["signers"]) == 2
        assert status["signers"][0]["email"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_get_envelope_status_api_error(self):
        provider, mock_client = self._make_provider()
        mock_ea = self._mock_envelopes_api(mock_client)
        mock_ea.get_envelope.side_effect = Exception("API error")

        with patch("docusign_esign.EnvelopesApi", return_value=mock_ea):
            status = await provider.get_envelope_status("ds-bad")

        assert status["status"] == "error"
        assert "API error" in status["error"]

    @pytest.mark.asyncio
    async def test_get_signing_url(self):
        provider, mock_client = self._make_provider()
        mock_ea = self._mock_envelopes_api(mock_client)
        mock_ea.get_envelope.return_value = _make_docusign_envelope_detail()
        mock_ea.create_recipient_view.return_value = (
            _make_docusign_signing_view()
        )

        with patch("docusign_esign.EnvelopesApi", return_value=mock_ea):
            url = await provider.get_signing_url("ds-abc123", "alice@example.com")

        assert url is not None
        assert "startinsession" in url
        mock_ea.create_recipient_view.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_signing_url_unknown_email(self):
        provider, mock_client = self._make_provider()
        mock_ea = self._mock_envelopes_api(mock_client)
        mock_ea.get_envelope.return_value = _make_docusign_envelope_detail()

        with patch("docusign_esign.EnvelopesApi", return_value=mock_ea):
            url = await provider.get_signing_url("ds-abc123", "nobody@example.com")

        assert url is None
        mock_ea.create_recipient_view.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_envelope(self):
        provider, mock_client = self._make_provider()
        mock_ea = self._mock_envelopes_api(mock_client)
        mock_ea.update_envelope.return_value = MagicMock()

        with patch("docusign_esign.EnvelopesApi", return_value=mock_ea):
            result = await provider.cancel_envelope("ds-abc123")

        assert result is True
        mock_ea.update_envelope.assert_called_once()

        # Verify the payload has status=voided
        call_kwargs = mock_ea.update_envelope.call_args
        envelope_kwarg = call_kwargs.kwargs.get("envelope") or call_kwargs[1].get(
            "envelope"
        )
        assert envelope_kwarg["status"] == "voided"

    @pytest.mark.asyncio
    async def test_cancel_envelope_api_error(self):
        provider, mock_client = self._make_provider()
        mock_ea = self._mock_envelopes_api(mock_client)
        mock_ea.update_envelope.side_effect = Exception("Cannot void")

        with patch("docusign_esign.EnvelopesApi", return_value=mock_ea):
            result = await provider.cancel_envelope("ds-abc123")

        assert result is False

    @pytest.mark.asyncio
    async def test_require_client_raises_when_not_configured(self):
        provider = DocuSignProvider()
        with pytest.raises(ValueError, match="not configured"):
            provider._require_client()

    @pytest.mark.asyncio
    async def test_get_client_missing_credentials(self):
        """When env vars are missing, _get_client sets _client=False."""
        provider = DocuSignProvider()
        with patch("app.services.esignature.get_settings_lazy") as mock_settings:
            mock_settings.return_value.docusign_integration_key = None
            mock_settings.return_value.docusign_user_id = None
            mock_settings.return_value.docusign_account_id = None
            mock_settings.return_value.docusign_private_key_path = "nonexistent.pem"
            mock_settings.return_value.docusign_oauth_base_url = "account-d.docusign.com"
            mock_settings.return_value.docusign_base_path = "https://demo.docusign.net/restapi"
            provider._get_client()
        assert provider._client is False

    @pytest.mark.asyncio
    async def test_get_client_import_error(self):
        """When docusign_esign is not installed, _get_client sets _client=False."""
        provider = DocuSignProvider()
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "docusign_esign":
                raise ImportError("No module named 'docusign_esign'")
            return real_import(name, *args, **kwargs)

        with patch("app.services.esignature.get_settings_lazy") as mock_settings:
            mock_settings.return_value.docusign_integration_key = MagicMock(
                get_secret_value=lambda: "fake-key"
            )
            mock_settings.return_value.docusign_user_id = MagicMock(
                get_secret_value=lambda: "fake-user"
            )
            mock_settings.return_value.docusign_account_id = MagicMock(
                get_secret_value=lambda: "fake-account"
            )
            mock_settings.return_value.docusign_private_key_path = "nonexistent.pem"
            mock_settings.return_value.docusign_oauth_base_url = "account-d.docusign.com"
            mock_settings.return_value.docusign_base_path = "https://demo.docusign.net/restapi"

            with patch("builtins.__import__", side_effect=mock_import):
                provider._get_client()

        assert provider._client is False


# ===========================================================================
# AdobeSignProvider
# ===========================================================================


class TestAdobeSignProvider:
    """Tests for AdobeSignProvider with mocked httpx calls."""

    def _make_provider(self):
        provider = AdobeSignProvider()
        provider._access_token = "fake-token"
        provider._base_url = "https://api.echosign.com/api/rest/v6"
        return provider

    @pytest.mark.asyncio
    async def test_create_envelope(self, signers, sample_pdf):
        provider = self._make_provider()

        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_upload_resp.json.return_value = {
            "transientDocumentId": "transient-123"
        }
        mock_upload_resp.raise_for_status = MagicMock()

        mock_create_resp = MagicMock()
        mock_create_resp.status_code = 201
        mock_create_resp.json.return_value = {"id": "adobe-agreement-456"}
        mock_create_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=[mock_upload_resp, mock_create_resp]
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.create_envelope(
                document_bytes=sample_pdf,
                document_name="test.pdf",
                signers=signers,
                subject="Please sign",
                message="Sign this",
            )

        assert isinstance(result, EnvelopeResult)
        assert result.envelope_id == "adobe-agreement-456"
        assert result.status == "sent"

        # Verify two POST calls: transient doc upload + agreement creation
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_get_envelope_status(self):
        provider = self._make_provider()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "adobe-123",
            "status": "COMPLETED",
            "name": "Test Agreement",
            "senderEmail": "sender@example.com",
            "createdDate": "2025-01-01T00:00:00Z",
            "participantSetsInfo": [
                {
                    "memberInfos": [
                        {
                            "name": "Alice",
                            "email": "alice@example.com",
                            "status": "SIGNED",
                        }
                    ],
                    "role": "SIGNER",
                    "order": 1,
                }
            ],
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            status = await provider.get_envelope_status("adobe-123")

        assert status["envelope_id"] == "adobe-123"
        assert status["status"] == "completed"
        assert status["raw_status"] == "COMPLETED"
        assert status["name"] == "Test Agreement"
        assert len(status["signers"]) == 1
        assert status["signers"][0]["email"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_get_envelope_status_not_found(self):
        provider = self._make_provider()

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            status = await provider.get_envelope_status("nonexistent")

        assert status["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_get_signing_url(self):
        provider = self._make_provider()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "signingUrlSetInfos": [
                {
                    "signingUrls": [
                        {
                            "email": "alice@example.com",
                            "esignUrl": "https://secure.echosign.com/public/apiesign?pid=abc",
                        },
                        {
                            "email": "bob@example.com",
                            "esignUrl": "https://secure.echosign.com/public/apiesign?pid=def",
                        },
                    ]
                }
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            url = await provider.get_signing_url("adobe-123", "bob@example.com")

        assert url is not None
        assert "pid=def" in url

    @pytest.mark.asyncio
    async def test_get_signing_url_unknown_email(self):
        provider = self._make_provider()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "signingUrlSetInfos": [
                {
                    "signingUrls": [
                        {
                            "email": "alice@example.com",
                            "esignUrl": "https://secure.echosign.com/public/apiesign?pid=abc",
                        }
                    ]
                }
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            url = await provider.get_signing_url("adobe-123", "nobody@example.com")

        assert url is None

    @pytest.mark.asyncio
    async def test_cancel_envelope(self):
        provider = self._make_provider()

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.put = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.cancel_envelope("adobe-123")

        assert result is True
        mock_client.put.assert_called_once()
        # Verify the payload has value=CANCELLED
        put_kwargs = mock_client.put.call_args
        assert put_kwargs.kwargs.get("json", put_kwargs[1].get("json"))["value"] == "CANCELLED"

    @pytest.mark.asyncio
    async def test_cancel_envelope_failure(self):
        provider = self._make_provider()

        mock_resp = MagicMock()
        mock_resp.status_code = 409
        mock_resp.text = "Cannot cancel"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.put = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.cancel_envelope("adobe-123")

        assert result is False

    @pytest.mark.asyncio
    async def test_configure_raises_without_token(self):
        provider = AdobeSignProvider()
        with patch("app.services.esignature.get_settings_lazy") as mock_settings:
            mock_settings.return_value.adobesign_access_token = None
            with pytest.raises(ValueError, match="not configured"):
                provider._configure()


# ===========================================================================
# get_esignature_provider (by-name factory, spec 24.5)
# ===========================================================================


class TestGetProviderByName:
    def test_docusign_by_name(self):
        provider = get_esignature_provider("docusign")
        assert isinstance(provider, DocuSignProvider)

    def test_adobe_sign_by_name(self):
        """Regression: 'adobe_sign' previously fell back to mock."""
        provider = get_esignature_provider("adobe_sign")
        assert isinstance(provider, AdobeSignProvider)

    @pytest.mark.parametrize("alias", ["adobe", "adobesign", "ADOBE_SIGN"])
    def test_adobe_sign_aliases(self, alias):
        provider = get_esignature_provider(alias)
        assert isinstance(provider, AdobeSignProvider)

    def test_mock_by_name(self):
        provider = get_esignature_provider("mock")
        assert isinstance(provider, MockESignatureProvider)

    def test_default_is_mock(self):
        provider = get_esignature_provider()
        assert isinstance(provider, MockESignatureProvider)

    def test_unknown_name_degrades_to_mock(self):
        provider = get_esignature_provider("nonsense")
        assert isinstance(provider, MockESignatureProvider)


# ===========================================================================
# resolve_esignature_provider
# ===========================================================================


class TestResolveProvider:
    def test_resolve_mock_default(self):
        with patch("app.services.esignature.get_settings_lazy") as mock_settings:
            mock_settings.return_value.esignature_provider = "mock"
            provider = resolve_esignature_provider()
        assert isinstance(provider, MockESignatureProvider)

    def test_resolve_docusign_with_creds(self):
        with patch("app.services.esignature.get_settings_lazy") as mock_settings:
            mock_settings.return_value.esignature_provider = "docusign"
            mock_settings.return_value.docusign_integration_key = MagicMock(
                get_secret_value=lambda: "key"
            )
            mock_settings.return_value.docusign_account_id = MagicMock(
                get_secret_value=lambda: "acct"
            )
            provider = resolve_esignature_provider()
        assert isinstance(provider, DocuSignProvider)

    def test_resolve_docusign_without_creds_falls_back(self):
        with patch("app.services.esignature.get_settings_lazy") as mock_settings:
            mock_settings.return_value.esignature_provider = "docusign"
            mock_settings.return_value.docusign_integration_key = None
            mock_settings.return_value.docusign_account_id = None
            provider = resolve_esignature_provider()
        assert isinstance(provider, MockESignatureProvider)

    def test_resolve_adobe_sign_with_token(self):
        with patch("app.services.esignature.get_settings_lazy") as mock_settings:
            mock_settings.return_value.esignature_provider = "adobe_sign"
            mock_settings.return_value.adobesign_access_token = MagicMock(
                get_secret_value=lambda: "token"
            )
            provider = resolve_esignature_provider()
        assert isinstance(provider, AdobeSignProvider)

    def test_resolve_adobe_sign_without_token_falls_back(self):
        with patch("app.services.esignature.get_settings_lazy") as mock_settings:
            mock_settings.return_value.esignature_provider = "adobe_sign"
            mock_settings.return_value.adobesign_access_token = None
            provider = resolve_esignature_provider()
        assert isinstance(provider, MockESignatureProvider)

    def test_resolve_unknown_falls_back(self):
        with patch("app.services.esignature.get_settings_lazy") as mock_settings:
            mock_settings.return_value.esignature_provider = "unknown_provider"
            provider = resolve_esignature_provider()
        assert isinstance(provider, MockESignatureProvider)

    def test_resolve_uses_env_var_over_settings(self):
        with patch.dict("os.environ", {"ESIGNATURE_PROVIDER": "mock"}):
            with patch("app.services.esignature.get_settings_lazy") as mock_settings:
                mock_settings.return_value.esignature_provider = "docusign"
                mock_settings.return_value.docusign_integration_key = MagicMock(
                    get_secret_value=lambda: "key"
                )
                mock_settings.return_value.docusign_account_id = MagicMock(
                    get_secret_value=lambda: "acct"
                )
                provider = resolve_esignature_provider()
        # ENV var says "mock" so should be mock, not docusign
        assert isinstance(provider, MockESignatureProvider)
