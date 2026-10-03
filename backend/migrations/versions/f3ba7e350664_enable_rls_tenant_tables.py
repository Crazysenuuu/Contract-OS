"""Enable row-level security across tenant-scoped tables.

Previously only ten tables carried an RLS policy, which meant the ~90 other
tenant-scoped tables were reachable by any query that forgot an
``organization_id`` filter. This migration closes that gap for every table
whose tenant column is NOT NULL.

Two prerequisites had to land first, and both are why this was not a
drop-in change:

1. **Worker tenant context.** Background jobs are written as tenant-agnostic
   fan-outs ("find every pending X"). With RLS active and no
   ``app.current_tenant`` set, those jobs match zero rows and report success
   while doing nothing. ``app.services.tenant_context.run_per_tenant()`` now
   loops organizations and lets RLS do the scoping;
   ``tenant_scope()`` covers tasks addressed by a single tenant.

2. **Bootstrap tenant scope.** Onboarding is the one flow that writes tenant
   rows before a tenant context can exist — registration creates the
   organization, its owner role and the owner membership, and there is no
   membership yet for the request-scoped dependency to resolve. Registration,
   RBAC invites, OIDC provisioning and SCIM provisioning now pin the context
   explicitly.

Deliberately excluded, with reasons:

* **Tenant column is nullable.** A strict policy silently hides shared or
  platform-wide rows (global templates, glossary terms, reference clauses).
  These need a per-table decision about whether shared rows are intentionally
  readable, which is not a mechanical change.
* **Credential/bootstrap lookups** — ``api_keys``, ``scim_tokens``,
  ``sso_connections``, ``otp_challenges``, ``tenants``. These are queried by
  hash, challenge id, email domain or slug with no organization filter,
  because the row being looked up is what tells the application which tenant
  the caller belongs to. RLS would hide the row needed to establish the
  context, which is unfixable by adding context: the context does not exist
  yet.
* **``organization_members``**, the same class of problem as above and for a
  sharper reason: it is the table tenant resolution itself reads.
  ``dependencies.tenant.get_current_organization_id()`` queries active
  memberships to decide which tenant a request belongs to, so a policy keyed
  on ``app.current_tenant`` evaluates against an unset GUC and matches zero
  rows — every authenticated request would fail, including single-organization
  users. Unlike the credential tables, whose rows are self-identifying by
  hash or id, membership rows are found by ``user_id``: they are isolated by
  the authenticated user, and every read in the codebase filters on it. Keep
  this table app-layer filtered; the 67 other targets are enforced by the
  database.
* **Already protected** by earlier migrations.

Revision ID: f3ba7e350664
Revises: 7c2d1e0f4a89
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f3ba7e350664"
down_revision: Union[str, Sequence[str], None] = "7c2d1e0f4a89"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Tables already under RLS from earlier revisions.
_ALREADY_PROTECTED = frozenset({
    "audit_events",
    "audit_evidence",
    "outbox_events",
    "subscriptions",
    "usage_records",
    "invoices",
    "entitlements",
    "signature_requests",
    "execution_requirements",
    "execution_packages",
    "document_search_index",
})

# Resolved before the requesting tenant is known.
_LOOKUP_TABLES = frozenset({
    "api_keys",
    "scim_tokens",
    "sso_connections",
    "otp_challenges",
    "tenants",
})

# Read to *resolve* the tenant, so the context does not exist yet. See the
# module docstring: a policy here would match zero rows and lock every
# authenticated user out.
_TENANT_RESOLUTION_TABLES = frozenset({"organization_members"})

# Tenant column is NOT NULL, so every row provably belongs to exactly one
# organization and strict isolation is correct.
_TENANT_SCOPED = {
    "action_items": "organization_id",
    "agreement_precedents": "organization_id",
    "agreements": "organization_id",
    "approval_definitions": "organization_id",
    "audit_batches": "tenant_id",
    "audit_chain_roots": "tenant_id",
    "automation_checkpoints": "organization_id",
    "automation_rules": "organization_id",
    "bulk_jobs": "organization_id",
    "clause_playbooks": "organization_id",
    "company_policies": "organization_id",
    "documents": "organization_id",
    "erasure_requests": "organization_id",
    "executive_insights": "organization_id",
    "export_jobs": "organization_id",
    "external_observations": "organization_id",
    "external_workspace_policies": "organization_id",
    "extraction_candidates": "organization_id",
    "extraction_conflicts": "organization_id",
    "extraction_provenance": "organization_id",
    "field_encryption_records": "organization_id",
    "forecast_runs": "organization_id",
    "human_review_tasks": "organization_id",
    "ingestion_batches": "organization_id",
    "ingestion_jobs": "organization_id",
    "integration_connections": "organization_id",
    "integration_connectors": "organization_id",
    "intelligence_conversations": "organization_id",
    "knowledge_chunks": "organization_id",
    "legal_entities": "organization_id",
    "legal_holds": "organization_id",
    "legal_representatives": "organization_id",
    "metric_anomalies": "organization_id",
    "metric_snapshots": "organization_id",
    "monitoring_evaluations": "organization_id",
    "monitoring_evidence": "organization_id",
    "monitoring_exceptions": "organization_id",
    "monitoring_runs": "organization_id",
    "monitoring_workspace_policies": "organization_id",
    "notification_preferences": "organization_id",
    "notifications": "organization_id",
    "obligation_exceptions": "organization_id",
    "obligation_monitoring": "organization_id",
    "ocr_documents": "organization_id",
    "organization_holidays": "organization_id",
    "organization_members": "organization_id",
    "party_addresses": "organization_id",
    "party_contacts": "organization_id",
    "party_identifiers": "organization_id",
    "redaction_requests": "organization_id",
    "repository_records": "organization_id",
    "retention_policies": "organization_id",
    "retention_records": "organization_id",
    "risk_graph_edges": "organization_id",
    "risk_graph_nodes": "organization_id",
    "risk_snapshots": "organization_id",
    "roles": "organization_id",
    "saved_filters": "organization_id",
    "saved_searches": "organization_id",
    "scenario_runs": "organization_id",
    "tenant_audit_logs": "tenant_id",
    "tenant_branding": "tenant_id",
    "tenant_invitations": "tenant_id",
    "tenant_themes": "tenant_id",
    "user_activity": "organization_id",
    "user_tasks": "organization_id",
    "webhook_endpoints": "organization_id",
    "workspace_lifecycle_configs": "organization_id",
}

_TARGETS = {
    table: column
    for table, column in _TENANT_SCOPED.items()
    if table not in _ALREADY_PROTECTED
    and table not in _LOOKUP_TABLES
    and table not in _TENANT_RESOLUTION_TABLES
}

assert not (_TARGETS.keys() & _ALREADY_PROTECTED), "table double-protected"
assert not (_TARGETS.keys() & _LOOKUP_TABLES), "credential lookup table must stay open"
assert not (
    _TARGETS.keys() & _TENANT_RESOLUTION_TABLES
), "tenant-resolution table cannot be RLS-protected"


def _policy_name(table: str) -> str:
    return f"tenant_isolation_{table}"


def upgrade() -> None:
    for table, column in sorted(_TARGETS.items()):
        # Policy first: a table with RLS enabled but no policy denies
        # everything, so an interrupted upgrade must leave it unprotected
        # rather than silently empty.
        op.execute(
            f"CREATE POLICY {_policy_name(table)} ON {table} "
            f"USING ({column} = "
            f"NULLIF(current_setting('app.current_tenant', true), '')::uuid)"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE so the policy also applies to the table owner. Without it,
        # migrations and admin connections running as the owner would bypass
        # isolation entirely.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in sorted(_TARGETS):
        op.execute(
            f"DROP POLICY IF EXISTS {_policy_name(table)} ON {table}"
        )
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")