"""Multi-tenant and branding service."""
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.tenant import (
    Tenant, TenantBranding, TenantTheme, TenantInvitation, TenantAuditLog
)


class TenantService:
    """Service for multi-tenant management and white-label branding."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ===== TENANT MANAGEMENT =====

    async def create_tenant(
        self,
        organization_id: str,
        slug: str,
        plan: str = "free"
    ) -> Tenant:
        """Create a new tenant for an organization."""
        # Check slug uniqueness
        existing = await self.db.execute(
            select(Tenant).where(Tenant.slug == slug)
        )
        if existing.scalar_one_or_none():
            raise ValueError(f"Slug '{slug}' is already taken")

        limits = self._get_plan_limits(plan)
        tenant = Tenant(
            organization_id=organization_id,
            slug=slug,
            plan=plan,
            max_users=limits["max_users"],
            max_agreements=limits["max_agreements"],
            storage_limit_mb=limits["storage_limit_mb"],
            features=self._get_plan_features(plan),
        )
        self.db.add(tenant)
        await self.db.flush()

        # Create default branding
        branding = TenantBranding(
            tenant_id=tenant.id,
            company_name="ContractOS",
        )
        self.db.add(branding)

        await self.db.commit()
        await self.db.refresh(tenant)
        return tenant

    async def get_tenant(self, organization_id: str) -> Optional[Tenant]:
        """Get tenant by organization ID."""
        result = await self.db.execute(
            select(Tenant).where(Tenant.organization_id == organization_id)
        )
        return result.scalar_one_or_none()

    async def get_tenant_by_slug(self, slug: str) -> Optional[Tenant]:
        """Get tenant by slug (for subdomain routing)."""
        result = await self.db.execute(select(Tenant).where(Tenant.slug == slug))
        return result.scalar_one_or_none()

    async def update_tenant_plan(
        self,
        tenant_id: str,
        new_plan: str
    ) -> Tenant:
        """Update tenant plan and limits."""
        result = await self.db.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise ValueError("Tenant not found")

        old_plan = tenant.plan
        tenant.plan = new_plan
        limits = self._get_plan_limits(new_plan)
        tenant.max_users = limits["max_users"]
        tenant.max_agreements = limits["max_agreements"]
        tenant.storage_limit_mb = limits["storage_limit_mb"]
        tenant.features = self._get_plan_features(new_plan)

        await self._log_audit(tenant_id, "plan_changed", "tenant", tenant_id, {
            "old_plan": old_plan,
            "new_plan": new_plan
        })

        await self.db.commit()
        await self.db.refresh(tenant)
        return tenant

    async def suspend_tenant(self, tenant_id: str, reason: str) -> Tenant:
        """Suspend a tenant."""
        result = await self.db.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise ValueError("Tenant not found")

        tenant.is_active = False
        tenant.suspended_at = datetime.utcnow()
        tenant.suspension_reason = reason

        await self._log_audit(tenant_id, "tenant_suspended", "tenant", tenant_id, {"reason": reason})
        await self.db.commit()
        await self.db.refresh(tenant)
        return tenant

    async def check_limits(self, tenant_id: str) -> Dict:
        """Check if tenant is within plan limits."""
        result = await self.db.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            return {"allowed": False, "reason": "Tenant not found"}

        if not tenant.is_active:
            return {"allowed": False, "reason": "Tenant is suspended"}

        if tenant.suspended_at:
            return {"allowed": False, "reason": "Account suspended"}

        issues = []
        if tenant.users_count >= tenant.max_users:
            issues.append(f"User limit reached ({tenant.max_users})")
        if tenant.storage_used_mb >= tenant.storage_limit_mb:
            issues.append(f"Storage limit reached ({tenant.storage_limit_mb}MB)")

        return {
            "allowed": len(issues) == 0,
            "issues": issues,
            "usage": {
                "users": f"{tenant.users_count}/{tenant.max_users}",
                "storage_mb": f"{tenant.storage_used_mb}/{tenant.storage_limit_mb}",
                "agreements": tenant.max_agreements,
            }
        }

    # ===== BRANDING =====

    async def get_branding(self, tenant_id: str) -> Optional[TenantBranding]:
        """Get branding configuration."""
        result = await self.db.execute(
            select(TenantBranding).where(TenantBranding.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def update_branding(
        self,
        tenant_id: str,
        updates: Dict[str, Any]
    ) -> TenantBranding:
        """Update branding configuration."""
        branding = await self.get_branding(tenant_id)
        if not branding:
            branding = TenantBranding(tenant_id=tenant_id)
            self.db.add(branding)

        for key, value in updates.items():
            if hasattr(branding, key):
                setattr(branding, key, value)

        branding.updated_at = datetime.utcnow()

        await self._log_audit(tenant_id, "branding_updated", "branding", branding.id, updates)
        await self.db.commit()
        await self.db.refresh(branding)
        return branding

    async def generate_css_variables(self, tenant_id: str) -> str:
        """Generate CSS custom properties from branding."""
        branding = await self.get_branding(tenant_id)
        if not branding:
            return ":root {}"

        variables = f"""
