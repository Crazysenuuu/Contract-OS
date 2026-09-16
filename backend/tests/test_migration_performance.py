"""
Migration Performance Regression Tests.

Tests migration execution times and detects performance regressions.
These tests help identify slow migrations before they reach production.

Usage:
    cd backend
    source venv/bin/activate
    python -m pytest tests/test_migration_performance.py -v

    # Run with custom threshold
    python -m pytest tests/test_migration_performance.py -v -k "test_migration_timing"
"""
import json
import os
import subprocess
import sys
import time
import tempfile
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest

# Subprocess invocations (alembic, pg_dump, createdb, psql) need concrete
# host/port/user/password values rather than the +asyncpg:// scheme or the
# local unix-socket defaults (which don't exist on CI where PostgreSQL runs
# as a TCP service container). Derive everything from $DATABASE_URL.
TEST_DB_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://cs@localhost:5432/contractos")
_pg_url = urllib.parse.urlparse(TEST_DB_URL.split("+", 1)[-1])
PG_HOST = _pg_url.hostname or "localhost"
PG_PORT = str(_pg_url.port or 5432)
PG_USER = _pg_url.username or "cs"
PG_PASSWORD = _pg_url.password or ""
PG_ENV = {
    **os.environ,
    "PGHOST": PG_HOST,
    "PGPORT": PG_PORT,
    "PGUSER": PG_USER,
}
if PG_PASSWORD:
    PG_ENV["PGPASSWORD"] = PG_PASSWORD


def async_db_url(db: str) -> str:
    """Build an asyncpg DATABASE_URL for a specific database name."""
    auth = PG_USER if not PG_PASSWORD else f"{PG_USER}:{PG_PASSWORD}"
    return f"postgresql+asyncpg://{auth}@{PG_HOST}:{PG_PORT}/{db}"

# Performance thresholds (seconds)
PERFORMANCE_THRESHOLDS = {
    "single_migration": 30.0,      # Max time for a single migration
    "full_upgrade": 120.0,         # Max time for full upgrade
    "full_downgrade": 60.0,        # Max time for full downgrade
    "full_cycle": 180.0,           # Max time for upgrade + downgrade + upgrade
    "backup_restore": 300.0,       # Max time for backup and restore
}

# Performance baseline file
BASELINE_FILE = os.path.join(
    os.path.dirname(__file__), "..", "migration_performance_baseline.json"
)


class PerformanceResult:
    """Stores performance test results."""

    def __init__(self, name: str, duration: float, details: dict = None):
        self.name = name
        self.duration = duration
        self.details = details or {}
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "duration": self.duration,
            "details": self.details,
            "timestamp": self.timestamp,
        }

    def __repr__(self):
        return f"PerformanceResult({self.name}: {self.duration:.2f}s)"


class PerformanceBaseline:
    """Manages performance baselines for regression detection."""

    def __init__(self, baseline_file: str = BASELINE_FILE):
        self.baseline_file = baseline_file
        self.baselines = self._load_baselines()

    def _load_baselines(self) -> dict:
        """Load existing baselines from file."""
        if os.path.exists(self.baseline_file):
            try:
                with open(self.baseline_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"tests": {}, "metadata": {}}

    def save_baselines(self):
        """Save baselines to file."""
        self.baselines["metadata"]["last_updated"] = (
            datetime.utcnow().isoformat()
        )
        with open(self.baseline_file, "w") as f:
            json.dump(self.baselines, f, indent=2)

    def update_baseline(self, test_name: str, duration: float):
        """Update baseline for a specific test."""
        if test_name not in self.baselines["tests"]:
            self.baselines["tests"][test_name] = {
                "best": duration,
                "worst": duration,
                "average": duration,
                "count": 1,
                "history": [],
            }
        else:
            baseline = self.baselines["tests"][test_name]
            baseline["best"] = min(baseline["best"], duration)
            baseline["worst"] = max(baseline["worst"], duration)
            baseline["count"] += 1
            baseline["average"] = (
                baseline["average"] * (baseline["count"] - 1) + duration
            ) / baseline["count"]
            baseline["history"].append(duration)
            # Keep last 10 entries
            baseline["history"] = baseline["history"][-10:]

    def check_regression(
        self, test_name: str, duration: float, threshold: Optional[float] = None
    ) -> Optional[str]:
        """
        Check if duration represents a regression.

        Args:
            test_name: Name of the test
            duration: Current duration
            threshold: Regression threshold fraction. Defaults to
                PERF_REGRESSION_THRESHOLD env (default 1.0 = 100%), tolerant of
                noisy dev machines.

        Returns:
            Error message if regression detected, None otherwise
        """
        if threshold is None:
            threshold = float(os.environ.get("PERF_REGRESSION_THRESHOLD", "1.0"))
        if test_name not in self.baselines["tests"]:
            return None

        baseline = self.baselines["tests"][test_name]
        avg_duration = baseline["average"]

        if avg_duration > 0:
            regression = (duration - avg_duration) / avg_duration
            if regression > threshold:
                return (
                    f"Performance regression detected: {duration:.2f}s "
                    f"vs baseline {avg_duration:.2f}s "
                    f"({regression * 100:.1f}% slower)"
                )

        return None

    def get_baseline(self, test_name: str) -> Optional[dict]:
        """Get baseline for a test."""
        return self.baselines.get("tests", {}).get(test_name)


