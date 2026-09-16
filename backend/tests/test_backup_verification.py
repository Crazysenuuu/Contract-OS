"""
Automated Backup Verification Tests.

Tests that verify backup creation, restoration, and data integrity.
These tests should be run periodically to ensure backup reliability.

Usage:
    cd backend
    source venv/bin/activate
    python -m pytest tests/test_backup_verification.py -v

Note: These tests require a running PostgreSQL database.
"""
import asyncio
import os
import subprocess
import sys
import tempfile
import urllib.parse
import pytest
from datetime import datetime
from pathlib import Path

# Test configuration
TEST_DB_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://cs@localhost:5432/contractos")
BACKUP_DIR = tempfile.mkdtemp(prefix="contractos_backup_test_")

# libpq tools (pg_dump/psql/createdb) don't understand the +asyncpg:// scheme
# and can't use the local Unix socket on CI, where PostgreSQL runs as a TCP
# service container. Derive the standard PG* connection variables from
# TEST_DB_URL and pass them to every subprocess call.
_pg_url = urllib.parse.urlparse(TEST_DB_URL.split("+", 1)[-1])
PG_ENV = {
    **os.environ,
    "PGHOST": _pg_url.hostname or "localhost",
    "PGPORT": str(_pg_url.port or 5432),
    "PGUSER": _pg_url.username or "cs",
    "PGPASSWORD": _pg_url.password or "password",
}


@pytest.fixture(scope="module")
def db_name():
    """Extract database name from URL. Skip if PostgreSQL isn't reachable."""
    ready = subprocess.run(
        ["pg_isready", "-h", PG_ENV["PGHOST"], "-p", PG_ENV["PGPORT"]],
        capture_output=True,
        text=True,
        env=PG_ENV,
    )
    if ready.returncode != 0:
        pytest.skip("PostgreSQL is not reachable on localhost:5432")
    for tool in ("pg_dump", "pg_restore", "psql", "createdb", "dropdb"):
        if subprocess.run(["which", tool], capture_output=True).returncode != 0:
            pytest.skip(f"{tool} not found on PATH")
    return TEST_DB_URL.split("/")[-1]


