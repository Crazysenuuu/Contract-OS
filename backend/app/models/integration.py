"""Integration connector models (spec 24.6).

Prebuilt connectors for common ERP/CRM/payment providers (Salesforce,
HubSpot, NetSuite, SAP, Stripe). Each connector stores its provider type,
credentials reference, and per-provider settings; the integration service
builds provider-specific payloads and dispatches them through the webhook
infrastructure.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class IntegrationConnector(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A configured third-party integration connector."""

    __tablename__ = "integration_connectors"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'salesforce' | 'hubspot' | 'netsuite' | 'sap' | 'stripe'
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Credentials are stored encrypted via the privacy service's field-level
    # encryption (ciphertext reference here); never plaintext.
    credentials_ref: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    settings: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        # Per-provider settings: e.g. {"account_id": ..., "object_types": [...]}
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Events to sync, e.g. ["agreement.signed", "agreement.executed"]
    events: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    last_sync_status: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        # 'success' | 'failed' | 'pending'
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    organization = relationship("Organization")


PROVIDER_LABELS = {
    "salesforce": "Salesforce",
    "hubspot": "HubSpot",
    "netsuite": "NetSuite",
    "sap": "SAP",
    "stripe": "Stripe",
}

SUPPORTED_EVENTS = [
    "agreement.created",
    "agreement.signed",
    "agreement.executed",
    "agreement.renewed",
    "agreement.terminated",
    "obligation.due",
    "payment.recorded",
]