"""Guard the RLS migration list against model drift.

``f3ba7e350664_enable_rls_tenant_tables`` hard-codes the tables it protects,
which is deliberate — a migration must not change meaning because a model
changed. That does mean the list can silently go stale, so these tests assert
the three invariants that make the exclusion decisions correct.
"""

import importlib.util
from pathlib import Path

import pytest

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.models.base import Base

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "f3ba7e350664_enable_rls_tenant_tables.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("rls_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _load_migration()


def test_every_target_table_exists_in_metadata(migration):
    missing = sorted(set(migration._TARGETS) - set(Base.metadata.tables))
    assert missing == [], f"migration targets tables that no longer exist: {missing}"


def test_every_target_tenant_column_is_not_null(migration):
    """
    A nullable tenant column would make the strict policy hide shared rows.

    If one of these becomes nullable, the table must move to the deliberately
    excluded set rather than keep an isolation policy that quietly drops rows.
    """
    nullable = []
    for table, column in migration._TARGETS.items():
        col = Base.metadata.tables[table].columns.get(column)
        if col is None:
            nullable.append(f"{table}.{column} (column missing)")
        elif col.nullable:
            nullable.append(f"{table}.{column}")
    assert nullable == [], (
        "tables gained a nullable tenant column and need an explicit "
        f"shared-row decision: {nullable}"
    )


def test_lookup_tables_are_never_protected(migration):
    """
    Credential lookups are resolved before the tenant is known.

    ``api_keys``/``scim_tokens`` by hash, ``otp_challenges`` by challenge id,
    ``sso_connections`` by email domain, ``tenants`` by slug. The row being
    looked up is what tells the application which tenant the caller is, so
    RLS would hide the row needed to establish the context.
    """
    for table in migration._LOOKUP_TABLES:
        assert table not in migration._TARGETS, (
            f"{table} is a credential/bootstrap lookup and must stay readable "
            "before tenant context exists"
        )


def test_no_table_is_protected_twice(migration):
    overlap = set(migration._TARGETS) & migration._ALREADY_PROTECTED
    assert overlap == set()


def test_tenant_resolution_table_is_not_protected(migration):
    """
    ``organization_members`` is the table tenant resolution reads.

    ``get_current_organization_id()`` looks up active memberships to decide
    which tenant a request acts in. A policy keyed on ``app.current_tenant``
    would evaluate against an unset GUC and match nothing, so enabling RLS
    here fails every authenticated request — single-organization users
    included. Guarded explicitly because the symptom appears at runtime, not
    at migration time: the DDL is valid and the migration would "succeed".
    """
    for table in migration._TENANT_RESOLUTION_TABLES:
        assert table not in migration._TARGETS, (
            f"{table} resolves the tenant, so it must stay readable before "
            "tenant context exists"
        )


def test_target_count_is_stable(migration):
    """
    Pin the target count.

    A silent change here means a table gained or lost database-enforced
    isolation; the RLS gaps this migration closes are exactly the kind that
    stay invisible until someone leaks data.
    """
    assert len(migration._TARGETS) == 67


def test_policy_uses_the_declared_tenant_column(migration):
    for table, column in migration._TARGETS.items():
        assert column in Base.metadata.tables[table].columns, (
            f"{table} has no {column} column"
        )


def test_billing_tables_are_absent(migration):
    """Billing is intentionally out of scope for this migration."""
    billing = {t for t in migration._TARGETS if t.startswith("billing")}
    assert billing == set()


def test_upgrade_is_idempotent_in_shape(migration):
    """Every target gets exactly one policy name, shared with downgrade."""
    names = {migration._policy_name(t) for t in migration._TARGETS}
    assert len(names) == len(migration._TARGETS)