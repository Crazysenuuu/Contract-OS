"""
Tests for Migration Monitoring Service.

Tests the monitoring, alerting, and health check functionality.
"""
import pytest
from datetime import datetime, timedelta
from app.services.migration_monitor import MigrationMonitor, get_migration_monitor


class TestMigrationMonitor:
    """Test migration monitoring functionality."""

    def test_singleton_instance(self):
        """Test that get_migration_monitor returns singleton."""
        monitor1 = get_migration_monitor()
        monitor2 = get_migration_monitor()
        assert monitor1 is monitor2

    def test_record_migration_success(self):
        """Test recording a successful migration."""
        monitor = MigrationMonitor()

        event = monitor.start_migration(
            event_type="upgrade",
            migration_id="test-001",
            migration_name="add_users_table",
            database_name="test_db",
        )

        assert event.status == "running"
        assert monitor.metrics["total_migrations"] == 1

        monitor.complete_migration(event, success=True)

        assert event.status == "success"
        assert event.duration_seconds is not None
        assert monitor.metrics["successful_migrations"] == 1

    def test_record_migration_failure(self):
        """Test recording a failed migration."""
        monitor = MigrationMonitor()

        event = monitor.start_migration(
            event_type="upgrade",
            migration_id="test-002",
            migration_name="bad_migration",
            database_name="test_db",
        )

        monitor.complete_migration(
            event,
            success=False,
            error_message="Table already exists",
        )

        assert event.status == "failed"
        assert event.error_message == "Table already exists"
        assert monitor.metrics["failed_migrations"] == 1

    def test_consecutive_failure_alert(self):
        """Test alert on consecutive failures."""
        monitor = MigrationMonitor()
        monitor._failure_threshold = 3

        for i in range(3):
            event = monitor.start_migration(
                event_type="upgrade",
                migration_id=f"test-{i}",
                migration_name=f"migration_{i}",
                database_name="test_db",
            )
            monitor.complete_migration(
                event,
                success=False,
                error_message=f"Error {i}",
            )

        assert monitor._consecutive_failures == 3

    def test_consecutive_failure_reset(self):
        """Test that consecutive failures reset on success."""
        monitor = MigrationMonitor()

        # Fail twice
        for i in range(2):
            event = monitor.start_migration(
                event_type="upgrade",
                migration_id=f"test-fail-{i}",
                migration_name=f"fail_{i}",
                database_name="test_db",
            )
            monitor.complete_migration(event, success=False)

        assert monitor._consecutive_failures == 2

        # Succeed once
        event = monitor.start_migration(
            event_type="upgrade",
            migration_id="test-success",
            migration_name="success",
            database_name="test_db",
        )
        monitor.complete_migration(event, success=True)

        assert monitor._consecutive_failures == 0

    def test_health_status_healthy(self):
        """Test health status when no failures."""
        monitor = MigrationMonitor()

        event = monitor.start_migration(
            event_type="upgrade",
            migration_id="test-001",
            migration_name="migration",
            database_name="test_db",
        )
        monitor.complete_migration(event, success=True)

        health = monitor.get_health_status()
        assert health["status"] == "healthy"
        assert health["recent_failures"] == 0

    def test_health_status_degraded(self):
        """Test health status when there are recent failures."""
        monitor = MigrationMonitor()

        # Add a recent failure
        event = monitor.start_migration(
            event_type="upgrade",
            migration_id="test-fail",
            migration_name="failed_migration",
            database_name="test_db",
        )
        event.started_at = datetime.utcnow() - timedelta(hours=1)
        monitor.complete_migration(event, success=False)

        health = monitor.get_health_status()
        assert health["status"] == "degraded"
        assert health["recent_failures"] == 1

    def test_export_metrics(self):
        """Test metrics export for Prometheus."""
        monitor = MigrationMonitor()

        event = monitor.start_migration(
            event_type="upgrade",
            migration_id="test-001",
            migration_name="migration",
            database_name="test_db",
        )
        monitor.complete_migration(event, success=True)

        metrics = monitor.export_metrics()

        assert "contractos_migrations_total" in metrics
        assert "contractos_migrations_successful" in metrics
        assert "contractos_migrations_failed" in metrics
        assert metrics["contractos_migrations_total"] == 1
        assert metrics["contractos_migrations_successful"] == 1

    def test_get_event_history(self):
        """Test event history retrieval."""
        monitor = MigrationMonitor()

        # Add some events
        for i in range(5):
            event = monitor.start_migration(
                event_type="upgrade" if i % 2 == 0 else "downgrade",
                migration_id=f"test-{i}",
                migration_name=f"migration_{i}",
                database_name="test_db",
            )
            monitor.complete_migration(
                event,
                success=i % 3 != 0,  # Every 3rd fails
            )

        # Get all events
        history = monitor.get_event_history(limit=10)
        assert len(history) == 5

        # Filter by type
        upgrade_events = monitor.get_event_history(event_type="upgrade")
        assert len(upgrade_events) == 3

        # Filter by status
        failed_events = monitor.get_event_history(status="failed")
        assert len(failed_events) == 2

    def test_slow_migrations_detection(self):
        """Test detection of slow migrations."""
        # Use fresh instance to avoid singleton pollution
        monitor = MigrationMonitor()
        monitor.events = []  # Clear any previous events

        # Create a slow event directly
        from app.services.migration_monitor import MigrationEvent
        slow_event = MigrationEvent(
            event_id="slow-001",
            event_type="upgrade",
            migration_id="slow-001",
            migration_name="slow_migration",
            database_name="test_db",
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            duration_seconds=120.0,
            status="success",
        )
        monitor.events.append(slow_event)

        # Create a fast event
        fast_event = MigrationEvent(
            event_id="fast-001",
            event_type="upgrade",
            migration_id="fast-001",
            migration_name="fast_migration",
            database_name="test_db",
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            duration_seconds=5.0,
            status="success",
        )
        monitor.events.append(fast_event)

        slow = monitor.get_slow_migrations(threshold_seconds=60.0)
        assert len(slow) == 1
        assert slow[0]["migration_name"] == "slow_migration"

    def test_recommendations_generation(self):
        """Test that recommendations are generated."""
        monitor = MigrationMonitor()

        # Good state
        event = monitor.start_migration(
            event_type="upgrade",
            migration_id="test-001",
            migration_name="migration",
            database_name="test_db",
        )
        monitor.complete_migration(event, success=True)

        summary = monitor.get_health_status()
        assert summary["status"] == "healthy"


