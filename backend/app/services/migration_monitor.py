"""
Migration Monitoring Service.

Tracks migration events, detects failures, and provides alerting.
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, asdict

from app.services.alerting_service import (
    AlertSeverity,
    get_alerting_service,
)

logger = logging.getLogger(__name__)


@dataclass
class MigrationEvent:
    """Represents a migration event."""
    event_id: str
    event_type: str  # upgrade, downgrade, rollback, failure
    migration_id: str
    migration_name: str
    database_name: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    status: str = "pending"  # pending, running, success, failed
    error_message: Optional[str] = None
    tables_affected: int = 0
    rows_affected: int = 0
    metadata: Optional[dict] = None


class MigrationMonitor:
    """Monitors migration operations and tracks metrics."""

    def __init__(self):
        self.events: list[MigrationEvent] = []
        self.metrics = {
            "total_migrations": 0,
            "successful_migrations": 0,
            "failed_migrations": 0,
            "rollbacks": 0,
            "avg_duration_seconds": 0.0,
            "last_migration_time": None,
            "last_failure_time": None,
        }
        self._failure_threshold = 3  # Alert after N consecutive failures
        self._consecutive_failures = 0

    def start_migration(
        self,
        event_type: str,
        migration_id: str,
        migration_name: str,
        database_name: str,
    ) -> MigrationEvent:
        """Record the start of a migration."""
        event = MigrationEvent(
            event_id=f"mig-{int(time.time() * 1000)}",
            event_type=event_type,
            migration_id=migration_id,
            migration_name=migration_name,
            database_name=database_name,
            started_at=datetime.utcnow(),
            status="running",
        )
        self.events.append(event)
        self.metrics["total_migrations"] += 1
        logger.info(
            f"Migration started: {event_type} {migration_name} "
            f"on {database_name}"
        )
        return event

    def complete_migration(
        self,
        event: MigrationEvent,
        success: bool,
        error_message: Optional[str] = None,
        tables_affected: int = 0,
        rows_affected: int = 0,
    ):
        """Record the completion of a migration."""
        event.completed_at = datetime.utcnow()
        event.duration_seconds = (
            event.completed_at - event.started_at
        ).total_seconds()
        event.status = "success" if success else "failed"
        event.error_message = error_message
        event.tables_affected = tables_affected
        event.rows_affected = rows_affected

        if success:
            self.metrics["successful_migrations"] += 1
            self._consecutive_failures = 0
            logger.info(
                f"Migration completed successfully: {event.migration_name} "
                f"in {event.duration_seconds:.2f}s"
            )

            # On success, resolve any open PagerDuty incidents
            if self.metrics["failed_migrations"] > 0:
                try:
                    from app.services.alerting_service import get_alerting_service
                    get_alerting_service().resolve_incident(
                        "migration", "Migration Alert",
                    )
                except Exception:
                    pass
        else:
            self.metrics["failed_migrations"] += 1
            self._consecutive_failures += 1
            self.metrics["last_failure_time"] = datetime.utcnow()
            logger.error(
                f"Migration failed: {event.migration_name} - {error_message}"
            )

            # Check if we need to alert
            if self._consecutive_failures >= self._failure_threshold:
                self._trigger_alert(
                    f"CRITICAL: {self._consecutive_failures} consecutive "
                    f"migration failures!",
                    severity="critical",
                )
            else:
                self._trigger_alert(
                    f"Migration failed: {event.migration_name} — {error_message}",
                    severity="warning",
                )

        self.metrics["last_migration_time"] = datetime.utcnow()
        self._update_avg_duration()

    def record_rollback(self, event: MigrationEvent):
        """Record a rollback event."""
        self.metrics["rollbacks"] += 1
        logger.warning(
            f"Migration rollback: {event.migration_name}"
        )

    def _update_avg_duration(self):
        """Update average migration duration."""
        durations = [
            e.duration_seconds
            for e in self.events
            if e.duration_seconds is not None
        ]
        if durations:
            self.metrics["avg_duration_seconds"] = sum(durations) / len(
                durations
            )

    def _trigger_alert(self, message: str, severity: str = "critical"):
        """Trigger an alert via all configured providers (Slack, PagerDuty)."""
        logger.critical(f"ALERT: {message}")
        print(f"🚨 ALERT: {message}")

        alerting = get_alerting_service()
        alert_severity = (
            AlertSeverity.CRITICAL if severity == "critical"
            else AlertSeverity.WARNING if severity == "warning"
            else AlertSeverity.INFO
        )

        result = alerting.send_migration_alert(
            title="Migration Alert",
            message=message,
            severity=alert_severity,
            details={
                "total_migrations": self.metrics["total_migrations"],
                "failed_migrations": self.metrics["failed_migrations"],
                "consecutive_failures": self._consecutive_failures,
            },
            runbook_url="https://docs.contractos.dev/runbooks/migration-failure",
        )
        logger.info(
            "Alert dispatched to %d/%d providers",
            result.successful, result.total_providers,
        )

    def get_health_status(self) -> dict:
        """Get current migration health status."""
        recent_failures = [
            e
            for e in self.events
            if e.status == "failed"
            and e.started_at > datetime.utcnow() - timedelta(hours=24)
        ]

        return {
            "status": "healthy" if not recent_failures else "degraded",
            "metrics": self.metrics,
            "recent_failures": len(recent_failures),
            "consecutive_failures": self._consecutive_failures,
            "last_24h_events": len(
                [
                    e
                    for e in self.events
                    if e.started_at > datetime.utcnow() - timedelta(hours=24)
                ]
            ),
        }

    def get_event_history(
        self,
        limit: int = 50,
        event_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        """Get migration event history."""
        filtered = self.events

        if event_type:
            filtered = [e for e in filtered if e.event_type == event_type]
        if status:
            filtered = [e for e in filtered if e.status == status]

        # Sort by most recent first
        filtered.sort(key=lambda e: e.started_at, reverse=True)

        return [asdict(e) for e in filtered[:limit]]

    def get_slow_migrations(
        self, threshold_seconds: float = 60.0
    ) -> list[dict]:
        """Get migrations that took longer than threshold."""
        slow = [
            e
            for e in self.events
            if e.duration_seconds is not None
            and e.duration_seconds > threshold_seconds
        ]
        slow.sort(key=lambda e: e.duration_seconds or 0, reverse=True)
        return [asdict(e) for e in slow]

    def export_metrics(self) -> dict:
        """Export metrics for monitoring systems (Prometheus, etc.)."""
        return {
            "contractos_migrations_total": self.metrics["total_migrations"],
            "contractos_migrations_successful": self.metrics[
                "successful_migrations"
            ],
            "contractos_migrations_failed": self.metrics[
                "failed_migrations"
            ],
            "contractos_migrations_rollbacks": self.metrics["rollbacks"],
            "contractos_migration_avg_duration": self.metrics[
                "avg_duration_seconds"
            ],
        }


# Global singleton
_migration_monitor: Optional[MigrationMonitor] = None


def get_migration_monitor() -> MigrationMonitor:
    """Get the global migration monitor instance."""
    global _migration_monitor
    if _migration_monitor is None:
        _migration_monitor = MigrationMonitor()
    return _migration_monitor
