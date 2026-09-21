"""
Escalation Policy Service — Multi-tier alert routing.

Routes alerts through configurable escalation tiers:
  Tier 1: Slack notification to the team channel
  Tier 2: PagerDuty incident (after delay)
  Tier 3: Email to management (after longer delay)

Policies are per-organization and configurable via API.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Incident records live in the shared StateStore (Redis when configured) so
# acknowledge/resolve/stats work across API replicas. Incidents expire after
# 7 days — they are operational alert state, not audit history.
INCIDENT_TTL_SECONDS = 7 * 24 * 3600


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
    Escalation policy manager and incident tracker.

    Policies are process-local (they are configuration, seeded by
    ``ensure_default_policies`` and managed via the admin API; every replica
    seeds the same defaults). Throttling counters and incident records live
    in the shared StateStore (Redis when REDIS_URL is set) so a cooldown
    applies across replicas and an incident acknowledged on one worker is
    visible on all of them.
    """

    def __init__(self):
        self._policies: dict[str, EscalationPolicy] = {}

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

        # Check throttling (shared counters — see _is_throttled)
        throttle_key = f"{organization_id}:{category}"
        if self._is_throttled(throttle_key, policy):
            logger.info("Alert throttled: %s", throttle_key)
            return None

        # Unique across replicas: a per-process counter would collide in the
        # shared incident store (two workers both minting inc-000001).
        import uuid as _uuid

        incident = EscalationIncident(
            id=f"inc-{_uuid.uuid4().hex[:12]}",
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
        self._store_incident(incident)

        # Record throttle timestamp (shared across replicas)
        self._record_throttle(throttle_key, policy)

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
        self._store_incident(incident)
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
        self._store_incident(incident)
        return True

    def get_incidents(
        self,
        organization_id: str | None = None,
        category: str | None = None,
        status: EscalationStatus | None = None,
        limit: int = 50,
    ) -> list[EscalationIncident]:
        incidents = self._load_incidents()

        if organization_id:
            incidents = [i for i in incidents if i.organization_id == organization_id]
        if category:
            incidents = [i for i in incidents if i.category == category]
        if status:
            incidents = [i for i in incidents if i.status == status]

        return list(reversed(incidents[-limit:]))

    def get_incident_stats(self, organization_id: str) -> dict[str, Any]:
        incidents = [
            i for i in self._load_incidents()
            if i.organization_id == organization_id
        ]
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

    # ── Shared-state helpers (StateStore: Redis when configured) ─────────

    def get_incident(self, incident_id: str) -> EscalationIncident | None:
        """Fetch a single incident from the shared store (public API)."""
        return self._get_incident(incident_id)

    def _is_throttled(self, key: str, policy: EscalationPolicy) -> bool:
        """Read-only check — recording happens in ``_record_throttle`` only
        when an incident is actually created (matching the original flow)."""
        from app.core.distributed_state import get_state_store

        store = get_state_store()

        # Cooldown: TTL key set at last alert.
        if store.get(f"escalation:cooldown:{key}") is not None:
            return True

        # Hourly rate: shared sliding window (read-only here).
        stats = store.window_count(f"escalation:rate:{key}", window_seconds=3600)
        return stats >= policy.max_alerts_per_hour

    def _record_throttle(self, key: str, policy: EscalationPolicy):
        from app.core.distributed_state import get_state_store

        store = get_state_store()
        store.set(
            f"escalation:cooldown:{key}",
            str(time.time()),
            ttl_seconds=policy.cooldown_seconds,
        )
        # Hourly budget: record this alert's slot in the shared window.
        store.window_add(
            f"escalation:rate:{key}",
            window_seconds=3600,
            max_events=policy.max_alerts_per_hour,
        )

    def _store_incident(self, incident: EscalationIncident) -> None:
        from app.core.distributed_state import get_state_store

        store = get_state_store()
        store.set_json(
            f"escalation:incident:{incident.id}",
            incident.to_dict(),
            ttl_seconds=INCIDENT_TTL_SECONDS,
        )
        # Registry of ids per org for listing (index, not source of truth).
        index_key = f"escalation:incidents:{incident.organization_id}"
        try:
            ids = store.get_json(index_key) or []
        except (json.JSONDecodeError, TypeError):
            ids = []
        if incident.id not in ids:
            ids.append(incident.id)
        store.set_json(index_key, ids[-500:], ttl_seconds=INCIDENT_TTL_SECONDS)

    def _load_incidents(self) -> list[EscalationIncident]:
        from app.core.distributed_state import get_state_store

        store = get_state_store()
        incidents: list[EscalationIncident] = []
        for index_key in store.keys("escalation:incidents:*"):
            ids = store.get_json(index_key) or []
            for incident_id in ids:
                raw = store.get_json(f"escalation:incident:{incident_id}")
                if raw is None:
                    continue
                incidents.append(self._incident_from_dict(raw))
        # Dedup (an org index may appear under multiple scans) and sort by id.
        seen: set[str] = set()
        unique: list[EscalationIncident] = []
        for incident in sorted(incidents, key=lambda i: i.id):
            if incident.id not in seen:
                seen.add(incident.id)
                unique.append(incident)
        return unique

    @staticmethod
    def _incident_from_dict(raw: dict) -> EscalationIncident:
        def _dt(value: str | None) -> datetime | None:
            if value is None:
                return None
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed

        return EscalationIncident(
            id=raw["id"],
            policy_id=raw["policy_id"],
            organization_id=raw["organization_id"],
            category=raw["category"],
            title=raw["title"],
            message=raw["message"],
            severity=EscalationLevel(raw["severity"]),
            current_tier=raw.get("current_tier", 1),
            status=EscalationStatus(raw["status"]),
            details=raw.get("details") or {},
            created_at=_dt(raw.get("created_at")) or datetime.now(timezone.utc),
            escalated_at=_dt(raw.get("escalated_at")),
            acknowledged_at=_dt(raw.get("acknowledged_at")),
            resolved_at=_dt(raw.get("resolved_at")),
            notifications_sent=raw.get("notifications_sent") or [],
        )

    def _get_incident(self, incident_id: str) -> EscalationIncident | None:
        from app.core.distributed_state import get_state_store

        raw = get_state_store().get_json(f"escalation:incident:{incident_id}")
        if raw is None:
            return None
        try:
            return self._incident_from_dict(raw)
        except (KeyError, ValueError):
            return None


# ── Global singleton ────────────────────────────────────────────────────────

_escalation_service: EscalationService | None = None


def get_escalation_service() -> EscalationService:
    global _escalation_service
    if _escalation_service is None:
        _escalation_service = EscalationService()
    return _escalation_service
