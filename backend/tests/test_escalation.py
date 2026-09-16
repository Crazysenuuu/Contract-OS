"""
Tests for the Escalation Service.

Covers:
  - Policy CRUD operations
  - Default policy creation
  - Incident creation and throttling
  - Acknowledge / resolve lifecycle
  - Incident stats
  - Throttling (cooldown + hourly rate limit)
"""
import time
from unittest.mock import patch

import pytest

from app.services.escalation_service import (
    EscalationIncident,
    EscalationLevel,
    EscalationPolicy,
    EscalationService,
    EscalationStatus,
    EscalationTier,
    get_escalation_service,
)


@pytest.fixture(autouse=True)
def _clean_singleton():
    """Reset singleton between tests."""
    import app.services.escalation_service as mod
    mod._escalation_service = None
    yield
    mod._escalation_service = None


@pytest.fixture()
def org_id():
    return "org-test-001"


@pytest.fixture()
def service():
    return EscalationService()


# ── Policy tests ─────────────────────────────────────────────────────────────


class TestEscalationPolicies:
    def test_create_policy(self, service, org_id):
        policy = EscalationPolicy(
            id="p-1",
            organization_id=org_id,
            name="Test Policy",
            category="compliance",
            tiers=[
                EscalationTier(level=1, delay_seconds=0, channels=["slack"]),
                EscalationTier(level=2, delay_seconds=300, channels=["pagerduty"]),
            ],
        )
        created = service.create_policy(policy)
        assert created.id == "p-1"
        assert len(service.get_policies(org_id)) == 1

    def test_get_policies_scoped(self, service):
        service.create_policy(EscalationPolicy(
            id="p-1", organization_id="org-a", name="A", category="c",
        ))
        service.create_policy(EscalationPolicy(
            id="p-2", organization_id="org-b", name="B", category="c",
        ))
        assert len(service.get_policies("org-a")) == 1
        assert len(service.get_policies("org-b")) == 1

    def test_get_policy_by_id(self, service):
        service.create_policy(EscalationPolicy(
            id="p-1", organization_id="org-a", name="P1", category="c",
        ))
        assert service.get_policy("p-1") is not None
        assert service.get_policy("p-nonexistent") is None

    def test_update_policy(self, service):
        service.create_policy(EscalationPolicy(
            id="p-1", organization_id="org-a", name="Old", category="c",
        ))
        updated = service.update_policy("p-1", {"name": "New"})
        assert updated is not None
        assert updated.name == "New"

    def test_update_policy_nonexistent(self, service):
        assert service.update_policy("nope", {"name": "X"}) is None

    def test_delete_policy(self, service):
        service.create_policy(EscalationPolicy(
            id="p-1", organization_id="org-a", name="X", category="c",
        ))
        assert service.delete_policy("p-1") is True
        assert service.get_policy("p-1") is None

    def test_delete_nonexistent(self, service):
        assert service.delete_policy("nope") is False

    def test_policy_to_dict(self, service):
        policy = EscalationPolicy(
            id="p-1", organization_id="org-a", name="P", category="c",
            tiers=[EscalationTier(level=1, delay_seconds=0, channels=["slack"])],
        )
        d = policy.to_dict()
        assert d["id"] == "p-1"
        assert d["min_severity"] == "warning"
        assert len(d["tiers"]) == 1


# ── Default policies ─────────────────────────────────────────────────────────


class TestDefaultPolicies:
    def test_ensure_default_creates_three(self, service, org_id):
        service.ensure_default_policies(org_id)
        policies = service.get_policies(org_id)
        assert len(policies) == 3
        categories = {p.category for p in policies}
        assert categories == {"compliance", "esignature", "migration"}

    def test_ensure_default_idempotent(self, service, org_id):
        service.ensure_default_policies(org_id)
        service.ensure_default_policies(org_id)
        assert len(service.get_policies(org_id)) == 3

    def test_default_compliance_has_three_tiers(self, service, org_id):
        service.ensure_default_policies(org_id)
        policy = next(p for p in service.get_policies(org_id) if p.category == "compliance")
        assert len(policy.tiers) == 3
        assert policy.tiers[0].channels == ["slack"]
        assert policy.tiers[1].channels == ["pagerduty"]
        assert policy.tiers[2].channels == ["email"]


