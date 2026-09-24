"""Credential resolution for monitoring integrations (spec 3.15.6).

Integration credentials are *references*, never inline values:

    IntegrationConnection → IntegrationCredential.secret_reference
        → secret manager / encrypted environment binding

This module resolves a ``secret_reference`` to an actual value at the moment
it is needed (connector auth/test) and always **fails closed**: if the
reference format is unknown, the manager is unconfigured, or the value is
missing, ``CredentialUnavailable`` is raised so monitoring produces
INCONCLUSIVE rather than inventing success (3.15.62).

Supported reference schemes:
  - ``env://VAR_NAME``           12-factor environment binding
  - ``secretman://provider/...`` pluggable secret-manager binding — the
    production secret-store adapters live behind the ``SecretManagerProvider``
    interface; without one configured these references fail closed.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.monitoring.enums import CredentialStatus
from app.monitoring.exceptions import CredentialUnavailable

_log = logging.getLogger(__name__)

_SELF_REFERENCE = "env://MONITORING_CREDENTIAL"

_ENV_RE = re.compile(r"^env://([A-Z0-9_]+)$", re.IGNORECASE)
_SECRETMAN_RE = re.compile(r"^secretman://([A-Za-z0-9_.-]+)/(.+)$")


class SecretManagerProvider:
    """Interface for production secret-store adapters.

    T1 ships with the environment binding only. Adapt for AWS Secrets
    Manager / HashiCorp Vault / GCP Secret Manager / Azure Key Vault by
    implementing ``resolve``; until one is configured, ``secretman://``
    references fail closed.
    """

    async def resolve(self, reference: str) -> str:
        raise CredentialUnavailable(
            f"No secret-manager provider configured for reference '{reference}'"
        )


@dataclass(frozen=True)
class ResolvedCredential:
    value: str
    reference: str
    scheme: str
    expires_at: datetime | None = None


async def resolve_credential_value(
    db: AsyncSession,
    *,
    integration_id: uuid.UUID,
    secret_reference: str,
    manager: SecretManagerProvider | None = None,
) -> ResolvedCredential:
    """Resolve one credential reference to a usable value (fail closed)."""
    ref = (secret_reference or "").strip()
    if not ref:
        raise CredentialUnavailable("Credential reference is empty")

    matched = _ENV_RE.match(ref)
    if matched:
        value = os.environ.get(matched.group(1))
        if value is None:
            _log.error(
                "credential env binding %s unset; failing closed for integration %s",
                ref,
                integration_id,
            )
            raise CredentialUnavailable(f"Secret environment variable '{matched.group(1)}' is not set")
        return ResolvedCredential(value=value, reference=ref, scheme="env")

    matched = _SECRETMAN_RE.match(ref)
    if matched:
        provider = manager or SecretManagerProvider()
        value = await provider.resolve(ref)
        if not value:
            raise CredentialUnavailable(f"Secret manager returned no value for '{ref}'")
        return ResolvedCredential(value=value, reference=ref, scheme=matched.group(1))

    raise CredentialUnavailable(f"Unsupported secret reference scheme: '{ref}'")


async def get_active_credentials(
    db: AsyncSession,
    integration_id: uuid.UUID,
    *,
    manager: SecretManagerProvider | None = None,
) -> list[ResolvedCredential]:
    """Resolve the integration's ACTIVE credentials.

    Raises ``CredentialUnavailable`` when no active credential exists or any
    required value is missing — callers convert that to INCONCLUSIVE.
    """
    from app.monitoring.models import IntegrationCredential

    result = await db.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.integration_id == integration_id,
            IntegrationCredential.status == CredentialStatus.ACTIVE.value,
        )
    )
    records = list(result.scalars().all())
    if not records:
        raise CredentialUnavailable(
            f"No active credential for integration {integration_id}"
        )

    resolved: list[ResolvedCredential] = []
    for record in records:
        resolved.append(
            await resolve_credential_value(
                db,
                integration_id=integration_id,
                secret_reference=record.secret_reference,
                manager=manager,
            )
        )
    return resolved


def strip_credential_metadata(payload: dict) -> dict:
    """Never leak credential material into persisted observation payloads."""
    blocked = {key for key in payload if any(
        token in key.lower() for token in ("secret", "token", "api_key", "apikey", "password", "authorization")
    )}
    return {k: v for k, v in payload.items() if k not in blocked}


def credential_header_token(resolved: list[ResolvedCredential]) -> str:
    """Best-effort bearer for generic REST connectors (first env credential)."""
    for cred in resolved:
        return cred.value
    raise CredentialUnavailable("No bearer credential available")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)