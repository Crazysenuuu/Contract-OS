"""Enterprise identity models (spec 1.21.8-1.21.9 / 2.15).

- SSOConnection: a per-organization SAML 2.0 / OIDC connection. Secrets are
  stored as references to the secret manager, never as raw values.
- SCIMToken: bearer token for SCIM 2.0 automated user provisioning.
- IdentityProviderEvent: audit log of IdP-initiated events (SSO logins,
  SCIM creates/updates/deletes).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SSOConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A SAML/OIDC connection for an organization (spec 1.21.8)."""

    __tablename__ = "sso_connections"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    protocol: Mapped[str] = mapped_column(
        String(20), nullable=False  # 'saml' | 'oidc'
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # OIDC: issuer/discovery URL. SAML: IdP metadata URL or entity id.
    issuer: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # OIDC client configuration. The secret is a reference into the secret
    # manager (spec 1.22.14) - never the raw client secret.
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_secret_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # SAML configuration.
    idp_metadata_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    idp_certificate: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Platform side.
    sp_entity_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    acs_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    domains: Mapped[dict | None] = mapped_column(
        JSON, nullable=True  # ["corp.example.com"] for domain-based routing
    )

    # default role key assigned to SSO-provisioned members
    default_role: Mapped[str | None] = mapped_column(String(100), nullable=True)

    enforce_sso: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SCIMToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bearer credential for SCIM 2.0 provisioning (spec 1.21.9)."""

    __tablename__ = "scim_tokens"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdentityProviderEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Audit record of IdP events: SSO assertions and SCIM operations."""

    __tablename__ = "identity_provider_events"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False
        # 'sso_login', 'scim_user_created', 'scim_user_updated',
        # 'scim_user_deactivated', 'scim_group_synced'
    )

    idp_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_idp_events_org_type", "organization_id", "event_type"),
    )
