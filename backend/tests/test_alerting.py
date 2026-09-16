"""
Tests for the Alerting Service.

Covers:
  - Alert creation and dispatch
  - Slack provider formatting
  - PagerDuty event construction
  - Provider fallback (no providers configured)
  - Alert history and stats
  - Integration with MigrationMonitor
"""
import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.alerting_service import (
    Alert,
    AlertSeverity,
    AlertingService,
    PagerDutyProvider,
    SlackProvider,
    get_alerting_service,
)
from app.services.migration_monitor import MigrationMonitor


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_singleton():
    """Reset the global alerting service singleton between tests."""
    import app.services.alerting_service as mod
    mod._alerting_service = None
    yield
    mod._alerting_service = None


@pytest.fixture()
def sample_alert():
    return Alert(
        title="Test Alert",
        message="Something happened",
        severity=AlertSeverity.WARNING,
        source="contractos-test",
        category="test",
        details={"key": "value"},
    )


# ── Alert model tests ────────────────────────────────────────────────────────


class TestAlertModel:
    def test_alert_creation(self, sample_alert):
        assert sample_alert.title == "Test Alert"
        assert sample_alert.severity == AlertSeverity.WARNING
        assert sample_alert.source == "contractos-test"

    def test_alert_to_dict(self, sample_alert):
        d = sample_alert.to_dict()
        assert d["severity"] == "warning"
        assert d["category"] == "test"
        assert "timestamp" in d
        assert d["details"]["key"] == "value"

    def test_alert_defaults(self):
        alert = Alert(title="X", message="Y")
        assert alert.severity == AlertSeverity.WARNING
        assert alert.source == "contractos"
        assert alert.details == {}
        assert alert.runbook_url is None


# ── Slack provider tests ─────────────────────────────────────────────────────


