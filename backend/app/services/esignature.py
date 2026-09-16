"""
E-Signature Provider Integration Interface.

Abstract interface for e-signature providers (DocuSign, Adobe Sign, etc.)
with a mock implementation for development.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from app.core.config import get_settings_lazy

logger = logging.getLogger(__name__)


@dataclass
class EnvelopeResult:
    """Result of creating or sending an envelope."""

    envelope_id: str
    status: str
    status_message: str
    signing_url: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class SignerInfo:
    """Information about a signer."""

    name: str
    email: str
    role: str  # "signer", "approver", "cc"
    order: int = 1


class ESignatureProvider(ABC):
    """Abstract base class for e-signature providers."""

    @abstractmethod
    async def create_envelope(
        self,
        document_bytes: bytes,
        document_name: str,
        signers: list[SignerInfo],
        subject: str,
        message: str,
        agreement_id: Optional[UUID] = None,
    ) -> EnvelopeResult:
        """Create and send an envelope for signing."""
        pass

    @abstractmethod
    async def get_envelope_status(
        self,
        envelope_id: str,
    ) -> dict:
        """Get the status of an envelope."""
        pass

    @abstractmethod
    async def get_signing_url(
        self,
        envelope_id: str,
        signer_email: str,
    ) -> Optional[str]:
        """Get the signing URL for a specific signer."""
        pass

    @abstractmethod
    async def cancel_envelope(
        self,
        envelope_id: str,
    ) -> bool:
        """Cancel an envelope."""
        pass


class MockESignatureProvider(ESignatureProvider):
    """
    Mock e-signature provider for development.

    Simulates the e-signature flow without actually sending documents.
    """

    def __init__(self):
        self._envelopes: dict[str, dict] = {}

    async def create_envelope(
        self,
        document_bytes: bytes,
        document_name: str,
        signers: list[SignerInfo],
        subject: str,
        message: str,
        agreement_id: Optional[UUID] = None,
    ) -> EnvelopeResult:
        """Create a mock envelope."""
        envelope_id = f"mock-{uuid4().hex[:16]}"

        self._envelopes[envelope_id] = {
            "id": envelope_id,
            "document_name": document_name,
            "signers": [
                {"name": s.name, "email": s.email, "role": s.role, "order": s.order}
                for s in signers
            ],
            "status": "sent",
            "subject": subject,
            "message": message,
            "agreement_id": str(agreement_id) if agreement_id else None,
            "created_at": datetime.utcnow(),
            "signatures": [],
        }

        logger.info(f"[MOCK] Created envelope {envelope_id} for {document_name}")

        return EnvelopeResult(
            envelope_id=envelope_id,
            status="sent",
            status_message="Envelope sent to signers",
            signing_url=f"https://mock-esign.example.com/sign/{envelope_id}",
            created_at=datetime.utcnow(),
        )

    async def get_envelope_status(
        self,
        envelope_id: str,
    ) -> dict:
        """Get mock envelope status."""
        envelope = self._envelopes.get(envelope_id)
        if not envelope:
            return {"status": "not_found", "error": "Envelope not found"}

        return {
            "envelope_id": envelope_id,
            "status": envelope["status"],
            "document_name": envelope["document_name"],
            "signers": envelope["signers"],
            "signatures": envelope["signatures"],
            "created_at": envelope["created_at"].isoformat(),
        }

    async def get_signing_url(
        self,
        envelope_id: str,
        signer_email: str,
    ) -> Optional[str]:
        """Get mock signing URL."""
        envelope = self._envelopes.get(envelope_id)
        if not envelope:
            return None

        return f"https://mock-esign.example.com/sign/{envelope_id}?email={signer_email}"

    async def cancel_envelope(
        self,
        envelope_id: str,
    ) -> bool:
        """Cancel a mock envelope."""
        envelope = self._envelopes.get(envelope_id)
        if not envelope:
            return False

        envelope["status"] = "cancelled"
        logger.info(f"[MOCK] Cancelled envelope {envelope_id}")
        return True

    async def simulate_signing(
        self,
        envelope_id: str,
        signer_email: str,
    ) -> dict:
        """Simulate a signer signing the document (for testing)."""
        envelope = self._envelopes.get(envelope_id)
        if not envelope:
            return {"error": "Envelope not found"}

        envelope["signatures"].append({
            "signer_email": signer_email,
            "signed_at": datetime.utcnow().isoformat(),
            "ip_address": "127.0.0.1",
            "status": "signed",
        })

        # Check if all signers have signed
        all_emails = {s["email"] for s in envelope["signers"]}
        signed_emails = {sig["signer_email"] for sig in envelope["signatures"]}

        if all_emails.issubset(signed_emails):
            envelope["status"] = "completed"
            logger.info(f"[MOCK] Envelope {envelope_id} fully signed")
        else:
            envelope["status"] = "partially_signed"

        return {
            "envelope_id": envelope_id,
            "status": envelope["status"],
            "signed_count": len(envelope["signatures"]),
            "total_signers": len(envelope["signers"]),
        }


class DocuSignProvider(ESignatureProvider):
    """
    DocuSign e-signature provider.

    Uses JWT Grant authentication to interact with the DocuSign eSignature
    REST API (v2.1).  Requires the following environment variables:

      DOCUSIGN_INTEGRATION_KEY   – OAuth client / integration key
      DOCUSIGN_USER_ID           – DocuSign user ID to impersonate
      DOCUSIGN_ACCOUNT_ID        – DocuSign account ID
      DOCUSIGN_PRIVATE_KEY_PATH  – path to RSA private key PEM file
      DOCUSIGN_OAUTH_BASE_URL    – OAuth server (default: account-d.docusign.com)
      DOCUSIGN_BASE_PATH         – REST API base (default: demo endpoint)
    """

    def __init__(self):
        self._client = None
        self._account_id: str | None = None

    # ------------------------------------------------------------------
    # Client initialisation
    # ------------------------------------------------------------------

    def _get_client(self):
        """Lazy-load and authenticate the DocuSign API client."""
        if self._client is not None:
            return

        try:
            from docusign_esign import ApiClient
        except ImportError:
            logger.error(
                "docusign_esign package is not installed; "
                "run: pip install docusign_esign"
            )
            self._client = False
            return

        settings = get_settings_lazy()

        integration_key = (
            settings.docusign_integration_key.get_secret_value()
            if settings.docusign_integration_key
            else None
        )
        user_id = (
            settings.docusign_user_id.get_secret_value()
            if settings.docusign_user_id
            else None
        )
        account_id = (
            settings.docusign_account_id.get_secret_value()
            if settings.docusign_account_id
            else None
        )
        private_key_path = settings.docusign_private_key_path
        oauth_base_url = settings.docusign_oauth_base_url
        base_path = settings.docusign_base_path

        if not all([integration_key, user_id, account_id]):
            logger.error(
                "DocuSign credentials incomplete – set DOCUSIGN_INTEGRATION_KEY, "
                "DOCUSIGN_USER_ID, and DOCUSIGN_ACCOUNT_ID"
            )
            self._client = False
            return

        try:
            api_client = ApiClient()
            api_client.set_base_path(base_path)
            api_client.configure_jwt_authorization_flow(
                private_key_file_path=private_key_path,
                oauth_host_name=oauth_base_url,
                client_id=integration_key,
                user_id=user_id,
                expires_in=3600,
            )
            self._client = api_client
            self._account_id = account_id
            logger.info("DocuSign client initialised (JWT Grant)")
        except FileNotFoundError:
            logger.error(
                f"DocuSign private key not found at {private_key_path}"
            )
            self._client = False
        except Exception as exc:
            logger.error(f"Failed to initialise DocuSign client: {exc}")
            self._client = False

    def _require_client(self):
        """Initialise the client and raise if unavailable."""
        self._get_client()
        if not self._client:
            raise ValueError(
                "DocuSign is not configured. Set DOCUSIGN_* environment "
                "variables and ensure the private key file exists."
            )

    # ------------------------------------------------------------------
    # Envelope operations
    # ------------------------------------------------------------------

    async def create_envelope(
        self,
        document_bytes: bytes,
        document_name: str,
        signers: list[SignerInfo],
        subject: str,
        message: str,
        agreement_id: Optional[UUID] = None,
    ) -> EnvelopeResult:
        """Create and send a DocuSign envelope."""
        import base64

        from docusign_esign import (
            Document,
            EnvelopeDefinition,
            Recipients,
            Signer,
        )
        from docusign_esign import EnvelopesApi

        self._require_client()

        # Encode the document as base64
        doc_b64 = base64.b64encode(document_bytes).decode("ascii")

        # Build signer list – each signer gets a sequential routing order
        ds_signers = []
        for idx, s in enumerate(signers, start=1):
            ds_signers.append(
                Signer(
                    email=s.email,
                    name=s.name,
                    recipient_id=str(idx),
                    routing_order=str(idx),
                    tabs=None,  # Use default tab placement
                )
            )

        envelope_definition = EnvelopeDefinition(
            email_subject=subject,
            email_blurb=message,
            documents=[
                Document(
                    document_base64=doc_b64,
                    name=document_name,
                    file_extension="pdf",
                    document_id="1",
                )
            ],
            recipients=Recipients(signers=ds_signers),
            status="sent",  # Send immediately
        )

        if agreement_id:
            envelope_definition.custom_fields = None  # Could set metadata here

        envelopes_api = EnvelopesApi(self._client)

        try:
            result = envelopes_api.create_envelope(
                account_id=self._account_id,
                envelope_definition=envelope_definition,
            )
        except Exception as exc:
            logger.error(f"DocuSign create_envelope failed: {exc}")
            raise ValueError(f"DocuSign envelope creation failed: {exc}") from exc

        logger.info(
            f"DocuSign envelope created: {result.envelope_id} "
            f"(status={result.status})"
        )

        return EnvelopeResult(
            envelope_id=result.envelope_id,
            status=result.status or "sent",
            status_message="Envelope sent to signers via DocuSign",
            signing_url=None,  # Use get_signing_url() for embedded signing
            created_at=datetime.utcnow(),
        )

    async def get_envelope_status(self, envelope_id: str) -> dict:
        """Retrieve the status of a DocuSign envelope."""
        from docusign_esign import EnvelopesApi

        self._require_client()

        envelopes_api = EnvelopesApi(self._client)

        try:
            result = envelopes_api.get_envelope(
                account_id=self._account_id,
                envelope_id=envelope_id,
            )
        except Exception as exc:
            logger.error(
                f"DocuSign get_envelope_status failed for {envelope_id}: {exc}"
            )
            return {
                "status": "error",
                "error": str(exc),
                "envelope_id": envelope_id,
            }

        # Build signer summary from recipients
        signers_summary = []
        if result.recipients and result.recipients.signers:
            for s in result.recipients.signers:
                signers_summary.append({
                    "name": s.name,
                    "email": s.email,
                    "status": s.status,
                    "signed_at": s.signed_datetime,
                })

        return {
            "envelope_id": result.envelope_id,
            "status": result.status,
            "subject": result.email_subject,
            "created_at": result.created_date_time,
            "sent_at": result.sent_date_time,
            "completed_at": result.completed_date_time,
            "signers": signers_summary,
        }

    async def get_signing_url(
        self,
        envelope_id: str,
        signer_email: str,
    ) -> Optional[str]:
        """Get an embedded signing URL for a specific signer."""
        from docusign_esign import EnvelopesApi

        self._require_client()

        envelopes_api = EnvelopesApi(self._client)

        try:
            # First, find the recipient ID for the given email
            envelope = envelopes_api.get_envelope(
                account_id=self._account_id,
                envelope_id=envelope_id,
            )

            recipient_id = None
            if envelope.recipients and envelope.recipients.signers:
                for s in envelope.recipients.signers:
                    if s.email == signer_email:
                        recipient_id = s.recipient_id
                        break

            if recipient_id is None:
                logger.warning(
                    f"Signer {signer_email} not found in envelope {envelope_id}"
                )
                return None

            # Create the embedded signing view
            from docusign_esign import EnvelopeDefinition, RecipientViewRequest

            return_url = (
                f"{get_settings_lazy().app_base_url}"
                f"/esignature/callback"
            )

            view_request = RecipientViewRequest(
                authentication_method="email",
                client_user_id="contractos-app",
                recipient_id=recipient_id,
                return_url=return_url,
                user_id=self._client.user_id
                if hasattr(self._client, "user_id")
                else None,
            )

            results = envelopes_api.create_recipient_view(
                account_id=self._account_id,
                envelope_id=envelope_id,
                recipient_view_request=view_request,
            )

            return results.url

        except Exception as exc:
            logger.error(
                f"DocuSign get_signing_url failed for {envelope_id}: {exc}"
            )
            return None

    async def cancel_envelope(self, envelope_id: str) -> bool:
        """Void (cancel) a DocuSign envelope that hasn't been completed."""
        from docusign_esign import EnvelopesApi

        self._require_client()

        envelopes_api = EnvelopesApi(self._client)

        try:
            envelopes_api.update_envelope(
                account_id=self._account_id,
                envelope_id=envelope_id,
                envelope={"status": "voided", "voided_reason": "Cancelled by user"},
            )
            logger.info(f"DocuSign envelope {envelope_id} voided")
            return True
        except Exception as exc:
            logger.error(
                f"DocuSign cancel_envelope failed for {envelope_id}: {exc}"
            )
            return False


