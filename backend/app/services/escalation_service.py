"""
Escalation Policy Service — Multi-tier alert routing.

Routes alerts through configurable escalation tiers:
  Tier 1: Slack notification to the team channel
  Tier 2: PagerDuty incident (after delay)
  Tier 3: Email to management (after longer delay)

Policies are per-organization and configurable via API.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Escalation severity ──────────────────────────────────────────────────────

class EscalationLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class EscalationStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    ACKNOWLEDGED = "acknowledged"


# ── Escalation tier ──────────────────────────────────────────────────────────

@dataclass
class EscalationTier:
    """One tier in an escalation policy."""
    level: int                          # 1, 2, 3 ...
    delay_seconds: int                  # wait before escalating to this tier
    channels: list[str]                 # ["slack", "pagerduty", "email"]
    notify_roles: list[str] = field(default_factory=list)  # ["admin", "legal", "cto"]
    notify_emails: list[str] = field(default_factory=list)
    slack_channel: str | None = None    # override default channel

    def to_dict(self) -> dict:
        return asdict(self)


# ── Escalation policy ────────────────────────────────────────────────────────

@dataclass
class EscalationPolicy:
    """An organization's escalation policy for a category of alerts."""
    id: str
    organization_id: str
    name: str
    category: str                       # "compliance", "esignature", "migration", "general"
    enabled: bool = True
    tiers: list[EscalationTier] = field(default_factory=list)

    # Alert filtering
    min_severity: EscalationLevel = EscalationLevel.WARNING
    alert_categories: list[str] = field(default_factory=lambda: ["all"])

    # Throttling
    cooldown_seconds: int = 300         # minimum gap between alerts of same type
    max_alerts_per_hour: int = 20

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["min_severity"] = self.min_severity.value
        d["tiers"] = [t.to_dict() for t in self.tiers]
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        return d


# ── Escalation incident ──────────────────────────────────────────────────────

@dataclass
class EscalationIncident:
    """Tracks an in-flight escalation."""
    id: str
    policy_id: str
    organization_id: str
    category: str
    title: str
    message: str
    severity: EscalationLevel
    current_tier: int = 1
    status: EscalationStatus = EscalationStatus.PENDING
    details: dict[str, Any] = field(default_factory=dict)

    # Tracking
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    escalated_at: datetime | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None

    # Notifications sent
    notifications_sent: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        d["status"] = self.status.value
        d["created_at"] = self.created_at.isoformat()
        d["escalated_at"] = self.escalated_at.isoformat() if self.escalated_at else None
        d["acknowledged_at"] = self.acknowledged_at.isoformat() if self.acknowledged_at else None
        d["resolved_at"] = self.resolved_at.isoformat() if self.resolved_at else None
        return d


# ── Default policies ─────────────────────────────────────────────────────────

def _default_compliance_policy(org_id: str) -> EscalationPolicy:
    return EscalationPolicy(
        id=f"policy-compliance-{org_id}",
        organization_id=org_id,
        name="Compliance Violation Escalation",
        category="compliance",
        tiers=[
            EscalationTier(level=1, delay_seconds=0, channels=["slack"],
                           notify_roles=["legal"], slack_channel="#compliance-alerts"),
            EscalationTier(level=2, delay_seconds=300, channels=["pagerduty"],
                           notify_roles=["legal", "cto"]),
            EscalationTier(level=3, delay_seconds=3600, channels=["email"],
                           notify_roles=["admin"], notify_emails=["ceo@company.com"]),
        ],
        min_severity=EscalationLevel.WARNING,
        cooldown_seconds=600,
    )


def _default_esignature_policy(org_id: str) -> EscalationPolicy:
    return EscalationPolicy(
        id=f"policy-esignature-{org_id}",
        organization_id=org_id,
        name="E-Signature Alert Escalation",
        category="esignature",
        tiers=[
            EscalationTier(level=1, delay_seconds=0, channels=["slack"],
                           notify_roles=["admin"], slack_channel="#signing-alerts"),
            EscalationTier(level=2, delay_seconds=600, channels=["pagerduty"],
                           notify_roles=["admin", "legal"]),
        ],
        min_severity=EscalationLevel.WARNING,
        cooldown_seconds=300,
    )


