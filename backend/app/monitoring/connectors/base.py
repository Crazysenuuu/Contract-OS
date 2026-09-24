"""Connector contracts (spec 3.15.9, 3.15.42).

The connector interface is deliberately obligation-agnostic: a connector
fetches ``ExternalObservation`` values for a *query*, and monitoring rule
semantics live in the evaluators. No provider-specific branch appears in
obligation or service code (3.15.2).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.monitoring.enums import ConnectorCapability


@dataclass(frozen=True)
class ExternalObservation:
    """One real observation from an external source."""

    external_id: str
    observed_at: datetime
    resource_type: str
    payload: dict[str, Any]
    source_reference: dict[str, Any] = field(default_factory=dict)


class Connector(ABC):
    """Read-only external source connector.

    Connectors must fail closed: unreachable/auth-failed sources raise a
    connector error so the service records INCONCLUSIVE — never a fabricated
    PASS or FAIL (3.15.25, 3.15.62).
    """

    def __init__(self, *, credentials: list, configuration: dict) -> None:
        self.credentials = credentials
        self.configuration = configuration or {}

    @abstractmethod
    async def validate_connection(self) -> bool:
        """True when the configured credentials/source are usable."""

    @abstractmethod
    async def fetch(self, query: dict) -> list[ExternalObservation]:
        """Execute a query and return normalized observations."""

    async def close(self) -> None:
        """Release transport resources (no-op by default)."""


@dataclass(frozen=True)
class ConnectorMetadata:
    """Static provider metadata (spec 3.15.42)."""

    provider_key: str
    label: str
    integration_types: tuple[str, ...] = ()
    capabilities: frozenset[ConnectorCapability] = frozenset(
        {ConnectorCapability.READ}
    )
    configuration_schema: dict = field(default_factory=dict)
    credential_schema: dict = field(default_factory=dict)


# Registry of every connector a workspace may configure. A provider listed
# here but without a registered transport factory reports DISCONNECTED /
# INCONCLUSIVE rather than fabricating data.
CONNECTOR_METADATA: dict[str, ConnectorMetadata] = {}


def register_metadata(metadata: ConnectorMetadata) -> ConnectorMetadata:
    """Register static provider metadata at import time."""
    CONNECTOR_METADATA[metadata.provider_key] = metadata
    return metadata


def get_metadata(provider_key: str) -> ConnectorMetadata | None:
    return CONNECTOR_METADATA.get(provider_key)