# ── Incident tests ───────────────────────────────────────────────────────────


class TestEscalationIncidents:
    def test_create_incident(self, service, org_id):
        service.ensure_default_policies(org_id)
        incident = service.create_incident(
            organization_id=org_id,
            category="compliance",
            title="Test violation",
            message="Critical deviation",
            severity=EscalationLevel.CRITICAL,
        )
        assert incident is not None
        assert incident.status == EscalationStatus.PENDING
        assert incident.current_tier == 1
        assert incident.severity == EscalationLevel.CRITICAL

    def test_create_incident_no_policy(self, service):
        incident = service.create_incident(
            organization_id="org-nonexistent",
            category="nonexistent",
            title="X",
            message="Y",
            severity=EscalationLevel.WARNING,
        )
        assert incident is None

    def test_incident_stored(self, service, org_id):
        service.ensure_default_policies(org_id)
        service.create_incident(
            organization_id=org_id,
            category="compliance",
            title="T", message="M",
            severity=EscalationLevel.WARNING,
        )
        incidents = service.get_incidents(organization_id=org_id)
        assert len(incidents) == 1

    def test_acknowledge_incident(self, service, org_id):
        service.ensure_default_policies(org_id)
        incident = service.create_incident(
            organization_id=org_id,
            category="compliance",
            title="T", message="M",
            severity=EscalationLevel.WARNING,
        )
        ok = service.acknowledge_incident(incident.id, by="admin@co.com")
        assert ok is True
        assert incident.status == EscalationStatus.ACKNOWLEDGED
        assert incident.acknowledged_at is not None

    def test_resolve_incident(self, service, org_id):
        service.ensure_default_policies(org_id)
        incident = service.create_incident(
            organization_id=org_id,
            category="compliance",
            title="T", message="M",
            severity=EscalationLevel.WARNING,
        )
        ok = service.resolve_incident(incident.id, by="admin@co.com")
        assert ok is True
        assert incident.status == EscalationStatus.RESOLVED
        assert incident.resolved_at is not None

    def test_acknowledge_nonexistent(self, service):
        assert service.acknowledge_incident("nope") is False

    def test_resolve_nonexistent(self, service):
        assert service.resolve_incident("nope") is False

    def test_incident_to_dict(self, service, org_id):
        service.ensure_default_policies(org_id)
        incident = service.create_incident(
            organization_id=org_id,
            category="compliance",
            title="T", message="M",
            severity=EscalationLevel.WARNING,
        )
        d = incident.to_dict()
        assert d["severity"] == "warning"
        assert d["status"] == "pending"
        assert "created_at" in d


# ── Filtering ────────────────────────────────────────────────────────────────


class TestIncidentFiltering:
    def _create_multiple(self, service, org_id):
        service.ensure_default_policies(org_id)
        for cat in ["compliance", "esignature", "migration"]:
            service.create_incident(
                organization_id=org_id,
                category=cat,
                title=f"{cat} alert", message="msg",
                severity=EscalationLevel.WARNING,
            )

    def test_filter_by_category(self, service, org_id):
        self._create_multiple(service, org_id)
        compliance = service.get_incidents(category="compliance")
        assert len(compliance) == 1
        assert compliance[0].category == "compliance"

    def test_filter_by_status(self, service, org_id):
        self._create_multiple(service, org_id)
        incidents = service.get_incidents()
        service.resolve_incident(incidents[0].id)

        resolved = service.get_incidents(status=EscalationStatus.RESOLVED)
        assert len(resolved) == 1

        pending = service.get_incidents(status=EscalationStatus.PENDING)
        assert len(pending) == 2


