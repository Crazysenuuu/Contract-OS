"""Gap-fill milestones: audit chain, execution evidence, legal knowledge, billing, outbox, auth security

Revision ID: a1b2c3d4e5f6
Revises: 14ec94aaf2b3
Create Date: 2026-09-05

Adds the schema for the six gap-fill subsystems:
  - 1.20 audit hash chain (audit_events columns, audit_chain_roots, audit_evidence)
  - 1.15 execution evidence (signature_requests, signer_records,
    execution_requirements, execution_packages, execution_evidence_items)
  - 1.10 legal knowledge (legal_sources, legal_source_versions, legal_rules,
    legal_rule_versions)
  - 1.24 billing (billing_plans, subscriptions, entitlements, usage_records,
    invoices, invoice_lines)
  - 1.14 event outbox (outbox_events)
  - 1.22 auth security (refresh_tokens, login_attempts)

PostgreSQL-only hardening (guarded by dialect so SQLite dev/test works):
  - Row-Level Security on tenant-scoped tables reading app.current_tenant
  - Triggers rejecting DELETE/UPDATE on audit_events (immutability)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "14ec94aaf2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1.20 — audit hash chain
    # ------------------------------------------------------------------
    op.add_column(
        "audit_events",
        sa.Column("sequence_number", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "audit_events",
        sa.Column("prev_hash", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "audit_events",
        sa.Column("event_hash", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "audit_events",
        sa.Column("batch_id", sa.UUID(), nullable=True),
    )
    op.create_index("ix_audit_events_batch_id", "audit_events", ["batch_id"])
    op.create_unique_constraint(
        "uq_audit_events_tenant_sequence",
        "audit_events",
        ["tenant_id", "sequence_number"],
    )
    op.create_unique_constraint(
        "uq_audit_events_tenant_event_hash",
        "audit_events",
        ["tenant_id", "event_hash"],
    )

    op.create_table(
        "audit_chain_roots",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("root_hash", sa.String(length=128), nullable=False),
        sa.Column("first_event_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id"),
    )
    op.create_index(
        "ix_audit_chain_roots_tenant_id", "audit_chain_roots", ["tenant_id"]
    )

    op.create_table(
        "audit_evidence",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=True),
        sa.Column("version_id", sa.UUID(), nullable=True),
        sa.Column("evidence_type", sa.String(length=50), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("content_ref", sa.String(length=500), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agreement_id"], ["agreements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["version_id"], ["agreement_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_evidence_agreement_id", "audit_evidence", ["agreement_id"])
    op.create_index("ix_audit_evidence_tenant_id", "audit_evidence", ["tenant_id"])

    # ------------------------------------------------------------------
    # 1.15 — execution evidence
    # ------------------------------------------------------------------
    op.create_table(
        "signature_requests",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("party_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("signer_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agreement_id"], ["agreements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["party_id"], ["agreement_parties.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["agreement_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_signature_requests_agreement_id", "signature_requests", ["agreement_id"]
    )
    op.create_index(
        "ix_signature_requests_tenant_id", "signature_requests", ["tenant_id"]
    )

    op.create_table(
        "signer_records",
        sa.Column("signature_request_id", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("signer_type", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("consent_text", sa.Text(), nullable=False),
        sa.Column("signature_hash", sa.String(length=128), nullable=False),
        sa.Column("identity_verified", sa.Boolean(), nullable=False),
        sa.Column("identity_method", sa.String(length=50), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agreement_id"], ["agreements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["signature_request_id"], ["signature_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["version_id"], ["agreement_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signer_records_agreement_id", "signer_records", ["agreement_id"])
    op.create_index(
        "ix_signer_records_signature_request_id", "signer_records", ["signature_request_id"]
    )

    op.create_table(
        "execution_requirements",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=True),
        sa.Column("agreement_type_id", sa.UUID(), nullable=True),
        sa.Column("requirement_type", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("satisfied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("satisfied_by", sa.UUID(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agreement_id"], ["agreements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["agreement_type_id"], ["agreement_types.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["satisfied_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_requirements_agreement_id",
        "execution_requirements",
        ["agreement_id"],
    )
    op.create_index(
        "ix_execution_requirements_tenant_id", "execution_requirements", ["tenant_id"]
    )

    op.create_table(
        "execution_packages",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("final_document_hash", sa.String(length=128), nullable=False),
        sa.Column("package_hash", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sealed_by", sa.UUID(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agreement_id"], ["agreements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sealed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["agreement_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_packages_agreement_id", "execution_packages", ["agreement_id"]
    )
    op.create_index(
        "ix_execution_packages_tenant_id", "execution_packages", ["tenant_id"]
    )

    op.create_table(
        "execution_evidence_items",
        sa.Column("package_id", sa.UUID(), nullable=False),
        sa.Column("evidence_type", sa.String(length=50), nullable=False),
        sa.Column("data_json", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["package_id"], ["execution_packages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_evidence_items_package_id",
        "execution_evidence_items",
        ["package_id"],
    )

    # ------------------------------------------------------------------
    # 1.10 — legal knowledge
    # ------------------------------------------------------------------
    op.create_table(
        "legal_sources",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("jurisdiction_code", sa.String(length=10), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("source_version", sa.String(length=100), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_legal_sources_jurisdiction_code", "legal_sources", ["jurisdiction_code"]
    )
    op.create_index("ix_legal_sources_tenant_id", "legal_sources", ["tenant_id"])

    op.create_table(
        "legal_source_versions",
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("change_notes", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["legal_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_legal_source_versions_source_id", "legal_source_versions", ["source_id"]
    )

    op.create_table(
        "legal_rules",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("rule_key", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("jurisdiction_code", sa.String(length=10), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("source_version_id", sa.UUID(), nullable=True),
        sa.Column("applies_to_agreement_types", sa.JSON(), nullable=True),
        sa.Column("proposition", sa.Text(), nullable=False),
        sa.Column("executable_condition", sa.JSON(), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("reviewed_by", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["legal_sources.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["legal_source_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_legal_rules_jurisdiction_code", "legal_rules", ["jurisdiction_code"])
    op.create_index("ix_legal_rules_rule_key", "legal_rules", ["rule_key"])
    op.create_index("ix_legal_rules_tenant_id", "legal_rules", ["tenant_id"])

    op.create_table(
        "legal_rule_versions",
        sa.Column("rule_id", sa.UUID(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("proposition", sa.Text(), nullable=False),
        sa.Column("executable_condition", sa.JSON(), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("change_notes", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["rule_id"], ["legal_rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_legal_rule_versions_rule_id", "legal_rule_versions", ["rule_id"]
    )

    # ------------------------------------------------------------------
    # 1.24 — billing
    # ------------------------------------------------------------------
    op.create_table(
        "billing_plans",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("monthly_price_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("features", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    op.create_table(
        "subscriptions",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("current_period_start", sa.Date(), nullable=True),
        sa.Column("current_period_end", sa.Date(), nullable=True),
        sa.Column("seat_limit", sa.Integer(), nullable=False),
        sa.Column("external_provider", sa.String(length=50), nullable=True),
        sa.Column("external_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["billing_plans.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id"),
    )
    op.create_index("ix_subscriptions_tenant_id", "subscriptions", ["tenant_id"])

    op.create_table(
        "entitlements",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("feature_key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "feature_key", name="uq_entitlements_tenant_feature"),
    )
    op.create_index("ix_entitlements_feature_key", "entitlements", ["feature_key"])
    op.create_index("ix_entitlements_tenant_id", "entitlements", ["tenant_id"])

    op.create_table(
        "usage_records",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("feature_key", sa.String(length=100), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_records_feature_key", "usage_records", ["feature_key"])
    op.create_index("ix_usage_records_tenant_id", "usage_records", ["tenant_id"])

    op.create_table(
        "invoices",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("subscription_id", sa.UUID(), nullable=True),
        sa.Column("number", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invoices_tenant_id", "invoices", ["tenant_id"])

    op.create_table(
        "invoice_lines",
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.BigInteger(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invoice_lines_invoice_id", "invoice_lines", ["invoice_id"])

    # ------------------------------------------------------------------
    # 1.14 — event outbox
    # ------------------------------------------------------------------
    op.create_table(
        "outbox_events",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.UUID(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_events_aggregate_id", "outbox_events", ["aggregate_id"])
    op.create_index("ix_outbox_events_event_type", "outbox_events", ["event_type"])
    op.create_index("ix_outbox_events_status", "outbox_events", ["status"])
    op.create_index("ix_outbox_events_tenant_id", "outbox_events", ["tenant_id"])

    # ------------------------------------------------------------------
    # 1.22 — auth security
    # ------------------------------------------------------------------
    op.create_table(
        "refresh_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_token_id", sa.UUID(), nullable=True),
        sa.Column("revoked_reason", sa.String(length=100), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["user_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"])
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    op.create_table(
        "login_attempts",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=100), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_login_attempts_attempted_at", "login_attempts", ["attempted_at"])
    op.create_index("ix_login_attempts_email", "login_attempts", ["email"])
    op.create_index("ix_login_attempts_ip_address", "login_attempts", ["ip_address"])

    # ------------------------------------------------------------------
    # PostgreSQL hardening: RLS + audit immutability triggers
    # ------------------------------------------------------------------
    if _is_postgres():
        _enable_rls()
        _install_audit_triggers()


def downgrade() -> None:
    if _is_postgres():
        _drop_audit_triggers()
        _disable_rls()

    op.drop_table("login_attempts")
    op.drop_table("refresh_tokens")
    op.drop_table("outbox_events")
    op.drop_table("invoice_lines")
    op.drop_table("invoices")
    op.drop_table("usage_records")
    op.drop_table("entitlements")
    op.drop_table("subscriptions")
    op.drop_table("billing_plans")
    op.drop_table("legal_rule_versions")
    op.drop_table("legal_rules")
    op.drop_table("legal_source_versions")
    op.drop_table("legal_sources")
    op.drop_table("execution_evidence_items")
    op.drop_table("execution_packages")
    op.drop_table("execution_requirements")
    op.drop_table("signer_records")
    op.drop_table("signature_requests")
    op.drop_table("audit_evidence")
    op.drop_table("audit_chain_roots")

    op.drop_constraint("uq_audit_events_tenant_event_hash", "audit_events", type_="unique")
    op.drop_constraint("uq_audit_events_tenant_sequence", "audit_events", type_="unique")
    op.drop_index("ix_audit_events_batch_id", table_name="audit_events")
    op.drop_column("audit_events", "batch_id")
    op.drop_column("audit_events", "event_hash")
    op.drop_column("audit_events", "prev_hash")
    op.drop_column("audit_events", "sequence_number")


# --------------------------------------------------------------------------
# PostgreSQL-only helpers
# --------------------------------------------------------------------------

_RLS_TABLES: dict[str, str] = {
    "agreements": "organization_id",
    "audit_events": "tenant_id",
    "audit_evidence": "tenant_id",
    "outbox_events": "tenant_id",
    "subscriptions": "tenant_id",
    "usage_records": "tenant_id",
    "invoices": "tenant_id",
    "entitlements": "tenant_id",
    "signature_requests": "tenant_id",
    "execution_requirements": "tenant_id",
    "execution_packages": "tenant_id",
}


def _enable_rls() -> None:
    for table, tenant_col in _RLS_TABLES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON {table} "
            f"USING ({tenant_col} = NULLIF(current_setting('app.current_tenant', true), '')::uuid)"
        )


def _disable_rls() -> None:
    for table in _RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def _install_audit_triggers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_mutation() RETURNS trigger AS $$
        BEGIN
            IF current_setting('audit.maintenance', true) <> 'on' THEN
                RAISE EXCEPTION 'audit_events are immutable';
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation()"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation()"
    )


def _drop_audit_triggers() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_mutation()")