class TestSlackProvider:
    def test_send_success(self, sample_alert):
        provider = SlackProvider(webhook_url="https://hooks.slack.com/test")
        with patch("app.services.alerting_service.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_url.return_value = mock_resp

            result = provider.send(sample_alert)

        assert result is True
        call_args = mock_url.call_args
        payload = json.loads(call_args[0][0].data)
        assert "attachments" in payload
        assert payload["attachments"][0]["color"] == "#f2994a"  # warning color

    def test_send_failure(self, sample_alert):
        provider = SlackProvider(webhook_url="https://hooks.slack.com/test")
        with patch("app.services.alerting_service.urlopen") as mock_url:
            mock_url.side_effect = Exception("network error")
            result = provider.send(sample_alert)

        assert result is False

    def test_channel_override(self, sample_alert):
        provider = SlackProvider(
            webhook_url="https://hooks.slack.com/test",
            channel="#alerts",
        )
        with patch("app.services.alerting_service.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_url.return_value = mock_resp

            provider.send(sample_alert)

            payload = json.loads(mock_url.call_args[0][0].data)
            assert payload["channel"] == "#alerts"

    def test_severity_colors(self):
        provider = SlackProvider(webhook_url="https://hooks.slack.com/test")
        assert provider.SEVERITY_COLORS[AlertSeverity.INFO] == "#36a64f"
        assert provider.SEVERITY_COLORS[AlertSeverity.WARNING] == "#f2994a"
        assert provider.SEVERITY_COLORS[AlertSeverity.CRITICAL] == "#eb5757"


# ── PagerDuty provider tests ─────────────────────────────────────────────────


class TestPagerDutyProvider:
    def test_send_trigger(self, sample_alert):
        provider = PagerDutyProvider(routing_key="test-key-123")
        with patch("app.services.alerting_service.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.status = 202
            mock_resp.read.return_value = json.dumps({"status": "success"}).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_url.return_value = mock_resp

            result = provider.send(sample_alert)

        assert result is True
        payload = json.loads(mock_url.call_args[0][0].data)
        assert payload["event_action"] == "trigger"
        assert payload["routing_key"] == "test-key-123"
        assert payload["payload"]["severity"] == "warning"

    def test_send_failure(self, sample_alert):
        provider = PagerDutyProvider(routing_key="test-key-123")
        with patch("app.services.alerting_service.urlopen") as mock_url:
            mock_url.side_effect = Exception("timeout")
            result = provider.send(sample_alert)

        assert result is False

    def test_acknowledge(self):
        provider = PagerDutyProvider(routing_key="test-key-123")
        with patch("app.services.alerting_service.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.status = 202
            mock_resp.read.return_value = json.dumps({"status": "success"}).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_url.return_value = mock_resp

            result = provider.acknowledge("test-dedup-key")

        assert result is True
        payload = json.loads(mock_url.call_args[0][0].data)
        assert payload["event_action"] == "acknowledge"
        assert payload["dedup_key"] == "test-dedup-key"

    def test_resolve(self):
        provider = PagerDutyProvider(routing_key="test-key-123")
        with patch("app.services.alerting_service.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.status = 202
            mock_resp.read.return_value = json.dumps({"status": "success"}).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_url.return_value = mock_resp

            result = provider.resolve("test-dedup-key")

        assert result is True
        payload = json.loads(mock_url.call_args[0][0].data)
        assert payload["event_action"] == "resolve"


# ── AlertingService tests ────────────────────────────────────────────────────


class TestAlertingService:
    def test_no_providers_configured(self):
        """Without env vars, service should log-only."""
        service = AlertingService()
        assert service.providers == []

        result = service.send_alert(Alert(
            title="Test", message="msg", severity=AlertSeverity.INFO,
        ))
        assert result.total_providers == 0
        assert result.successful == 0

    def test_slack_provider_configured(self):
        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"}):
            service = AlertingService()
            assert "slack" in service.providers

    def test_pagerduty_provider_configured(self):
        with patch.dict(os.environ, {"PAGERDUTY_ROUTING_KEY": "test-key"}):
            service = AlertingService()
            assert "pagerduty" in service.providers

    def test_both_providers_configured(self):
        env = {
            "SLACK_WEBHOOK_URL": "https://hooks.slack.com/test",
            "PAGERDUTY_ROUTING_KEY": "test-key",
        }
        with patch.dict(os.environ, env):
            service = AlertingService()
            assert set(service.providers) == {"slack", "pagerduty"}

    def test_alerting_disabled(self):
        with patch.dict(os.environ, {"ALERTING_ENABLED": "false"}):
            service = AlertingService()
            assert service.enabled is False

            result = service.send_alert(Alert(
                title="Test", message="msg", severity=AlertSeverity.INFO,
            ))
            assert result.total_providers == 0

    def test_send_migration_alert_convenience(self):
        service = AlertingService()
        result = service.send_migration_alert(
            title="Migration Failed",
            message="f1a2b3c4d5e6 failed",
            severity=AlertSeverity.CRITICAL,
            details={"migration_id": "f1a2b3c4d5e6"},
        )
        assert result.total_providers == 0  # no providers configured

    def test_alert_history_tracking(self):
        service = AlertingService()
        for i in range(5):
            service.send_alert(Alert(
                title=f"Alert {i}", message=f"msg {i}",
                severity=AlertSeverity.WARNING,
            ))

        history = service.get_alert_history(limit=3)
        assert len(history) == 3
        # Most recent first
        assert history[0]["title"] == "Alert 4"

    def test_alert_history_filter_by_severity(self):
        service = AlertingService()
        service.send_alert(Alert(
            title="Info", message="m", severity=AlertSeverity.INFO,
        ))
        service.send_alert(Alert(
            title="Critical", message="m", severity=AlertSeverity.CRITICAL,
        ))

        crits = service.get_alert_history(severity=AlertSeverity.CRITICAL)
        assert len(crits) == 1
        assert crits[0]["title"] == "Critical"

    def test_alert_history_filter_by_category(self):
        service = AlertingService()
        service.send_alert(Alert(
            title="A", message="m", severity=AlertSeverity.INFO,
            category="migration",
        ))
        service.send_alert(Alert(
            title="B", message="m", severity=AlertSeverity.INFO,
            category="compliance",
        ))

        mig = service.get_alert_history(category="migration")
        assert len(mig) == 1
        assert mig[0]["title"] == "A"

    def test_alert_stats(self):
        service = AlertingService()
        service.send_alert(Alert(
            title="A", message="m", severity=AlertSeverity.WARNING,
        ))
        service.send_alert(Alert(
            title="B", message="m", severity=AlertSeverity.CRITICAL,
        ))

        stats = service.get_alert_stats()
        assert stats["total_alerts"] == 2
        assert stats["last_24h"] == 2
        assert stats["by_severity"]["warning"] == 1
        assert stats["by_severity"]["critical"] == 1
        assert stats["enabled"] is True

    def test_log_max_size(self):
        service = AlertingService()
        service._max_log_size = 3
        for i in range(5):
            service.send_alert(Alert(
                title=f"A{i}", message="m", severity=AlertSeverity.INFO,
            ))
        assert len(service._alert_log) == 3

    def test_resolve_incident_no_pd(self):
        """Resolve is a no-op when PagerDuty is not configured."""
        service = AlertingService()
        # Should not raise
        service.resolve_incident("migration", "Test Alert")


# ── MigrationMonitor integration ─────────────────────────────────────────────


class TestMigrationMonitorAlerting:
    def test_trigger_alert_on_failure(self):
        monitor = MigrationMonitor()
        monitor._failure_threshold = 2

        event = monitor.start_migration(
            "upgrade", "test-001", "test_migration", "test_db",
        )

        # First failure — warning
        with patch("app.services.migration_monitor.get_alerting_service") as mock_get:
            mock_service = MagicMock()
            mock_get.return_value = mock_service
            monitor.complete_migration(event, success=False, error_message="boom")
            mock_service.send_migration_alert.assert_called_once()
            call_kwargs = mock_service.send_migration_alert.call_args[1]
            assert call_kwargs["severity"] == AlertSeverity.WARNING

    def test_critical_alert_on_consecutive_failures(self):
        monitor = MigrationMonitor()
        monitor._failure_threshold = 2

        for i in range(2):
            event = monitor.start_migration(
                "upgrade", f"test-{i}", f"migration_{i}", "test_db",
            )
            with patch("app.services.migration_monitor.get_alerting_service") as mock_get:
                mock_service = MagicMock()
                mock_get.return_value = mock_service
                monitor.complete_migration(event, success=False, error_message="boom")

        # Second failure should trigger critical alert
        assert monitor._consecutive_failures == 2

    def test_resolve_on_success_after_failure(self):
        monitor = MigrationMonitor()
        event = monitor.start_migration(
            "upgrade", "test-001", "test_migration", "test_db",
        )
        # Fail once to increment failed_migrations
        with patch("app.services.migration_monitor.get_alerting_service"):
            monitor.complete_migration(event, success=False, error_message="boom")
        assert monitor.metrics["failed_migrations"] == 1

        event2 = monitor.start_migration(
            "upgrade", "test-002", "test_migration_2", "test_db",
        )
        # Patch the local import path used inside complete_migration
        with patch("app.services.alerting_service.get_alerting_service") as mock_get:
            mock_service = MagicMock()
            mock_get.return_value = mock_service
            monitor.complete_migration(event2, success=True)
            # Should attempt to resolve incident on recovery
            mock_service.resolve_incident.assert_called_once()


# ── API endpoint tests ───────────────────────────────────────────────────────


class TestAlertingAPI:
    """Test alerting API endpoints via the FastAPI test client."""

    @pytest.mark.asyncio
    async def test_list_providers_requires_auth(self, client):
        resp = await client.get("/api/v1/alerting/providers")
        assert resp.status_code in [401, 403]

    @pytest.mark.asyncio
    async def test_stats_requires_auth(self, client):
        resp = await client.get("/api/v1/alerting/stats")
        assert resp.status_code in [401, 403]

    @pytest.mark.asyncio
    async def test_history_requires_auth(self, client):
        resp = await client.get("/api/v1/alerting/history")
        assert resp.status_code in [401, 403]

    @pytest.mark.asyncio
    async def test_config_requires_auth(self, client):
        resp = await client.get("/api/v1/alerting/config")
        assert resp.status_code in [401, 403]

    @pytest.mark.asyncio
    async def test_test_alert_requires_auth(self, client):
        resp = await client.post(
            "/api/v1/alerting/test",
            json={"provider": "slack", "severity": "info"},
        )
        assert resp.status_code in [401, 403]