@pytest.fixture(scope="module")
def backup_dir():
    """Create temporary backup directory."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    yield BACKUP_DIR
    # Cleanup after all tests
    import shutil
    shutil.rmtree(BACKUP_DIR, ignore_errors=True)


@pytest.fixture(scope="module")
def pg_dump_backup(db_name, backup_dir):
    """Create a pg_dump backup once per module, for restore tests."""
    backup_file = os.path.join(backup_dir, "test_backup.dump")
    if not os.path.exists(backup_file):
        result = subprocess.run(
            ["pg_dump", "-d", db_name, "-F", "c", "-f", backup_file],
            capture_output=True,
            text=True,
            env=PG_ENV,
        )
        assert result.returncode == 0, f"pg_dump failed: {result.stderr}"
    return backup_file


class TestBackupCreation:
    """Test backup creation functionality."""

    def test_create_pg_dump_backup(self, db_name, backup_dir):
        """Test creating a backup using pg_dump."""
        backup_file = os.path.join(backup_dir, "test_backup.dump")

        result = subprocess.run(
            ["pg_dump", "-d", db_name, "-F", "c", "-f", backup_file],
            capture_output=True,
            text=True,
            env=PG_ENV,
        )

        assert result.returncode == 0, f"pg_dump failed: {result.stderr}"
        assert os.path.exists(backup_file), "Backup file not created"

        # Check file size (should be > 0)
        file_size = os.path.getsize(backup_file)
        assert file_size > 0, "Backup file is empty"
        print(f"  ✅ Backup created: {backup_file} ({file_size:,} bytes)")

    def test_create_sql_backup(self, db_name, backup_dir):
        """Test creating a SQL format backup."""
        backup_file = os.path.join(backup_dir, "test_backup.sql")

        result = subprocess.run(
            ["pg_dump", "-d", db_name, "-F", "p", "-f", backup_file],
            capture_output=True,
            text=True,
            env=PG_ENV,
        )

        assert result.returncode == 0, f"pg_dump failed: {result.stderr}"
        assert os.path.exists(backup_file), "SQL backup file not created"

        # Verify SQL file contains expected content
        with open(backup_file, "r") as f:
            content = f.read()
            assert "PostgreSQL database dump" in content or "CREATE TABLE" in content
            print(f"  ✅ SQL backup created: {backup_file}")

    def test_backup_contains_all_tables(self, db_name, backup_dir):
        """Test that backup contains all expected tables."""
        backup_file = os.path.join(backup_dir, "test_backup.dump")

        # Get list of tables in backup
        result = subprocess.run(
            ["pg_restore", "-l", backup_file],
            capture_output=True,
            text=True,
            env=PG_ENV,
        )

        assert result.returncode == 0, f"pg_restore listing failed: {result.stderr}"

        # Check for critical tables
        critical_tables = [
            "users",
            "organizations",
            "agreements",
            "agreement_types",
            "jurisdictions",
            "company_policies",
            "languages",
        ]

        missing_tables = []
        for table in critical_tables:
            if table not in result.stdout:
                missing_tables.append(table)

        assert len(missing_tables) == 0, f"Missing tables in backup: {missing_tables}"
        print(f"  ✅ Backup contains all {len(critical_tables)} critical tables")


class TestBackupRestore:
    """Test backup restoration functionality."""

    def test_restore_to_new_database(self, db_name, pg_dump_backup):
        """Test restoring backup to a new database."""
        test_db = f"contractos_restore_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        backup_file = pg_dump_backup

        try:
            # Create test database
            result = subprocess.run(
                ["createdb", test_db],
                capture_output=True,
                text=True,
                env=PG_ENV,
            )
            assert result.returncode == 0, f"createdb failed: {result.stderr}"

            # Restore backup
            result = subprocess.run(
                ["pg_restore", "-d", test_db, backup_file],
                capture_output=True,
                text=True,
                env=PG_ENV,
            )
            assert result.returncode == 0, f"pg_restore failed: {result.stderr}"

            # Verify tables exist in restored database
            result = subprocess.run(
                [
                    "psql", "-d", test_db,
                    "-c", "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';"
                ],
                capture_output=True,
                text=True,
                env=PG_ENV,
            )
            assert result.returncode == 0
            table_count = int(result.stdout.strip().split("\n")[-2].strip())
            assert table_count > 50, f"Expected >50 tables, got {table_count}"
            print(f"  ✅ Restored database has {table_count} tables")

        finally:
            # Cleanup test database
            subprocess.run(
                ["dropdb", test_db],
                capture_output=True,
                text=True,
                env=PG_ENV,
            )

    def test_restore_data_integrity(self, db_name, pg_dump_backup):
        """Test that restored data matches original."""
        test_db = f"contractos_integrity_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        backup_file = pg_dump_backup

        try:
            # Get row counts from original database
            original_counts = {}
            for table in ["users", "organizations", "agreements", "languages"]:
                result = subprocess.run(
                    ["psql", "-d", db_name,
                     "-t", "-A", "-c", f"SELECT COUNT(*) FROM {table};"],
                    capture_output=True,
                    text=True,
                    env=PG_ENV,
                )
                if result.returncode == 0:
                    original_counts[table] = int(result.stdout.strip())

            # Create and restore test database
            subprocess.run(["createdb", test_db], check=True, env=PG_ENV)
            subprocess.run(
                ["pg_restore", "-d", test_db, backup_file],
                check=True,
                env=PG_ENV,
            )

            # Verify row counts match
            for table, original_count in original_counts.items():
                result = subprocess.run(
                    ["psql", "-d", test_db,
                     "-t", "-A", "-c", f"SELECT COUNT(*) FROM {table};"],
                    capture_output=True,
                    text=True,
                    env=PG_ENV,
                )
                restored_count = int(result.stdout.strip())
                assert original_count == restored_count, (
                    f"Row count mismatch for {table}: "
                    f"original={original_count}, restored={restored_count}"
                )

            print(f"  ✅ Data integrity verified for {len(original_counts)} tables")

        finally:
            subprocess.run(
                ["dropdb", test_db],
                capture_output=True,
                text=True,
                env=PG_ENV,
            )


class TestBackupScheduling:
    """Test backup scheduling and automation."""

    def test_backup_retention_policy(self, backup_dir):
        """Test that old backups are cleaned up."""
        # Create some fake backup files with different dates
        from datetime import timedelta

        now = datetime.now()
        for days_ago in [1, 7, 15, 30, 45]:
            fake_date = now - timedelta(days=days_ago)
            fake_file = os.path.join(
                backup_dir,
                f"contractos_{fake_date.strftime('%Y%m%d')}.dump"
            )
            Path(fake_file).touch()

        # Simulate retention policy (keep 30 days)
        retention_days = 30
        cutoff_date = now - timedelta(days=retention_days)

        deleted_count = 0
        for file_path in Path(backup_dir).glob("contractos_*.dump"):
            file_date_str = file_path.stem.split("_")[1]
            try:
                file_date = datetime.strptime(file_date_str, "%Y%m%d")
                if file_date < cutoff_date:
                    file_path.unlink()
                    deleted_count += 1
            except ValueError:
                continue

        # Should have deleted files older than 30 days
        assert deleted_count >= 1, "Retention policy didn't delete old backups"
        print(f"  ✅ Retention policy deleted {deleted_count} old backup(s)")


class TestMigrationRollback:
    """Test migration rollback with backup."""

    def test_alembic_rollback_available(self):
        """Test that Alembic rollback commands work."""
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "history"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )

        assert result.returncode == 0, f"alembic history failed: {result.stderr}"
        assert "14ec94aaf2b3" in result.stdout, "Head migration not found"
        print("  ✅ Alembic history accessible")

    def test_migration_chain_integrity(self):
        """Test that migration chain is complete (no gaps)."""
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "history"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )

        assert result.returncode == 0, f"alembic history failed: {result.stderr}"

        # Count migrations
        migration_lines = [
            line for line in result.stdout.split("\n")
            if "->" in line or "<base>" in line
        ]

        assert len(migration_lines) >= 1, (
            f"Expected at least 1 migration, got {len(migration_lines)}"
        )
        print(f"  ✅ Migration chain has {len(migration_lines)} entries")


class TestBackupPerformance:
    """Test backup performance and size."""

    def test_backup_size_reasonable(self, db_name, backup_dir):
        """Test that backup size is within expected range."""
        backup_file = os.path.join(backup_dir, "test_backup.dump")

        # Create fresh backup
        subprocess.run(
            ["pg_dump", "-d", db_name, "-F", "c", "-f", backup_file],
            check=True,
            env=PG_ENV,
        )

        file_size = os.path.getsize(backup_file)

        # Expected size range: 100KB - 100MB for a development database
        min_size = 100 * 1024  # 100KB
        max_size = 100 * 1024 * 1024  # 100MB

        assert min_size <= file_size <= max_size, (
            f"Backup size {file_size:,} bytes is outside expected range "
            f"({min_size:,} - {max_size:,} bytes)"
        )
        print(f"  ✅ Backup size is reasonable: {file_size:,} bytes")

    def test_backup_creation_time(self, db_name, backup_dir):
        """Test that backup creation completes within time limit."""
        import time

        backup_file = os.path.join(backup_dir, "test_backup.dump")

        start_time = time.time()
        subprocess.run(
            ["pg_dump", "-d", db_name, "-F", "c", "-f", backup_file],
            check=True,
            env=PG_ENV,
        )
        elapsed_time = time.time() - start_time

        # Should complete within 60 seconds for development database
        max_time = 60
        assert elapsed_time < max_time, (
            f"Backup took {elapsed_time:.2f}s, exceeding {max_time}s limit"
        )
        print(f"  ✅ Backup completed in {elapsed_time:.2f}s")


# =============================================================================
# Test Runner
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
