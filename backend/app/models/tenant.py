"""Multi-tenant and white-label models for branding, themes, and tenant isolation."""
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import Column, String, Integer, Text, DateTime, JSON, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.models.base import Base


class Tenant(Base):
    """Tenant (organization) configuration for multi-tenancy."""
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), unique=True, nullable=False)

    # Tenant settings
    slug = Column(String(100), unique=True, nullable=False)  # subdomain: {slug}.contractos.lk
    plan = Column(String(50), default="free")  # free, starter, professional, enterprise
    max_users = Column(Integer, default=5)
    max_agreements = Column(Integer, default=100)
    storage_limit_mb = Column(Integer, default=100)

    # Feature flags
    features = Column(JSON, default=dict)
    # {
    #   "ai_analysis": true,
    #   "compliance": true,
    #   "esignature": false,
    #   "webhooks": true,
    #   "bulk_operations": false,
    #   "custom_branding": false,
    #   "sso": false,
    #   "audit_log": true,
    #   "api_access": false
    # }

    # Limits
    api_rate_limit = Column(Integer, default=100)  # requests per minute
    storage_used_mb = Column(Integer, default=0)
    users_count = Column(Integer, default=1)

    # Status
    is_active = Column(Boolean, default=True)
    trial_ends_at = Column(DateTime)
    suspended_at = Column(DateTime)
    suspension_reason = Column(Text)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    # Branding/themes are curated per-tenant config (bounded), read by
    # white-label rendering after awaits; keep eager (AsyncSession cannot
    # lazy-load without MissingGreenlet).
    branding = relationship(
        "TenantBranding", back_populates="tenant", lazy="selectin", uselist=False
    )
    themes = relationship(
        "TenantTheme", back_populates="tenant", lazy="selectin"
    )


class TenantBranding(Base):
    """White-label branding configuration."""
    __tablename__ = "tenant_branding"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), unique=True, nullable=False)

    # Company info
    company_name = Column(String(200))
    tagline = Column(String(300))

    # Logo
    logo_url = Column(String(500))
    logo_dark_url = Column(String(500))  # Dark mode logo
    favicon_url = Column(String(500))
    email_logo_url = Column(String(500))

    # Colors
    primary_color = Column(String(7), default="#0070f3")  # Hex
    secondary_color = Column(String(7), default="#1a1a2e")
    accent_color = Column(String(7), default="#00d4ff")
    background_color = Column(String(7), default="#ffffff")
    text_color = Column(String(7), default="#1a1a2e")
    error_color = Column(String(7), default="#ee0000")
    success_color = Column(String(7), default="#00aa00")

    # Typography
    font_family = Column(String(200), default="Inter, system-ui, sans-serif")
    heading_font = Column(String(200))
    font_size_base = Column(String(10), default="16px")

    # Custom CSS
    custom_css = Column(Text)
    custom_head_html = Column(Text)  # For analytics, etc.

    # Email branding
    email_from_name = Column(String(100))
    email_from_address = Column(String(200))
    email_footer_html = Column(Text)

    # Domain
    custom_domain = Column(String(200))  # e.g., contracts.yourcompany.com
    domain_verified = Column(Boolean, default=False)

    # Footer
    footer_text = Column(Text)
    footer_links = Column(JSON, default=list)  # [{"label": "Privacy", "url": "..."}]

    # Legal
    privacy_policy_url = Column(String(500))
    terms_of_service_url = Column(String(500))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant", back_populates="branding")


class TenantTheme(Base):
    """Theme presets for the tenant."""
    __tablename__ = "tenant_themes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    name = Column(String(100), nullable=False)
    description = Column(Text)

    # Theme configuration
    colors = Column(JSON, nullable=False)
    # {
    #   "primary": "#0070f3",
    #   "secondary": "#1a1a2e",
    #   "accent": "#00d4ff",
    #   "background": "#ffffff",
    #   "surface": "#f5f5f5",
    #   "text": "#1a1a2e",
    #   "textSecondary": "#666666",
    #   "border": "#e0e0e0",
    #   "error": "#ee0000",
    #   "warning": "#ffaa00",
    #   "success": "#00aa00"
    # }

    typography = Column(JSON, default=dict)
    # {
    #   "fontFamily": "Inter, system-ui, sans-serif",
    #   "headingFamily": "Inter, system-ui, sans-serif",
    #   "sizes": {"xs": "0.75rem", "sm": "0.875rem", "base": "1rem", "lg": "1.125rem"}
    # }

    spacing = Column(JSON, default=dict)  # Custom spacing scale
    border_radius = Column(JSON, default=dict)  # Custom border radius
    shadows = Column(JSON, default=dict)  # Custom shadows

    is_default = Column(Boolean, default=False)
    supports_dark_mode = Column(Boolean, default=False)
    dark_mode_colors = Column(JSON)  # Override colors for dark mode

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant", back_populates="themes")


class TenantInvitation(Base):
    """Tenant user invitations."""
    __tablename__ = "tenant_invitations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    invited_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    email = Column(String(200), nullable=False)
    role = Column(String(50), default="member")  # admin, member, viewer
    token = Column(String(100), unique=True, nullable=False)

    # Status
    accepted_at = Column(DateTime)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow)


class TenantAuditLog(Base):
    """Tenant-specific audit log for compliance."""
    __tablename__ = "tenant_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    action = Column(String(100), nullable=False)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(UUID(as_uuid=True))

    # Details
    details = Column(JSON, default=dict)
    ip_address = Column(String(50))
    user_agent = Column(String(500))

    # Changes
    old_value = Column(JSON)
    new_value = Column(JSON)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