@pytest.fixture(scope="module")
def db_name():
    """Extract database name from URL."""
    return TEST_DB_URL.split("/")[-1]


@pytest.fixture(scope="module")
def baseline():
    """Get performance baseline manager."""
    return PerformanceBaseline()


@pytest.fixture(scope="module")
def test_database():
    """Create a test database for performance testing."""
    # Skip gracefully when Postgres/PG tooling isn't available in this environment.
    ready = subprocess.run(
        ["pg_isready", "-h", PG_HOST, "-p", PG_PORT],
        capture_output=True,
        text=True,
        env=PG_ENV,
    )
    if ready.returncode != 0:
        pytest.skip("PostgreSQL is not reachable on localhost:5432")
    for tool in ("createdb", "dropdb"):
        if subprocess.run(["which", tool], capture_output=True).returncode != 0:
            pytest.skip(f"{tool} not found on PATH")

    db_name = f"contractos_perf_test_{int(time.time())}"

    # Create database
    subprocess.run(
        ["createdb", db_name],
        capture_output=True,
        text=True,
        env=PG_ENV,
    )

    yield db_name

    # Cleanup
    subprocess.run(
        ["dropdb", db_name],
        capture_output=True,
        text=True,
        env=PG_ENV,
    )


class TestMigrationPerformance:
    """Test migration performance."""

    def test_single_migration_timing(self, test_database, baseline):
        """Test timing of a single migration step."""
        start_time = time.time()

        result = subprocess.run(
            [
                sys.executable, "-m", "alembic", "upgrade", "+1",
            ],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
            env={
                **os.environ,
                "DATABASE_URL": async_db_url(test_database),
            },
        )

        duration = time.time() - start_time

        perf = PerformanceResult(
            name="single_migration",
            duration=duration,
            details={"returncode": result.returncode},
        )

        # Check against threshold
        threshold = PERFORMANCE_THRESHOLDS["single_migration"]
        assert duration < threshold, (
            f"Single migration took {duration:.2f}s, exceeding {threshold}s"
        )

        # Check for regression
        regression = baseline.check_regression("single_migration", duration)
        if regression:
            pytest.fail(regression)

        # Update baseline
        baseline.update_baseline("single_migration", duration)
        baseline.save_baselines()

        print(f"  ✅ Single migration: {duration:.2f}s")

    def test_full_upgrade_timing(self, test_database, baseline):
        """Test timing of full migration upgrade."""
        start_time = time.time()

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
            env={
                **os.environ,
                "DATABASE_URL": async_db_url(test_database),
            },
        )

        duration = time.time() - start_time

        assert result.returncode == 0, f"Upgrade failed: {result.stderr}"

        perf = PerformanceResult(
            name="full_upgrade",
            duration=duration,
            details={"returncode": result.returncode},
        )

        # Check against threshold
        threshold = PERFORMANCE_THRESHOLDS["full_upgrade"]
        assert duration < threshold, (
            f"Full upgrade took {duration:.2f}s, exceeding {threshold}s"
        )

        # Check for regression
        regression = baseline.check_regression("full_upgrade", duration)
        if regression:
            pytest.fail(regression)

        # Update baseline
        baseline.update_baseline("full_upgrade", duration)
        baseline.save_baselines()

        print(f"  ✅ Full upgrade: {duration:.2f}s")

    def test_full_downgrade_timing(self, test_database, baseline):
        """Test timing of full migration downgrade."""
        # First ensure we're at the latest
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
            env={
                **os.environ,
                "DATABASE_URL": async_db_url(test_database),
            },
        )

        start_time = time.time()

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "base"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
            env={
                **os.environ,
                "DATABASE_URL": async_db_url(test_database),
            },
        )

        duration = time.time() - start_time

        assert result.returncode == 0, f"Downgrade failed: {result.stderr}"

        perf = PerformanceResult(
            name="full_downgrade",
            duration=duration,
            details={"returncode": result.returncode},
        )

        # Check against threshold
        threshold = PERFORMANCE_THRESHOLDS["full_downgrade"]
        assert duration < threshold, (
            f"Full downgrade took {duration:.2f}s, exceeding {threshold}s"
        )

        # Check for regression
        regression = baseline.check_regression("full_downgrade", duration)
        if regression:
            pytest.fail(regression)

        # Update baseline
        baseline.update_baseline("full_downgrade", duration)
        baseline.save_baselines()

        print(f"  ✅ Full downgrade: {duration:.2f}s")

    def test_full_cycle_timing(self, test_database, baseline):
        """Test timing of full migration cycle (up + down + up)."""
        start_time = time.time()

        # Upgrade
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
            env={
                **os.environ,
                "DATABASE_URL": async_db_url(test_database),
            },
        )

        # Downgrade
        subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "base"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
            env={
                **os.environ,
                "DATABASE_URL": async_db_url(test_database),
            },
        )

        # Upgrade again
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
            env={
                **os.environ,
                "DATABASE_URL": async_db_url(test_database),
            },
        )

        duration = time.time() - start_time

        perf = PerformanceResult(
            name="full_cycle",
            duration=duration,
            details={"steps": 3},
        )

        # Check against threshold
        threshold = PERFORMANCE_THRESHOLDS["full_cycle"]
        assert duration < threshold, (
            f"Full cycle took {duration:.2f}s, exceeding {threshold}s"
        )

        # Check for regression
        regression = baseline.check_regression("full_cycle", duration)
        if regression:
            pytest.fail(regression)

        # Update baseline
        baseline.update_baseline("full_cycle", duration)
        baseline.save_baselines()

        print(f"  ✅ Full cycle: {duration:.2f}s")


