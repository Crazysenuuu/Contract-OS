"""Connector registry (spec 3.15.10).

Factories are registered per ``provider_key``. ``create`` raises
``UnsupportedConnector`` for provider keys without a registered factory —
the integration then reports DISCONNECTED and monitoring stays INCONCLUSIVE
instead of faking success.
"""

from __future__ import annotations

from typing import Any, Callable

from app.monitoring.connectors.base import Connector
from app.monitoring.exceptions import UnsupportedConnector
from app.monitoring.credentials import ResolvedCredential


class ConnectorRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., Connector]] = {}

    def register(self, provider_key: str, connector_factory: Callable[..., Connector]) -> None:
        self._factories[provider_key] = connector_factory

    def create(
        self,
        provider_key: str,
        credentials: list[ResolvedCredential],
        configuration: dict,
    ) -> Connector:
        factory = self._factories.get(provider_key)
        if factory is None:
            raise UnsupportedConnector(f"Unsupported connector: {provider_key}")
        return factory(
            credentials=credentials,
            configuration=configuration,
        )

    def supports(self, provider_key: str) -> bool:
        return provider_key in self._factories


connector_registry = ConnectorRegistry()