:root {{
  --color-primary: {branding.primary_color or '#0070f3'};
  --color-secondary: {branding.secondary_color or '#1a1a2e'};
  --color-accent: {branding.accent_color or '#00d4ff'};
  --color-background: {branding.background_color or '#ffffff'};
  --color-text: {branding.text_color or '#1a1a2e'};
  --color-error: {branding.error_color or '#ee0000'};
  --color-success: {branding.success_color or '#00aa00'};
  --font-family: {branding.font_family or 'Inter, system-ui, sans-serif'};
  --font-heading: {branding.heading_font or branding.font_family or 'Inter, system-ui, sans-serif'};
  --font-size-base: {branding.font_size_base or '16px'};
}}
"""
        return variables

    async def get_branding_payload(self, tenant_id: str) -> Dict:
        """Get full branding payload for frontend."""
        branding = await self.get_branding(tenant_id)
        if not branding:
            return {}

        return {
            "company_name": branding.company_name,
            "tagline": branding.tagline,
            "logo_url": branding.logo_url,
            "logo_dark_url": branding.logo_dark_url,
            "favicon_url": branding.favicon_url,
            "colors": {
                "primary": branding.primary_color,
                "secondary": branding.secondary_color,
                "accent": branding.accent_color,
                "background": branding.background_color,
                "text": branding.text_color,
                "error": branding.error_color,
                "success": branding.success_color,
            },
            "typography": {
                "font_family": branding.font_family,
                "heading_font": branding.heading_font,
                "font_size_base": branding.font_size_base,
            },
            "email": {
                "from_name": branding.email_from_name,
                "from_address": branding.email_from_address,
                "logo_url": branding.email_logo_url,
            },
            "footer": {
                "text": branding.footer_text,
                "links": branding.footer_links,
            },
            "custom_css": branding.custom_css,
            "custom_domain": branding.custom_domain,
        }

    # ===== THEMES =====

    async def create_theme(
        self,
        tenant_id: str,
        name: str,
        colors: Dict,
        description: str = None,
        is_default: bool = False,
        supports_dark_mode: bool = False,
        dark_mode_colors: Dict = None
    ) -> TenantTheme:
        """Create a theme preset."""
        theme = TenantTheme(
            tenant_id=tenant_id,
            name=name,
            description=description,
            colors=colors,
            is_default=is_default,
            supports_dark_mode=supports_dark_mode,
            dark_mode_colors=dark_mode_colors,
        )

        if is_default:
            # Unset other defaults
            await self.db.execute(
                TenantTheme.__table__
                .update()
                .where(
                    TenantTheme.tenant_id == tenant_id,
                    TenantTheme.is_default == True,  # noqa: E712
                )
                .values(is_default=False)
            )

        self.db.add(theme)
        await self.db.commit()
        await self.db.refresh(theme)
        return theme

    async def get_themes(self, tenant_id: str) -> List[TenantTheme]:
        """Get all themes for a tenant."""
        result = await self.db.execute(
            select(TenantTheme)
            .where(TenantTheme.tenant_id == tenant_id)
            .order_by(TenantTheme.is_default.desc(), TenantTheme.name)
        )
        return result.scalars().all()

    # ===== INVITATIONS =====

    async def invite_user(
        self,
        tenant_id: str,
        invited_by: str,
        email: str,
        role: str = "member"
    ) -> TenantInvitation:
        """Invite a user to the tenant."""
        token = str(uuid.uuid4())
        invitation = TenantInvitation(
            tenant_id=tenant_id,
            invited_by=invited_by,
            email=email,
            role=role,
            token=token,
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        self.db.add(invitation)

        await self._log_audit(tenant_id, "user_invited", "invitation", None, {
            "email": email,
            "role": role
        })

        await self.db.commit()
        await self.db.refresh(invitation)
        return invitation

    async def accept_invitation(self, token: str) -> Optional[TenantInvitation]:
        """Accept a tenant invitation."""
        result = await self.db.execute(
            select(TenantInvitation).where(
                TenantInvitation.token == token,
                TenantInvitation.accepted_at.is_(None),
                TenantInvitation.revoked_at.is_(None)
            )
        )
        invitation = result.scalar_one_or_none()

        if not invitation:
            return None

        if invitation.expires_at < datetime.utcnow():
            return None

        invitation.accepted_at = datetime.utcnow()

        # Update tenant user count
        tenant_result = await self.db.execute(
            select(Tenant).where(Tenant.id == invitation.tenant_id)
        )
        tenant = tenant_result.scalar_one_or_none()
        if tenant:
            tenant.users_count += 1

        await self.db.commit()
        await self.db.refresh(invitation)
        return invitation

    # ===== AUDIT LOG =====

    async def _log_audit(
        self,
        tenant_id: str,
        action: str,
        resource_type: str,
        resource_id: str = None,
        details: Dict = None,
        user_id: str = None
    ):
        """Log an audit event."""
        log = TenantAuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
        )
        self.db.add(log)
        await self.db.flush()

    async def get_audit_logs(
        self,
        tenant_id: str,
        limit: int = 100,
        offset: int = 0,
        action: str = None,
        resource_type: str = None
    ) -> List[TenantAuditLog]:
        """Get audit logs for a tenant."""
        query = select(TenantAuditLog).where(TenantAuditLog.tenant_id == tenant_id)

        if action:
            query = query.where(TenantAuditLog.action == action)
        if resource_type:
            query = query.where(TenantAuditLog.resource_type == resource_type)

        result = await self.db.execute(
            query.order_by(TenantAuditLog.created_at.desc()).offset(offset).limit(limit)
        )
        return result.scalars().all()

    # ===== HELPERS =====

    def _get_plan_limits(self, plan: str) -> Dict:
        """Get plan limits."""
        limits = {
            "free": {"max_users": 5, "max_agreements": 100, "storage_limit_mb": 100},
            "starter": {"max_users": 15, "max_agreements": 500, "storage_limit_mb": 1000},
            "professional": {"max_users": 50, "max_agreements": 5000, "storage_limit_mb": 10000},
            "enterprise": {"max_users": 9999, "max_agreements": 999999, "storage_limit_mb": 100000},
        }
        return limits.get(plan, limits["free"])

    def _get_plan_features(self, plan: str) -> Dict:
        """Get plan features."""
        features = {
            "free": {
                "ai_analysis": True,
                "compliance": True,
                "esignature": False,
                "webhooks": False,
                "bulk_operations": False,
                "custom_branding": False,
                "sso": False,
                "audit_log": True,
                "api_access": False,
            },
            "starter": {
                "ai_analysis": True,
                "compliance": True,
                "esignature": True,
                "webhooks": True,
                "bulk_operations": False,
                "custom_branding": False,
                "sso": False,
                "audit_log": True,
                "api_access": True,
            },
            "professional": {
                "ai_analysis": True,
                "compliance": True,
                "esignature": True,
                "webhooks": True,
                "bulk_operations": True,
                "custom_branding": True,
                "sso": False,
                "audit_log": True,
                "api_access": True,
            },
            "enterprise": {
                "ai_analysis": True,
                "compliance": True,
                "esignature": True,
                "webhooks": True,
                "bulk_operations": True,
                "custom_branding": True,
                "sso": True,
                "audit_log": True,
                "api_access": True,
            },
        }
        return features.get(plan, features["free"])