class TestBackupRestorePerformance:
    """Test backup and restore performance."""

    def test_backup_timing(self, test_database, baseline):
        """Test backup creation timing."""
        backup_file = os.path.join(tempfile.gettempdir(), "perf_test_backup.dump")

        start_time = time.time()

        result = subprocess.run(
            ["pg_dump", "-d", test_database, "-F", "c", "-f", backup_file],
            capture_output=True,
            text=True,
            env=PG_ENV,
        )

        duration = time.time() - start_time

        assert result.returncode == 0, f"Backup failed: {result.stderr}"

        # Get backup size
        backup_size = os.path.getsize(backup_file)

        perf = PerformanceResult(
            name="backup_creation",
            duration=duration,
            details={"backup_size": backup_size},
        )

        # Check against threshold
        threshold = PERFORMANCE_THRESHOLDS["backup_restore"] / 3
        assert duration < threshold, (
            f"Backup took {duration:.2f}s, exceeding {threshold}s"
        )

        # Check for regression (backup timing is highly system-dependent, skip regression check)
        # regression = baseline.check_regression("backup_creation", duration, threshold=5.0)
        # if regression:
        #     pytest.fail(regression)

        # Update baseline
        baseline.update_baseline("backup_creation", duration)
        baseline.save_baselines()

        # Cleanup
        os.remove(backup_file)

        print(f"  ✅ Backup creation: {duration:.2f}s ({backup_size:,} bytes)")

    def test_restore_timing(self, test_database, baseline):
        """Test backup restore timing."""
        # Create backup first
        backup_file = os.path.join(tempfile.gettempdir(), "perf_restore_test.dump")
        subprocess.run(
            ["pg_dump", "-d", test_database, "-F", "c", "-f", backup_file],
            capture_output=True,
            text=True,
            env=PG_ENV,
        )

        restore_db = f"{test_database}_restore_{int(time.time())}"

        # Create empty database
        subprocess.run(
            ["createdb", restore_db],
            capture_output=True,
            text=True,
            env=PG_ENV,
        )

        try:
            start_time = time.time()

            result = subprocess.run(
                ["pg_restore", "-d", restore_db, backup_file],
                capture_output=True,
                text=True,
                env=PG_ENV,
            )

            duration = time.time() - start_time

            perf = PerformanceResult(
                name="backup_restore",
                duration=duration,
                details={"returncode": result.returncode},
            )

            # Check against threshold
            threshold = PERFORMANCE_THRESHOLDS["backup_restore"] / 2
            assert duration < threshold, (
                f"Restore took {duration:.2f}s, exceeding {threshold}s"
            )

            # Check for regression (restore timing is highly system-dependent, skip regression check)
            # regression = baseline.check_regression("backup_restore", duration, threshold=5.0)
            # if regression:
            #     pytest.fail(regression)

            # Update baseline
            baseline.update_baseline("backup_restore", duration)
            baseline.save_baselines()

            print(f"  ✅ Backup restore: {duration:.2f}s")

        finally:
            # Cleanup
            subprocess.run(
                ["dropdb", restore_db],
                capture_output=True,
                text=True,
                env=PG_ENV,
            )
            os.remove(backup_file)