def _default_migration_policy(org_id: str) -> EscalationPolicy:
    return EscalationPolicy(
        id=f"policy-migration-{org_id}",
        organization_id=org_id,
        name="Migration Alert Escalation",
        category="migration",
        tiers=[
            EscalationTier(level=1, delay_seconds=0, channels=["slack"],
                           notify_roles=["admin"], slack_channel="#ops-alerts"),
            EscalationTier(level=2, delay_seconds=120, channels=["pagerduty"],
                           notify_roles=["admin", "cto"]),
        ],
        min_severity=EscalationLevel.WARNING,
        cooldown_seconds=120,
    )


# ── Escalation service ──────────────────────────────────────────────────────

class EscalationService:
    """
    In-memory escalation policy manager and incident tracker.

    In production, persist policies in the database. This keeps them
    in memory for fast lookups and easy testing.
    """

    def __init__(self):
        self._policies: dict[str, EscalationPolicy] = {}
        self._incidents: list[EscalationIncident] = []
        self._cooldown_tracker: dict[str, float] = {}  # key → last_alert_time
        self._hourly_counter: dict[str, list[float]] = {}  # key → [timestamps]
        self._incident_counter = 0

    # ── Policy management ───────────────────────────────────────────────

    def get_policies(self, organization_id: str) -> list[EscalationPolicy]:
        return [
            p for p in self._policies.values()
            if p.organization_id == organization_id
        ]

    def get_policy(self, policy_id: str) -> EscalationPolicy | None:
        return self._policies.get(policy_id)

    def create_policy(self, policy: EscalationPolicy) -> EscalationPolicy:
        self._policies[policy.id] = policy
        logger.info("Escalation policy created: %s", policy.name)
        return policy

    def update_policy(self, policy_id: str, updates: dict) -> EscalationPolicy | None:
        policy = self._policies.get(policy_id)
        if not policy:
            return None

        for key, value in updates.items():
            if key == "tiers" and isinstance(value, list):
                policy.tiers = [EscalationTier(**t) for t in value]
            elif key == "min_severity":
                policy.min_severity = EscalationLevel(value)
            elif hasattr(policy, key):
                setattr(policy, key, value)

        policy.updated_at = datetime.now(timezone.utc)
        return policy

    def delete_policy(self, policy_id: str) -> bool:
        if policy_id in self._policies:
            del self._policies[policy_id]
            return True
        return False

    def ensure_default_policies(self, organization_id: str):
        """Create default policies if none exist for this organization."""
        existing = self.get_policies(organization_id)
        categories = {p.category for p in existing}

        defaults = [
            _default_compliance_policy,
            _default_esignature_policy,
            _default_migration_policy,
        ]

        for factory in defaults:
            # Use a deterministic ID so we don't duplicate
            policy = factory(organization_id)
            if policy.id not in self._policies:
                self.create_policy(policy)

    # ── Incident management ─────────────────────────────────────────────

    def create_incident(
        self,
        organization_id: str,
        category: str,
        title: str,
        message: str,
        severity: EscalationLevel,
        details: dict[str, Any] | None = None,
    ) -> EscalationIncident | None:
        """
        Create an escalation incident.

        Returns None if throttled (cooldown or rate-limit).
        """
        # Find matching policy
        policy = self._find_matching_policy(organization_id, category, severity)
        if not policy or not policy.enabled:
            logger.debug("No escalation policy for %s/%s", category, severity)
            return None

        # Check throttling
        throttle_key = f"{organization_id}:{category}"
        if self._is_throttled(throttle_key, policy):
            logger.info("Alert throttled: %s", throttle_key)
            return None

        self._incident_counter += 1
        incident = EscalationIncident(
            id=f"inc-{self._incident_counter:06d}",
            policy_id=policy.id,
            organization_id=organization_id,
            category=category,
            title=title,
            message=message,
            severity=severity,
            details=details or {},
            current_tier=1,
            status=EscalationStatus.PENDING,
        )
        self._incidents.append(incident)

        # Record throttle timestamp
        self._cooldown_tracker[throttle_key] = time.time()
        self._record_hourly(throttle_key)

        logger.info(
            "Escalation incident created: %s (tier 1: %s)",
            incident.id, [t.channels for t in policy.tiers if t.level == 1],
        )

        return incident

    def acknowledge_incident(self, incident_id: str, by: str = "system") -> bool:
        incident = self._get_incident(incident_id)
        if not incident:
            return False

        incident.status = EscalationStatus.ACKNOWLEDGED
        incident.acknowledged_at = datetime.now(timezone.utc)
        incident.notifications_sent.append({
            "action": "acknowledged",
            "by": by,
            "at": incident.acknowledged_at.isoformat(),
        })
        return True

    def resolve_incident(self, incident_id: str, by: str = "system") -> bool:
        incident = self._get_incident(incident_id)
        if not incident:
            return False

        incident.status = EscalationStatus.RESOLVED
        incident.resolved_at = datetime.now(timezone.utc)
        incident.notifications_sent.append({
            "action": "resolved",
            "by": by,
            "at": incident.resolved_at.isoformat(),
        })
        return True

    def get_incidents(
        self,
        organization_id: str | None = None,
        category: str | None = None,
        status: EscalationStatus | None = None,
        limit: int = 50,
    ) -> list[EscalationIncident]:
        filtered = self._incidents

        if organization_id:
            filtered = [i for i in filtered if i.organization_id == organization_id]
        if category:
            filtered = [i for i in filtered if i.category == category]
        if status:
            filtered = [i for i in filtered if i.status == status]

        return list(reversed(filtered[-limit:]))

    def get_incident_stats(self, organization_id: str) -> dict[str, Any]:
        incidents = [i for i in self._incidents if i.organization_id == organization_id]
        now = datetime.now(timezone.utc)
        last_24h = [
            i for i in incidents
            if (now - i.created_at).total_seconds() < 86400
        ]

        by_status: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for i in last_24h:
            by_status[i.status.value] = by_status.get(i.status.value, 0) + 1
            by_category[i.category] = by_category.get(i.category, 0) + 1

        return {
            "total": len(incidents),
            "last_24h": len(last_24h),
            "by_status": by_status,
            "by_category": by_category,
            "active": sum(1 for i in incidents if i.status != EscalationStatus.RESOLVED),
        }

    # ── Private helpers ─────────────────────────────────────────────────

    def _find_matching_policy(
        self, organization_id: str, category: str, severity: EscalationLevel,
    ) -> EscalationPolicy | None:
        severity_order = {
            EscalationLevel.INFO: 0,
            EscalationLevel.WARNING: 1,
            EscalationLevel.CRITICAL: 2,
        }

        for policy in self._policies.values():
            if (policy.organization_id == organization_id
                    and policy.category == category
                    and policy.enabled):
                if severity_order.get(severity, 0) >= severity_order.get(policy.min_severity, 0):
                    return policy
        return None

    def _is_throttled(self, key: str, policy: EscalationPolicy) -> bool:
        last_time = self._cooldown_tracker.get(key)
        if last_time and (time.time() - last_time) < policy.cooldown_seconds:
            return True

        # Check hourly rate
        timestamps = self._hourly_counter.get(key, [])
        one_hour_ago = time.time() - 3600
        recent = [t for t in timestamps if t > one_hour_ago]
        if len(recent) >= policy.max_alerts_per_hour:
            return True

        return False

    def _record_hourly(self, key: str):
        if key not in self._hourly_counter:
            self._hourly_counter[key] = []
        self._hourly_counter[key].append(time.time())
        # Prune old entries
        one_hour_ago = time.time() - 3600
        self._hourly_counter[key] = [
            t for t in self._hourly_counter[key] if t > one_hour_ago
        ]

    def _get_incident(self, incident_id: str) -> EscalationIncident | None:
        for i in self._incidents:
            if i.id == incident_id:
                return i
        return None


# ── Global singleton ────────────────────────────────────────────────────────

_escalation_service: EscalationService | None = None


def get_escalation_service() -> EscalationService:
    global _escalation_service
    if _escalation_service is None:
        _escalation_service = EscalationService()
    return _escalation_service