# ── Throttling ───────────────────────────────────────────────────────────────


class TestThrottling:
    def test_cooldown_throttles(self, service, org_id):
        service.ensure_default_policies(org_id)

        first = service.create_incident(
            organization_id=org_id, category="compliance",
            title="1", message="m",
            severity=EscalationLevel.WARNING,
        )
        assert first is not None

        # Immediately — should be throttled
        second = service.create_incident(
            organization_id=org_id, category="compliance",
            title="2", message="m",
            severity=EscalationLevel.WARNING,
        )
        assert second is None

    def test_different_categories_not_throttled(self, service, org_id):
        service.ensure_default_policies(org_id)

        service.create_incident(
            organization_id=org_id, category="compliance",
            title="1", message="m",
            severity=EscalationLevel.WARNING,
        )
        # Different category should work
        second = service.create_incident(
            organization_id=org_id, category="esignature",
            title="2", message="m",
            severity=EscalationLevel.WARNING,
        )
        assert second is not None

    def test_cooldown_respects_config(self, service, org_id):
        service.create_policy(EscalationPolicy(
            id="p-fast", organization_id=org_id, name="Fast",
            category="compliance", cooldown_seconds=0,
            tiers=[EscalationTier(level=1, delay_seconds=0, channels=["slack"])],
        ))
        service.create_incident(
            organization_id=org_id, category="compliance",
            title="1", message="m", severity=EscalationLevel.WARNING,
        )
        # Cooldown is 0 — should not be throttled
        second = service.create_incident(
            organization_id=org_id, category="compliance",
            title="2", message="m", severity=EscalationLevel.WARNING,
        )
        assert second is not None

    def test_disabled_policy_not_triggered(self, service, org_id):
        service.create_policy(EscalationPolicy(
            id="p-off", organization_id=org_id, name="Off",
            category="compliance", enabled=False,
            tiers=[EscalationTier(level=1, delay_seconds=0, channels=["slack"])],
        ))
        incident = service.create_incident(
            organization_id=org_id, category="compliance",
            title="T", message="M",
            severity=EscalationLevel.WARNING,
        )
        assert incident is None


# ── Stats ────────────────────────────────────────────────────────────────────


class TestIncidentStats:
    def test_stats(self, service, org_id):
        service.ensure_default_policies(org_id)
        for cat in ["compliance", "esignature", "migration"]:
            service.create_incident(
                organization_id=org_id, category=cat,
                title="T", message="M",
                severity=EscalationLevel.WARNING,
            )

        stats = service.get_incident_stats(org_id)
        assert stats["total"] == 3
        assert stats["last_24h"] == 3
        assert stats["by_category"]["compliance"] == 1
        assert stats["by_category"]["esignature"] == 1
        assert stats["by_category"]["migration"] == 1
        assert stats["active"] == 3


# ── Severity filtering ───────────────────────────────────────────────────────


class TestSeverityFiltering:
    def test_info_not_matched_by_warning_policy(self, service, org_id):
        service.create_policy(EscalationPolicy(
            id="p-warn", organization_id=org_id, name="Warn",
            category="compliance", min_severity=EscalationLevel.WARNING,
            tiers=[EscalationTier(level=1, delay_seconds=0, channels=["slack"])],
        ))
        incident = service.create_incident(
            organization_id=org_id, category="compliance",
            title="T", message="M",
            severity=EscalationLevel.INFO,
        )
        assert incident is None

    def test_critical_matched_by_warning_policy(self, service, org_id):
        service.create_policy(EscalationPolicy(
            id="p-warn", organization_id=org_id, name="Warn",
            category="compliance", min_severity=EscalationLevel.WARNING,
            tiers=[EscalationTier(level=1, delay_seconds=0, channels=["slack"])],
        ))
        incident = service.create_incident(
            organization_id=org_id, category="compliance",
            title="T", message="M",
            severity=EscalationLevel.CRITICAL,
        )
        assert incident is not None