class AdobeSignProvider(ESignatureProvider):
    """
    Adobe Acrobat Sign e-signature provider.

    Uses the Adobe Acrobat Sign REST API v6 over HTTP.  There is no
    official Python SDK, so this provider calls the API directly via
    ``httpx`` (already a project dependency).

    Required environment variables:

      ADOBESIGN_ACCESS_TOKEN  – OAuth 2.0 Bearer token
      ADOBESIGN_BASE_URL      – API base (default: https://api.echosign.com/api/rest/v6)
    """

    # Adobe Sign status → our canonical status mapping
    _STATUS_MAP = {
        "OUT_FOR_SIGNATURE": "sent",
        "WAITING_FOR_MY_SIGNATURE": "sent",
        "WAITING_FOR_Others": "sent",
        "AUTHORING": "draft",
        "DRAFT": "draft",
        "IN_PROCESS": "sent",
        "SIGNED": "completed",
        "APPROVED": "completed",
        "DELIVERED": "completed",
        "COMPLETED": "completed",
        "CANCELLED": "cancelled",
        "EXPIRED": "expired",
        "RECALLED": "cancelled",
        "CLICK_ACCEPT": "completed",
        "CLICK_SIGN": "completed",
    }

    def __init__(self):
        self._base_url: str | None = None
        self._access_token: str | None = None

    def _configure(self) -> None:
        """Lazy-load credentials from settings."""
        if self._access_token is not None:
            return
        settings = get_settings_lazy()
        token = (
            settings.adobesign_access_token.get_secret_value()
            if settings.adobesign_access_token
            else None
        )
        if not token:
            raise ValueError(
                "Adobe Sign is not configured. "
                "Set the ADOBESIGN_ACCESS_TOKEN environment variable."
            )
        self._access_token = token
        self._base_url = settings.adobesign_base_url.rstrip("/")

    def _headers(self) -> dict:
        """Return the common Authorization headers."""
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Envelope operations
    # ------------------------------------------------------------------

    async def create_envelope(
        self,
        document_bytes: bytes,
        document_name: str,
        signers: list[SignerInfo],
        subject: str,
        message: str,
        agreement_id: Optional[UUID] = None,
    ) -> EnvelopeResult:
        """Upload a transient document and create an agreement."""
        import httpx

        self._configure()

        # Step 1: Upload the document as a transient document
        upload_url = f"{self._base_url}/transientDocuments"
        async with httpx.AsyncClient() as client:
            upload_resp = await client.post(
                upload_url,
                headers={"Authorization": f"Bearer {self._access_token}"},
                files={
                    "File": (document_name, document_bytes, "application/pdf")
                },
                timeout=60,
            )
            upload_resp.raise_for_status()
            transient_doc_id = upload_resp.json()["transientDocumentId"]

        # Step 2: Build participant sets (each signer is a separate set
        # with sequential ordering)
        participant_sets = []
        for idx, s in enumerate(signers, start=1):
            role_map = {
                "signer": "SIGNER",
                "approver": "APPROVER",
                "cc": "CC",
            }
            participant_sets.append(
                {
                    "memberInfos": [{"email": s.email, "name": s.name}],
                    "order": idx,
                    "role": role_map.get(s.role, "SIGNER"),
                }
            )

        # Step 3: Create the agreement
        agreement_payload = {
            "fileInfos": [{"transientDocumentId": transient_doc_id}],
            "name": document_name,
            "participantSetsInfo": participant_sets,
            "signatureType": "ESIGN",
            "state": "IN_PROCESS",
            "message": message,
        }

        create_url = f"{self._base_url}/agreements"
        async with httpx.AsyncClient() as client:
            create_resp = await client.post(
                create_url,
                headers=self._headers(),
                json=agreement_payload,
                timeout=30,
            )
            create_resp.raise_for_status()
            envelope_id = create_resp.json()["id"]

        logger.info(
            f"Adobe Sign agreement created: {envelope_id} "
            f"(document={document_name})"
        )

        return EnvelopeResult(
            envelope_id=envelope_id,
            status="sent",
            status_message="Agreement sent to signers via Adobe Sign",
            signing_url=None,
            created_at=datetime.utcnow(),
        )

    async def get_envelope_status(self, envelope_id: str) -> dict:
        """Get the status of an Adobe Sign agreement."""
        import httpx

        self._configure()

        url = f"{self._base_url}/agreements/{envelope_id}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url, headers=self._headers(), timeout=30
            )
            if resp.status_code == 404:
                return {
                    "status": "not_found",
                    "error": "Agreement not found",
                    "envelope_id": envelope_id,
                }
            resp.raise_for_status()
            data = resp.json()

        raw_status = data.get("status", "UNKNOWN")
        canonical = self._STATUS_MAP.get(raw_status, raw_status.lower())

        # Build signer summary
        signers_summary = []
        for pset in data.get("participantSetsInfo", []):
            for member in pset.get("memberInfos", []):
                signers_summary.append({
                    "name": member.get("name"),
                    "email": member.get("email"),
                    "status": member.get("status"),
                    "role": pset.get("role"),
                    "order": pset.get("order"),
                })

        return {
            "envelope_id": data.get("id", envelope_id),
            "status": canonical,
            "raw_status": raw_status,
            "name": data.get("name"),
            "sender_email": data.get("senderEmail"),
            "created_at": data.get("createdDate"),
            "signers": signers_summary,
        }

    async def get_signing_url(
        self,
        envelope_id: str,
        signer_email: str,
    ) -> Optional[str]:
        """Get the signing URL for a specific participant."""
        import httpx

        self._configure()

        url = f"{self._base_url}/agreements/{envelope_id}/signingUrls"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url, headers=self._headers(), timeout=30
            )
            if resp.status_code != 200:
                logger.warning(
                    f"Adobe Sign get_signing_url failed for {envelope_id}: "
                    f"HTTP {resp.status_code}"
                )
                return None
            data = resp.json()

        # signingUrlSetInfos is a list of sets; each set has signingUrls
        for url_set in data.get("signingUrlSetInfos", []):
            for signing_url_info in url_set.get("signingUrls", []):
                if (
                    signing_url_info.get("email", "").lower()
                    == signer_email.lower()
                ):
                    return signing_url_info.get("esignUrl")

        logger.warning(
            f"Signer {signer_email} not found in signing URLs for {envelope_id}"
        )
        return None

    async def cancel_envelope(self, envelope_id: str) -> bool:
        """Cancel (recall) an Adobe Sign agreement."""
        import httpx

        self._configure()

        url = f"{self._base_url}/agreements/{envelope_id}/state"
        payload = {
            "value": "CANCELLED",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                url, headers=self._headers(), json=payload, timeout=30
            )
            if resp.status_code in (200, 204):
                logger.info(
                    f"Adobe Sign agreement {envelope_id} cancelled"
                )
                return True
            logger.error(
                f"Adobe Sign cancel failed for {envelope_id}: "
                f"HTTP {resp.status_code} {resp.text[:200]}"
            )
            return False