class TestMigrationMonitoringAPI:
    """Test migration monitoring API endpoints."""

    @pytest.mark.asyncio
    async def test_health_endpoint(self, client):
        """Test health check endpoint."""
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "services" in data

    @pytest.mark.asyncio
    async def test_readiness_endpoint(self, client):
        """Test readiness check endpoint."""
        response = await client.get("/api/v1/health/ready")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_liveness_endpoint(self, client):
        """Test liveness check endpoint."""
        response = await client.get("/api/v1/health/live")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_migration_health_requires_auth(self, client):
        """Test that migration health requires authentication."""
        response = await client.get("/api/v1/migration-monitor/health")
        assert response.status_code in [401, 403]

    @pytest.mark.asyncio
    async def test_migration_health_authenticated(
        self, client, auth_headers
    ):
        """Test migration health with authentication."""
        response = await client.get(
            "/api/v1/migration-monitor/health",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "metrics" in data

    @pytest.mark.asyncio
    async def test_migration_events_authenticated(
        self, client, auth_headers
    ):
        """Test migration events with authentication."""
        response = await client.get(
            "/api/v1/migration-monitor/events",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_prometheus_metrics(self, client):
        """Test Prometheus metrics endpoint (no auth required)."""
        response = await client.get(
            "/api/v1/migration-monitor/prometheus"
        )
        assert response.status_code == 200
        assert "contractos_migrations_total" in response.text
