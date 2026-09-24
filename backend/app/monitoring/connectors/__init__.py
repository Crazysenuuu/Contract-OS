"""Connector registry wiring (spec 3.15.10).

Importing this package registers every *real* transport connector. Provider
keys without a registered factory are unsupported and produce DISCONNECTED
health — never fabricated observations.
"""

from app.monitoring.connectors.base import (
    CONNECTOR_METADATA,
    Connector,
    ConnectorCapability,
    ConnectorMetadata,
    ExternalObservation,
    register_metadata,
)
from app.monitoring.connectors.implementations.http import HTTPRESTConnector
from app.monitoring.connectors.registry import connector_registry

register_metadata(
    ConnectorMetadata(
        provider_key=HTTPRESTConnector.provider_key,
        label="Generic REST / HTTP API",
        integration_types=("REST_API",),
        capabilities=frozenset({ConnectorCapability.READ}),
        configuration_schema={
            "base_url": {
                "type": "string",
                "description": "Root URL of the source API",
            },
            "auth": {"type": "string", "description": "auth scheme, default 'bearer'"},
            "headers": {
                "type": "object",
                "description": "Static (non-secret) HTTP headers",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "Per-request timeout",
            },
            "health_path": {"type": "string"},
            "id_field": {"type": "string"},
            "observed_at_field": {"type": "string"},
        },
        credential_schema={
            "env": {"type": "string", "description": "env://SECRET_NAME bearer token"},
        },
    )
)
connector_registry.register(HTTPRESTConnector.provider_key, HTTPRESTConnector)

__all__ = [
    "Connector",
    "ConnectorCapability",
    "ConnectorMetadata",
    "CONNECTOR_METADATA",
    "ExternalObservation",
    "connector_registry",
    "register_metadata",
]