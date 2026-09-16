# Migration Rollback Strategy

## Overview

This document outlines the rollback strategy for ContractOS database migrations. It covers backup procedures, rollback execution, data preservation, and disaster recovery.

## Table of Contents

1. [Pre-Migration Checklist](#pre-migration-checklist)
2. [Migration Types](#migration-types)
3. [Rollback Procedures](#rollback-procedures)
4. [Backup Strategy](#backup-strategy)
5. [Data Preservation](#data-preservation)
6. [Disaster Recovery](#disaster-recovery)
7. [Testing Rollbacks](#testing-rollbacks)
8. [Emergency Procedures](#emergency-procedures)

---

## Pre-Migration Checklist

Before running any migration in production:

### 1. Create Backup

```bash
# Full database backup
pg_dump -U cs -d contractos -F c -f backup_$(date +%Y%m%d_%H%M%S).dump

# Or for specific schema only
pg_dump -U cs -d contractos -F c -f backup_pre_migration.dump contractos
```

### 2. Verify Backup

```bash
# Test restore to a temporary database
createdb -U cs contractos_test_restore
pg_restore -U cs -d contractos_test_restore backup_pre_migration.dump
psql -U cs -d contractos_test_restore -c "SELECT COUNT(*) FROM users;"
```

### 3. Check Migration Compatibility

```bash
# Test migration in dry-run mode (offline)
cd backend
alembic upgrade head --sql > migration_plan.sql

# Review the SQL generated
cat migration_plan.sql
```

### 4. Announce Maintenance Window

- Notify users of potential downtime
- Schedule during low-traffic hours
- Have team on standby

---

## Migration Types

### Safe Migrations (Low Risk)

| Type | Risk Level | Rollback Difficulty |
|------|------------|---------------------|
| Adding nullable column | Low | Easy |
| Adding new table | Low | Easy |
| Creating index | Low | Easy |
| Adding enum value | Low | Easy |
| Seeding data | Low | Medium |

### Risky Migrations (High Risk)

| Type | Risk Level | Rollback Difficulty |
|------|------------|---------------------|
| Dropping column | High | Hard (data loss) |
| Renaming column | High | Hard |
| Changing column type | High | Hard |
| Adding NOT NULL constraint | High | Medium |
| Modifying foreign keys | High | Hard |

---

## Rollback Procedures

### Automatic Rollback (Alembic)

Alembic supports automatic rollback for simple migrations:

```bash
# Rollback to previous migration
cd backend
alembic downgrade -1

# Rollback to specific migration
alembic downgrade <revision_id>

# Rollback all (dangerous!)
alembic downgrade base
```

### Manual Rollback

For migrations that Alembic can't automatically reverse:

#### 1. Dropping a Column

```sql
-- Before dropping, backup the data
CREATE TABLE users_backup AS SELECT * FROM users;

-- Then drop
ALTER TABLE users DROP COLUMN old_column;
```

#### 2. Changing Column Type

```sql
-- Backup
CREATE TABLE agreements_backup AS SELECT * FROM agreements;

-- Change type (may require data conversion)
ALTER TABLE agreements ALTER COLUMN data TYPE jsonb USING data::jsonb;
```

#### 3. Adding NOT NULL Constraint

```sql
-- Remove constraint if needed
ALTER TABLE agreements ALTER COLUMN title DROP NOT NULL;
```

---

## Backup Strategy

### Automated Backups

```bash
# Add to crontab (daily at 2 AM)
0 2 * * * pg_dump -U cs -d contractos -F c -f /backups/contractos_$(date +\%Y\%m\%d).dump

# Retain backups for 30 days
0 3 * * * find /backups -name "*.dump" -mtime +30 -delete
```

### Backup Locations

| Location | Retention | Purpose |
|----------|-----------|---------|
| Local disk | 7 days | Quick restore |
| S3 bucket | 30 days | Disaster recovery |
| Offsite | 90 days | Compliance |

### Incremental Backups

```bash
# WAL archiving for point-in-time recovery
# Configure in postgresql.conf:
wal_level = replica
archive_mode = on
archive_command = 'cp %p /archive/%f'
```

---

## Data Preservation

### Preserving User Data

When migrating schema changes:

1. **Never delete data without backup**
2. **Create staging tables** for data transformation
3. **Validate data integrity** after migration

Example:

```sql
-- Step 1: Create backup
CREATE TABLE notifications_backup AS SELECT * FROM notifications;

-- Step 2: Apply migration
ALTER TABLE notifications ADD COLUMN new_field VARCHAR(255);

-- Step 3: Migrate data
UPDATE notifications SET new_field = old_field WHERE old_field IS NOT NULL;

-- Step 4: Verify
SELECT COUNT(*) FROM notifications WHERE new_field IS NULL;

-- Step 5: Drop old column (only after verification)
ALTER TABLE notifications DROP COLUMN old_field;
```

### Preserving Relationships

```sql
-- Before modifying foreign keys
SELECT 
    tc.table_name, 
    kcu.column_name, 
    ccu.table_name AS foreign_table_name,
    ccu.column_name AS foreign_column_name
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu
    ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage AS ccu
    ON ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY';
```

---

## Disaster Recovery

### Recovery Time Objectives (RTO)

| Scenario | Target RTO | Method |
|----------|------------|--------|
| Corrupted data | 1 hour | Point-in-time recovery |
| Failed migration | 30 minutes | Alembic downgrade |
| Full database loss | 4 hours | Backup restore |
| Hardware failure | 2 hours | Failover to replica |

### Recovery Point Objectives (RPO)

| Scenario | Target RPO | Backup Frequency |
|----------|------------|------------------|
| Migration failure | 0 (no loss) | Pre-migration backup |
| Data corruption | 1 hour | WAL archiving |
| Server failure | 24 hours | Daily backups |

### Recovery Steps

#### Scenario 1: Failed Migration

```bash
# 1. Stop application
systemctl stop contractos-api

# 2. Rollback migration
cd backend
alembic downgrade -1

# 3. Verify database
psql -U cs -d contractos -c "SELECT * FROM alembic_version;"

# 4. Restart application
systemctl start contractos-api

# 5. Check logs
journalctl -u contractos-api --since "5 minutes ago"
```

#### Scenario 2: Data Corruption

```bash
# 1. Stop writes to database
ALTER TABLE users SET (autovacuum_enabled = false);

# 2. Identify corruption timestamp
# Check application logs for last known good state

# 3. Restore from backup
dropdb -U cs contractos
createdb -U cs contractos
pg_restore -U cs -d contractos /backups/contractos_20240115.dump

# 4. Apply WAL logs up to corruption point
# (if using WAL archiving)

# 5. Verify data integrity
psql -U cs -d contractos -c "SELECT COUNT(*) FROM users;"
```

#### Scenario 3: Full Database Loss

```bash
# 1. Provision new database server
# 2. Create database
createdb -U cs contractos

# 3. Restore from latest backup
pg_restore -U cs -d contractos /backups/latest.dump

# 4. Apply WAL logs (if available)
# 5. Update application configuration
# 6. Restart services
```

---

## Testing Rollbacks

### Test Environment Setup

```bash
# Create test database from production backup
pg_dump -U cs -d contractos -F c -f test_backup.dump
createdb -U cs contractos_test
pg_restore -U cs -d contractos_test test_backup.dump
```

### Rollback Test Script

```bash
#!/bin/bash
# test_rollback.sh

set -e

TEST_DB="contractos_test"
BACKUP_FILE="test_backup.dump"

echo "Testing rollback..."

# 1. Apply migration
cd backend
DATABASE_URL="postgresql+asyncpg://cs@localhost/$TEST_DB" alembic upgrade head

# 2. Run rollback
DATABASE_URL="postgresql+asyncpg://cs@localhost/$TEST_DB" alembic downgrade -1

# 3. Verify rollback
psql -U cs -d $TEST_DB -c "SELECT version_num FROM alembic_version;"

# 4. Restore test database for next run
dropdb -U cs $TEST_DB
createdb -U cs $TEST_DB
pg_restore -U cs -d $TEST_DB $BACKUP_FILE

echo "Rollback test completed successfully!"
```

---

## Emergency Procedures

### Immediate Response

1. **Identify the issue**
   - Check application logs
   - Check database logs
   - Check migration status: `alembic current`

2. **Stop the bleeding**
   - If migration is in progress, wait for completion if possible
   - If application is erroring, consider stopping it

3. **Execute rollback**
   - Use the appropriate rollback procedure
   - Document what happened

4. **Notify stakeholders**
   - Inform team of issue and resolution
   - Update status page if applicable

### Contact Information

| Role | Contact | Responsibility |
|------|---------|----------------|
| DBA | on-call DBA | Database operations |
| Lead Dev | team lead | Application issues |
| DevOps | ops team | Infrastructure |

### Rollback Decision Tree

```
Migration Failed?
├── Yes, in progress
│   ├── Can it complete? → Let it finish, then rollback
│   └── Cannot complete → Kill connection, then rollback
├── Yes, completed
│   ├── Data loss? → Restore from backup
│   └── No data loss → Alembic downgrade
└── No, but application errors
    ├── Schema mismatch → Check migration status
    └── Application bug → Code rollback
```

---

## Migration History

Track all migrations and their rollback status:

| Date | Migration | Description | Rollback Tested | Rollback Available |
|------|-----------|-------------|-----------------|-------------------|
| 2024-01-15 | f1a2b3c4d5e6 | Phase 2-4 models | ✅ | ✅ |
| 2024-01-10 | 3090ac2b5eb4 | Internal signatures | ✅ | ✅ |
| 2024-01-05 | d615c21c7705 | Authorization models | ✅ | ✅ |

---

## Best Practices

1. **Always test rollbacks** before deploying to production
2. **Never skip migrations** - apply them in order
3. **Keep migrations small** - one logical change per migration
4. **Use reversible operations** when possible
5. **Document breaking changes** in migration comments
6. **Have a backup before every migration**
7. **Monitor application after migration** for at least 1 hour
8. **Keep migration history** for audit purposes

---

## Appendix: Quick Reference Commands

```bash
# Check current migration
alembic current

# Show migration history
alembic history

# Upgrade to latest
alembic upgrade head

# Rollback one step
alembic downgrade -1

# Generate new migration
alembic revision --autogenerate -m "description"

# Create backup
pg_dump -U cs -d contractos -f backup_$(date +%Y%m%d).dump

# Restore backup
pg_restore -U cs -d contractos backup_20240115.dump

# Check table sizes
SELECT pg_size_pretty(pg_total_relation_size('agreements'));
```
