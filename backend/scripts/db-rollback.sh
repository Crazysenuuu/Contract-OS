#!/bin/bash
# =============================================================================
# ContractOS Database Rollback Utility
# =============================================================================
# Usage: ./scripts/db-rollback.sh [command] [options]
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://cs@localhost:5432/contractos}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"

# =============================================================================
# Helper Functions
# =============================================================================

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_prerequisites() {
    # Check if psql is available
    if ! command -v psql &> /dev/null; then
        log_error "psql is not installed. Please install PostgreSQL client."
        exit 1
    fi

    # Check if alembic is available
    if ! command -v alembic &> /dev/null; then
        log_error "alembic is not installed. Please run: pip install alembic"
        exit 1
    fi

    # Create backup directory if it doesn't exist
    mkdir -p "$BACKUP_DIR"
}

# =============================================================================
# Backup Functions
# =============================================================================

create_backup() {
    local backup_name="contractos_$(date +%Y%m%d_%H%M%S).dump"
    local backup_path="$BACKUP_DIR/$backup_name"

    log_info "Creating backup: $backup_name"

    # Extract database name from URL
    local db_name=$(echo "$DATABASE_URL" | sed 's|.*/||')

    pg_dump -U cs -d "$db_name" -F c -f "$backup_path"

    if [ $? -eq 0 ]; then
        log_info "Backup created successfully: $backup_path"
        echo "$backup_path"
    else
        log_error "Backup failed!"
        exit 1
    fi
}

restore_backup() {
    local backup_file=$1

    if [ -z "$backup_file" ]; then
        log_error "Please provide backup file path"
        exit 1
    fi

    if [ ! -f "$backup_file" ]; then
        log_error "Backup file not found: $backup_file"
        exit 1
    fi

    log_warn "This will restore the database from: $backup_file"
    read -p "Are you sure? (yes/no): " confirm

    if [ "$confirm" != "yes" ]; then
        log_info "Restore cancelled."
        exit 0
    fi

    # Extract database name from URL
    local db_name=$(echo "$DATABASE_URL" | sed 's|.*/||')

    log_info "Dropping and recreating database..."
    dropdb -U cs "$db_name" 2>/dev/null || true
    createdb -U cs "$db_name"

    log_info "Restoring from backup..."
    pg_restore -U cs -d "$db_name" "$backup_file"

    if [ $? -eq 0 ]; then
        log_info "Database restored successfully!"
    else
        log_error "Restore failed!"
        exit 1
    fi
}

# =============================================================================
# Migration Functions
# =============================================================================

show_status() {
    log_info "Current migration status:"
    alembic current
    echo ""
    log_info "Migration history:"
    alembic history
}

rollback_one() {
    local backup_path=$(create_backup)

    log_info "Rolling back one migration..."
    alembic downgrade -1

    if [ $? -eq 0 ]; then
        log_info "Rollback completed successfully!"
        log_info "Backup available at: $backup_path"
    else
        log_error "Rollback failed! Restoring from backup..."
        restore_backup "$backup_path"
    fi
}

rollback_to() {
    local target=$1
    local backup_path=$(create_backup)

    log_info "Rolling back to migration: $target"
    alembic downgrade "$target"

    if [ $? -eq 0 ]; then
        log_info "Rollback completed successfully!"
        log_info "Backup available at: $backup_path"
    else
        log_error "Rollback failed! Restoring from backup..."
        restore_backup "$backup_path"
    fi
}

rollback_all() {
    local backup_path=$(create_backup)

    log_warn "This will rollback ALL migrations!"
    read -p "Are you sure? (yes/no): " confirm

    if [ "$confirm" != "yes" ]; then
        log_info "Rollback cancelled."
        exit 0
    fi

    log_info "Rolling back to base..."
    alembic downgrade base

    if [ $? -eq 0 ]; then
        log_info "Rollback completed successfully!"
        log_info "Backup available at: $backup_path"
    else
        log_error "Rollback failed! Restoring from backup..."
        restore_backup "$backup_path"
    fi
}

# =============================================================================
# Testing Functions
# =============================================================================

test_migration() {
    local test_db="contractos_test_$(date +%Y%m%d_%H%M%S)"
    local backup_path=$(create_backup)

    log_info "Testing migration on temporary database: $test_db"

    # Create test database
    createdb -U cs "$test_db"
    pg_restore -U cs -d "$test_db" "$backup_path"

    # Test upgrade
    log_info "Testing upgrade..."
    DATABASE_URL="postgresql+asyncpg://cs@localhost/$test_db" alembic upgrade head

    if [ $? -eq 0 ]; then
        log_info "Upgrade test passed!"

        # Test downgrade
        log_info "Testing downgrade..."
        DATABASE_URL="postgresql+asyncpg://cs@localhost/$test_db" alembic downgrade -1

        if [ $? -eq 0 ]; then
            log_info "Downgrade test passed!"
        else
            log_error "Downgrade test failed!"
        fi
    else
        log_error "Upgrade test failed!"
    fi

    # Cleanup
    log_info "Cleaning up test database..."
    dropdb -U cs "$test_db"

    log_info "Test completed!"
}

# =============================================================================
# Main Menu
# =============================================================================

show_help() {
    echo "ContractOS Database Rollback Utility"
    echo ""
    echo "Usage: $0 [command] [options]"
    echo ""
    echo "Commands:"
    echo "  status              Show current migration status"
    echo "  backup              Create a database backup"
    echo "  restore [file]      Restore from backup file"
    echo "  rollback            Rollback one migration"
    echo "  rollback-to [rev]   Rollback to specific migration"
    echo "  rollback-all        Rollback all migrations"
    echo "  test                Test migration on temporary database"
    echo "  help                Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 status"
    echo "  $0 backup"
    echo "  $0 restore ./backups/contractos_20240115.dump"
    echo "  $0 rollback"
    echo "  $0 rollback-to 3090ac2b5eb4"
    echo "  $0 test"
}

# =============================================================================
# Script Entry Point
# =============================================================================

check_prerequisites

case "${1:-help}" in
    status)
        show_status
        ;;
    backup)
        create_backup
        ;;
    restore)
        restore_backup "$2"
        ;;
    rollback)
        rollback_one
        ;;
    rollback-to)
        rollback_to "$2"
        ;;
    rollback-all)
        rollback_all
        ;;
    test)
        test_migration
        ;;
    help|*)
        show_help
        ;;
esac