# Provider factory
def get_esignature_provider(provider: str = "mock") -> ESignatureProvider:
    """
    Get e-signature provider instance.

    Args:
        provider: "mock", "docusign", or "adobe_sign"

    Returns:
        ESignatureProvider instance
    """
    if provider == "docusign":
        return DocuSignProvider()
    else:
        return MockESignatureProvider()


def resolve_esignature_provider() -> ESignatureProvider:
    """Resolve the configured e-signature provider (spec 24.5).

    Reads the ESIGNATURE_PROVIDER environment variable (default "mock"):
      - "mock"      -> MockESignatureProvider (development)
      - "docusign"  -> DocuSignProvider (requires provider credentials)
      - "adobe_sign"-> falls back to mock until Adobe provider is wired

    Unknown or unconfigured provider names degrade gracefully to mock with
    a warning rather than failing envelope creation.
    """
    import os

    # Prefer the env var; fall back to the pydantic settings value so
    # .env files configure the provider the same way.
    provider = os.environ.get("ESIGNATURE_PROVIDER") or getattr(
        get_settings_lazy(), "esignature_provider", "mock"
    )
    provider = str(provider).strip().lower()
    if provider == "docusign":
        settings = get_settings_lazy()
        if settings.docusign_integration_key and settings.docusign_account_id:
            return DocuSignProvider()
        logger.warning(
            "ESIGNATURE_PROVIDER=docusign but DOCUSIGN_* env vars missing; "
            "falling back to mock provider"
        )
        return MockESignatureProvider()
    if provider == "adobe_sign":
        settings = get_settings_lazy()
        if settings.adobesign_access_token:
            return AdobeSignProvider()
        logger.warning(
            "ESIGNATURE_PROVIDER=adobe_sign but ADOBESIGN_ACCESS_TOKEN "
            "missing; falling back to mock provider"
        )
        return MockESignatureProvider()
    return MockESignatureProvider()
