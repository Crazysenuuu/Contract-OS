"""
Alerting Service — Slack & PagerDuty Integration.

Sends alert notifications via:
  - Slack Incoming Webhooks (rich Block Kit messages)
  - PagerDuty Events API v2 (severity-based incident routing)

Falls back to structured logging when no providers are configured.

Environment variables:
    SLACK_WEBHOOK_URL          — Slack incoming webhook URL
    SLACK_CHANNEL              — Override channel (optional)
    PAGERDUTY_ROUTING_KEY      — PagerDuty Events API v2 integration key
    PAGERDUTY_FROM_EMAIL       — Caller email for PagerDuty events
    ALERTING_ENABLED           — Master kill-switch (default: true)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)


# ── Severity ────────────────────────────────────────────────────────────────

class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ── Alert payload ───────────────────────────────────────────────────────────

@dataclass
class Alert:
    """Unified alert payload sent to all configured providers."""

    title: str
    message: str
    severity: AlertSeverity = AlertSeverity.WARNING
    source: str = "contractos"
    category: str = "general"          # migration, compliance, api, etc.
    details: dict[str, Any] = field(default_factory=dict)
    runbook_url: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        d["timestamp"] = self.timestamp.isoformat()
        return d


# ── Slack provider ──────────────────────────────────────────────────────────

class SlackProvider:
    """Sends alerts via Slack Incoming Webhook (Block Kit)."""

    SEVERITY_COLORS = {
        AlertSeverity.INFO: "#36a64f",
        AlertSeverity.WARNING: "#f2994a",
        AlertSeverity.CRITICAL: "#eb5757",
    }

    SEVERITY_EMOJI = {
        AlertSeverity.INFO: "ℹ️",
        AlertSeverity.WARNING: "⚠️",
        AlertSeverity.CRITICAL: "🚨",
    }

    def __init__(
        self,
        webhook_url: str,
        channel: str | None = None,
    ):
        self.webhook_url = webhook_url
        self.channel = channel

    def send(self, alert: Alert) -> bool:
        """Send alert to Slack. Returns True on success."""
        color = self.SEVERITY_COLORS[alert.severity]
        emoji = self.SEVERITY_EMOJI[alert.severity]

        # Build Block Kit payload
        blocks: list[dict] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} {alert.title}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": alert.message,
                },
            },
        ]

        # Fields row (severity + category)
        fields = [
            {
                "type": "mrkdwn",
                "text": f"*Severity:*\n{alert.severity.value.upper()}",
            },
            {
                "type": "mrkdwn",
                "text": f"*Category:*\n{alert.category}",
            },
        ]
        blocks.append({"type": "section", "fields": fields})

        # Details section
        if alert.details:
            detail_lines = []
            for k, v in alert.details.items():
                detail_lines.append(f"*{k}:* `{v}`")
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(detail_lines),
                },
            })

        # Divider
        blocks.append({"type": "divider"})

        # Footer
        footer_parts = [f"Source: {alert.source}"]
        if alert.runbook_url:
            footer_parts.append(f"<{alert.runbook_url}|Runbook>")
        footer_parts.append(
            f"Timestamp: {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": " | ".join(footer_parts),
                }
            ],
        })

        payload: dict[str, Any] = {
            "attachments": [
                {
                    "color": color,
                    "blocks": blocks,
                }
            ],
        }

        if self.channel:
            payload["channel"] = self.channel

        return self._post(payload)

    def _post(self, payload: dict) -> bool:
        """POST JSON to the webhook URL."""
        try:
            data = json.dumps(payload).encode("utf-8")
            req = Request(
                self.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                ok = resp.status == 200
                if ok:
                    logger.info("Slack alert sent: %s", payload.get("text", ""))
                else:
                    logger.warning("Slack webhook returned %s", resp.status)
                return ok
        except URLError as exc:
            logger.error("Slack webhook failed: %s", exc)
            return False
        except Exception as exc:
            logger.error("Slack send error: %s", exc)
            return False


# ── PagerDuty provider ──────────────────────────────────────────────────────

class PagerDutyProvider:
    """Sends incidents via PagerDuty Events API v2."""

    EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"

    SEVERITY_MAP = {
        AlertSeverity.INFO: "info",
        AlertSeverity.WARNING: "warning",
        AlertSeverity.CRITICAL: "critical",
    }

    def __init__(
        self,
        routing_key: str,
        from_email: str = "contractos@alerts.local",
    ):
        self.routing_key = routing_key
        self.from_email = from_email

    def send(self, alert: Alert) -> bool:
        """Send incident to PagerDuty. Returns True on success."""
        dedup_key = (
            f"{alert.source}-{alert.category}-"
            f"{alert.title.lower().replace(' ', '-')}"
        )

        payload = {
            "routing_key": self.routing_key,
            "event_action": "trigger",
            "dedup_key": dedup_key,
            "payload": {
                "summary": f"[{alert.severity.value.upper()}] {alert.title}: {alert.message}",
                "source": alert.source,
                "severity": self.SEVERITY_MAP[alert.severity],
                "component": alert.category,
                "group": alert.category,
                "class": alert.severity.value,
                "custom_details": {
                    **alert.details,
                    "runbook_url": alert.runbook_url,
                    "timestamp": alert.timestamp.isoformat(),
                },
            },
            "links": [],
            "images": [],
        }

        if alert.runbook_url:
            payload["links"].append({
                "href": alert.runbook_url,
                "text": "Runbook",
            })

        return self._post(payload)

    def acknowledge(self, dedup_key: str) -> bool:
        """Acknowledge an incident."""
        return self._post({
            "routing_key": self.routing_key,
            "event_action": "acknowledge",
            "dedup_key": dedup_key,
        })

    def resolve(self, dedup_key: str) -> bool:
        """Resolve an incident."""
        return self._post({
            "routing_key": self.routing_key,
            "event_action": "resolve",
            "dedup_key": dedup_key,
        })

    def _post(self, payload: dict) -> bool:
        """POST JSON to PagerDuty Events API."""
        try:
            data = json.dumps(payload).encode("utf-8")
            req = Request(
                self.EVENTS_URL,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read())
                ok = resp.status in (200, 202)
                if ok:
                    logger.info(
                        "PagerDuty event sent: dedup=%s status=%s",
                        payload.get("dedup_key"),
                        body.get("status"),
                    )
                else:
                    logger.warning("PagerDuty returned %s: %s", resp.status, body)
                return ok
        except URLError as exc:
            logger.error("PagerDuty request failed: %s", exc)
            return False
        except Exception as exc:
            logger.error("PagerDuty send error: %s", exc)
            return False


# ── Composite alerting service ──────────────────────────────────────────────

@dataclass
class AlertDeliveryResult:
    """Result of dispatching an alert to all providers."""

    total_providers: int
    successful: int
    failed: int
    provider_results: dict[str, bool] = field(default_factory=dict)


class AlertingService:
    """
    Multi-provider alerting facade.

    Configures itself from environment variables and dispatches alerts
    to every enabled provider.  Keeps an in-memory log of recent alerts
    for the monitoring API.
    """

    def __init__(self):
        self._providers: dict[str, Any] = {}
        self._alert_log: list[dict] = []
        self._max_log_size = 500
        self._init_providers()

    # ── Provider setup ──────────────────────────────────────────────────

    def _init_providers(self):
        """Initialize providers from environment variables."""
        # Slack
        slack_url = os.environ.get("SLACK_WEBHOOK_URL")
        if slack_url:
            slack_channel = os.environ.get("SLACK_CHANNEL")
            self._providers["slack"] = SlackProvider(
                webhook_url=slack_url,
                channel=slack_channel,
            )
            logger.info("Slack alerting enabled")

        # PagerDuty
        pd_key = os.environ.get("PAGERDUTY_ROUTING_KEY")
        if pd_key:
            pd_from = os.environ.get("PAGERDUTY_FROM_EMAIL", "contractos@alerts.local")
            self._providers["pagerduty"] = PagerDutyProvider(
                routing_key=pd_key,
                from_email=pd_from,
            )
            logger.info("PagerDuty alerting enabled")

        if not self._providers:
            logger.info(
                "No alert providers configured. "
                "Set SLACK_WEBHOOK_URL and/or PAGERDUTY_ROUTING_KEY."
            )

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return os.environ.get("ALERTING_ENABLED", "true").lower() == "true"

    @property
    def providers(self) -> list[str]:
        return list(self._providers.keys())

    def send_alert(self, alert: Alert) -> AlertDeliveryResult:
        """
        Dispatch an alert to all configured providers.

        Returns a delivery result with per-provider status.
        """
        if not self.enabled:
            logger.info("Alerting disabled — skipping: %s", alert.title)
            return AlertDeliveryResult(0, 0, 0)

        if not self._providers:
            logger.info(
                "[ALERT LOG] %s | %s | %s\n  %s",
                alert.severity.value.upper(),
                alert.category,
                alert.title,
                alert.message,
            )
            self._log_alert(alert, {"log": True})
            return AlertDeliveryResult(0, 0, 0)

        results: dict[str, bool] = {}
        for name, provider in self._providers.items():
            try:
                results[name] = provider.send(alert)
            except Exception as exc:
                logger.error("Provider %s failed: %s", name, exc)
                results[name] = False

        successful = sum(1 for v in results.values() if v)
        failed = len(results) - successful

        self._log_alert(alert, results)

        return AlertDeliveryResult(
            total_providers=len(results),
            successful=successful,
            failed=failed,
            provider_results=results,
        )

    def send_migration_alert(
        self,
        title: str,
        message: str,
        severity: AlertSeverity = AlertSeverity.WARNING,
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> AlertDeliveryResult:
        """Convenience: send a migration-specific alert."""
        alert = Alert(
            title=title,
            message=message,
            severity=severity,
            source="contractos-migration",
            category="migration",
            details=details or {},
            runbook_url=runbook_url,
        )
        return self.send_alert(alert)

    def resolve_incident(self, category: str, title: str) -> None:
        """Resolve a PagerDuty incident (no-op for Slack)."""
        pd = self._providers.get("pagerduty")
        if pd:
            dedup = f"contractos-{category}-{title.lower().replace(' ', '-')}"
            pd.resolve(dedup)

    # ── Alert log ───────────────────────────────────────────────────────

    def _log_alert(self, alert: Alert, provider_results: dict[str, bool]):
        entry = {
            **alert.to_dict(),
            "provider_results": provider_results,
            "logged_at": datetime.now(timezone.utc).isoformat(),
        }
        self._alert_log.append(entry)
        if len(self._alert_log) > self._max_log_size:
            self._alert_log = self._alert_log[-self._max_log_size:]

    def get_alert_history(
        self,
        limit: int = 50,
        severity: AlertSeverity | None = None,
        category: str | None = None,
    ) -> list[dict]:
        """Retrieve recent alert history."""
        filtered = self._alert_log

        if severity:
            filtered = [a for a in filtered if a.get("severity") == severity.value]
        if category:
            filtered = [a for a in filtered if a.get("category") == category]

        return list(reversed(filtered[-limit:]))

    def get_alert_stats(self) -> dict[str, Any]:
        """Aggregate alert statistics."""
        now = datetime.now(timezone.utc)
        last_24h = [a for a in self._alert_log
                     if (now - datetime.fromisoformat(a["timestamp"])).total_seconds() < 86400]

        by_severity: dict[str, int] = {}
        for a in last_24h:
            sev = a.get("severity", "unknown")
            by_severity[sev] = by_severity.get(sev, 0) + 1

        return {
            "total_alerts": len(self._alert_log),
            "last_24h": len(last_24h),
            "by_severity": by_severity,
            "providers": self.providers,
            "enabled": self.enabled,
        }


# ── Global singleton ────────────────────────────────────────────────────────

_alerting_service: AlertingService | None = None


def get_alerting_service() -> AlertingService:
    global _alerting_service
    if _alerting_service is None:
        _alerting_service = AlertingService()
    return _alerting_service