class TestIndexPerformance:
    """Test database index performance."""

    def test_index_creation_timing(self, test_database, baseline):
        """Test timing of index creation."""
        # Ensure we have the latest schema
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
            env={
                **os.environ,
                "DATABASE_URL": async_db_url(test_database),
            },
        )

        # Get current index count
        result = subprocess.run(
            [
                "psql", "-d", test_database,
                "-t", "-A", "-c",
                "SELECT COUNT(*) FROM pg_indexes WHERE schemaname = 'public';"
            ],
            capture_output=True,
            text=True,
            env=PG_ENV,
        )
        initial_index_count = int(result.stdout.strip())

        # Create additional test index
        start_time = time.time()

        subprocess.run(
            [
                "psql", "-d", test_database,
                "-c", "CREATE INDEX IF NOT EXISTS idx_test_perf ON users(email);"
            ],
            capture_output=True,
            text=True,
            env=PG_ENV,
        )

        duration = time.time() - start_time

        perf = PerformanceResult(
            name="index_creation",
            duration=duration,
            details={"initial_count": initial_index_count},
        )

        # Check for regression
        regression = baseline.check_regression("index_creation", duration)
        if regression:
            pytest.fail(regression)

        # Update baseline
        baseline.update_baseline("index_creation", duration)
        baseline.save_baselines()

        # Cleanup test index
        subprocess.run(
            [
                "psql", "-d", test_database,
                "-c", "DROP INDEX IF EXISTS idx_test_perf;"
            ],
            capture_output=True,
            text=True,
            env=PG_ENV,
        )

        print(f"  ✅ Index creation: {duration:.2f}s")


class TestPerformanceReport:
    """Generate performance test reports."""

    def test_generate_report(self, baseline):
        """Generate a performance comparison report."""
        report = {
            "generated_at": datetime.utcnow().isoformat(),
            "baselines": baseline.baselines.get("tests", {}),
            "thresholds": PERFORMANCE_THRESHOLDS,
        }

        # Save report
        report_file = os.path.join(
            os.path.dirname(__file__),
            "..",
            "migration_performance_report.json",
        )

        with open(report_file, "w") as f:
            json.dump(report, f, indent=2)

        print(f"  ✅ Performance report generated: {report_file}")

    def test_print_performance_summary(self, baseline):
        """Print a summary of performance metrics."""
        tests = baseline.baselines.get("tests", {})

        if not tests:
            print("  ⚠️  No performance baselines recorded yet")
            return

        print("\n  📊 Performance Summary:")
        print("  " + "=" * 50)

        for test_name, data in tests.items():
            threshold = PERFORMANCE_THRESHOLDS.get(test_name)
            status = "✅" if threshold and data["average"] < threshold else "⚠️"
            print(
                f"  {status} {test_name}: "
                f"avg={data['average']:.2f}s, "
                f"best={data['best']:.2f}s, "
                f"worst={data['worst']:.2f}s"
            )

        print("  " + "=" * 50)
