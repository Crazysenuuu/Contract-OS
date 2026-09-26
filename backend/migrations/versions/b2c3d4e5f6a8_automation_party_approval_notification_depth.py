"""Automation rules, party master data, approval depth, notification depth,
lifecycle config (spec §3.4/§3.6/§3.10/§3.13/§3.14/§3.19)

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-09-26

- automation_rules / automation_executions / automation_checkpoints (§3.19)
- party_contacts / party_addresses / party_identifiers (§3.4.6-8)
- approval stage quorum + deadline columns, record lock columns (§3.6.28-35)
- negotiation concessions + playbooks + deadlocks (§3.22-adjacent §9-10, §23)
- notification_deliveries / email_templates / quiet hours (§3.10)
- workspace_lifecycle_configs (§3.12.22)
- obligation_exceptions / risk_policy_versions / risk_snapshots (§3.13/§3.14)
- external_parties.party_metadata for evidence requests (§3.20.28)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a8"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts_cols() -> list:
    return [
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
    ]


def _org_fk(table: str) -> None:
    op.create_foreign_key(
        f"{table}_organization_id_fkey",
        table,
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )


def upgrade() -> None:
    # --- automation rules ---------------------------------------------------
    op.create_table(
        "automation_rules",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("trigger_event", sa.String(length=100), nullable=False),
        sa.Column("conditions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("action_key", sa.String(length=60), nullable=False),
        sa.Column("action_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=10), nullable=False),
        sa.Column("max_executions_per_hour", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("automation_rules_pkey")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("automation_rules_created_by_fkey")
        ),
    )
    _org_fk("automation_rules")
    op.create_index(
        op.f("ix_automation_rules_organization_id"),
        "automation_rules",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_automation_rules_trigger_event"), "automation_rules", ["trigger_event"]
    )

    op.create_table(
        "automation_executions",
        sa.Column("rule_id", sa.UUID(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("event_aggregate_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("automation_executions_pkey")),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["automation_rules.id"],
            name=op.f("automation_executions_rule_id_fkey"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_automation_executions_rule_id"), "automation_executions", ["rule_id"]
    )
    op.create_index(
        op.f("ix_automation_executions_event_aggregate_id"),
        "automation_executions",
        ["event_aggregate_id"],
    )

    op.create_table(
        "automation_checkpoints",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("workflow_step_instance_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("required_permission", sa.String(length=100), nullable=True),
        sa.Column("assignee_user_id", sa.UUID(), nullable=True),
        sa.Column("resolved_by", sa.UUID(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolution_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("automation_checkpoints_pkey")),
        sa.ForeignKeyConstraint(
            ["workflow_step_instance_id"],
            ["orch_workflow_step_instances.id"],
            name=op.f("automation_checkpoints_workflow_step_instance_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assignee_user_id"],
            ["users.id"],
            name=op.f("automation_checkpoints_assignee_user_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"], ["users.id"], name=op.f("automation_checkpoints_resolved_by_fkey")
        ),
    )
    _org_fk("automation_checkpoints")
    op.create_index(
        op.f("ix_automation_checkpoints_organization_id"),
        "automation_checkpoints",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_automation_checkpoints_workflow_step_instance_id"),
        "automation_checkpoints",
        ["workflow_step_instance_id"],
    )
    op.create_index(
        op.f("ix_automation_checkpoints_assignee_user_id"),
        "automation_checkpoints",
        ["assignee_user_id"],
    )

    # --- party master data ---------------------------------------------------
    op.create_table(
        "party_contacts",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("legal_entity_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("party_contacts_pkey")),
    )
    _org_fk("party_contacts")
    op.create_foreign_key(
        "party_contacts_legal_entity_id_fkey",
        "party_contacts",
        "legal_entities",
        ["legal_entity_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_party_contacts_organization_id"), "party_contacts", ["organization_id"]
    )
    op.create_index(
        op.f("ix_party_contacts_legal_entity_id"), "party_contacts", ["legal_entity_id"]
    )
    op.create_index(op.f("ix_party_contacts_email"), "party_contacts", ["email"])

    op.create_table(
        "party_addresses",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("legal_entity_id", sa.UUID(), nullable=True),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("address_type", sa.String(length=30), nullable=False),
        sa.Column("line1", sa.String(length=255), nullable=False),
        sa.Column("line2", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("region", sa.String(length=120), nullable=True),
        sa.Column("postal_code", sa.String(length=30), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("party_addresses_pkey")),
    )
    _org_fk("party_addresses")
    op.create_foreign_key(
        "party_addresses_legal_entity_id_fkey",
        "party_addresses",
        "legal_entities",
        ["legal_entity_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "party_addresses_contact_id_fkey",
        "party_addresses",
        "party_contacts",
        ["contact_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_party_addresses_organization_id"), "party_addresses", ["organization_id"]
    )
    op.create_index(
        op.f("ix_party_addresses_legal_entity_id"), "party_addresses", ["legal_entity_id"]
    )
    op.create_index(
        op.f("ix_party_addresses_contact_id"), "party_addresses", ["contact_id"]
    )

    op.create_table(
        "party_identifiers",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("legal_entity_id", sa.UUID(), nullable=False),
        sa.Column("identifier_type", sa.String(length=40), nullable=False),
        sa.Column("value", sa.String(length=120), nullable=False),
        sa.Column("issuing_country", sa.String(length=2), nullable=True),
        sa.Column("verification_status", sa.String(length=20), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("party_identifiers_pkey")),
        sa.UniqueConstraint(
            "legal_entity_id",
            "identifier_type",
            "value",
            name="uq_party_identifier_entity_type_value",
        ),
    )
    _org_fk("party_identifiers")
    op.create_foreign_key(
        "party_identifiers_legal_entity_id_fkey",
        "party_identifiers",
        "legal_entities",
        ["legal_entity_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_party_identifiers_organization_id"),
        "party_identifiers",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_party_identifiers_legal_entity_id"),
        "party_identifiers",
        ["legal_entity_id"],
    )
    op.create_index(
        "ix_party_identifiers_org_type",
        "party_identifiers",
        ["organization_id", "identifier_type"],
    )

    # --- approval depth (§3.6.28-35) ----------------------------------------
    op.add_column(
        "approval_stages",
        sa.Column("minimum_approvals", sa.Integer(), nullable=True),
    )
    op.add_column(
        "approval_stages",
        sa.Column("deadline_hours", sa.Integer(), nullable=True),
    )
    op.add_column(
        "approval_records",
        sa.Column("stage_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "approval_records",
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "approval_records",
        sa.Column("version_locked_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- negotiation concessions / playbooks / deadlocks ---------------------
    op.add_column(
        "negotiation_actions",
        sa.Column("concession_value", sa.Numeric(15, 2), nullable=True),
    )
    op.add_column(
        "negotiation_actions",
        sa.Column("concession_note", sa.Text(), nullable=True),
    )
    op.add_column(
        "agreement_change_items",
        sa.Column("concession_value", sa.Numeric(15, 2), nullable=True),
    )

    op.create_table(
        "clause_playbooks",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("clause_identifier", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("positions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("clause_playbooks_pkey")),
    )
    _org_fk("clause_playbooks")
    op.create_index(
        op.f("ix_clause_playbooks_organization_id"),
        "clause_playbooks",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_clause_playbooks_clause_identifier"),
        "clause_playbooks",
        ["clause_identifier"],
    )

    op.create_table(
        "negotiation_deadlocks",
        sa.Column("agreement_id", sa.UUID(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column(
            "disputed_clauses", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("detection_rule", sa.String(length=120), nullable=True),
        sa.Column("resolved_by", sa.UUID(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("negotiation_deadlocks_pkey")),
        sa.ForeignKeyConstraint(
            ["agreement_id"],
            ["agreements.id"],
            name=op.f("negotiation_deadlocks_agreement_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"],
            ["users.id"],
            name=op.f("negotiation_deadlocks_resolved_by_fkey"),
        ),
    )
    op.create_index(
        op.f("ix_negotiation_deadlocks_agreement_id"),
        "negotiation_deadlocks",
        ["agreement_id"],
    )

    # --- notification depth (§3.10) ------------------------------------------
    op.create_table(
        "notification_deliveries",
        sa.Column("notification_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("notification_deliveries_pkey")),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.id"],
            name=op.f("notification_deliveries_notification_id_fkey"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_notification_deliveries_notification_id"),
        "notification_deliveries",
        ["notification_id"],
    )

    op.create_table(
        "email_templates",
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("subject_template", sa.String(length=500), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column(
            "required_variables", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("email_templates_pkey")),
        sa.UniqueConstraint("key", name=op.f("email_templates_key_key")),
    )
    op.create_index(op.f("ix_email_templates_key"), "email_templates", ["key"])

    op.add_column(
        "notification_preferences",
        sa.Column("quiet_hours_start", sa.Integer(), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("quiet_hours_end", sa.Integer(), nullable=True),
    )

    # --- lifecycle workspace config (§3.12.22) -------------------------------
    op.create_table(
        "workspace_lifecycle_configs",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("renewal_notice_days", sa.Integer(), nullable=False),
        sa.Column("expiry_warning_days", sa.Integer(), nullable=False),
        sa.Column("notify_on_renewal_due", sa.Boolean(), nullable=False),
        sa.Column("notify_on_expiration", sa.Boolean(), nullable=False),
        sa.Column("create_action_items", sa.Boolean(), nullable=False),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("workspace_lifecycle_configs_pkey")),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("workspace_lifecycle_configs_organization_id_fkey"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_workspace_lifecycle_configs_organization_id"),
        "workspace_lifecycle_configs",
        ["organization_id"],
        unique=True,
    )

    # --- obligation exceptions / risk policies / risk snapshots --------------
    op.create_table(
        "obligation_exceptions",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("obligation_id", sa.UUID(), nullable=False),
        sa.Column("exception_type", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("revised_due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=False),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("obligation_exceptions_pkey")),
        sa.ForeignKeyConstraint(
            ["obligation_id"],
            ["obligations.id"],
            name=op.f("obligation_exceptions_obligation_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("obligation_exceptions_requested_by_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name=op.f("obligation_exceptions_decided_by_fkey")
        ),
    )
    _org_fk("obligation_exceptions")
    op.create_index(
        op.f("ix_obligation_exceptions_organization_id"),
        "obligation_exceptions",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_obligation_exceptions_obligation_id"),
        "obligation_exceptions",
        ["obligation_id"],
    )

    op.create_table(
        "risk_policy_versions",
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("policy", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("risk_policy_versions_pkey")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("risk_policy_versions_created_by_fkey")
        ),
    )
    op.create_foreign_key(
        "risk_policy_versions_organization_id_fkey",
        "risk_policy_versions",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_risk_policy_versions_organization_id"),
        "risk_policy_versions",
        ["organization_id"],
    )

    op.create_table(
        "risk_snapshots",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=True),
        sa.Column("overall_score", sa.Integer(), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("components", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("policy_version_id", sa.UUID(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols(),
        sa.PrimaryKeyConstraint("id", name=op.f("risk_snapshots_pkey")),
        sa.ForeignKeyConstraint(
            ["agreement_id"],
            ["agreements.id"],
            name=op.f("risk_snapshots_agreement_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["risk_policy_versions.id"],
            name=op.f("risk_snapshots_policy_version_id_fkey"),
            ondelete="SET NULL",
        ),
    )
    _org_fk("risk_snapshots")
    op.create_index(
        op.f("ix_risk_snapshots_organization_id"), "risk_snapshots", ["organization_id"]
    )
    op.create_index(
        op.f("ix_risk_snapshots_agreement_id"), "risk_snapshots", ["agreement_id"]
    )

    # --- external party metadata (§3.20.28 evidence requests) ----------------
    op.add_column(
        "external_parties",
        sa.Column(
            "party_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("external_parties", "party_metadata")
    op.drop_table("risk_snapshots")
    op.drop_table("risk_policy_versions")
    op.drop_table("obligation_exceptions")
    op.drop_table("workspace_lifecycle_configs")
    op.drop_column("notification_preferences", "quiet_hours_end")
    op.drop_column("notification_preferences", "quiet_hours_start")
    op.drop_table("email_templates")
    op.drop_table("notification_deliveries")
    op.drop_table("negotiation_deadlocks")
    op.drop_table("clause_playbooks")
    op.drop_column("agreement_change_items", "concession_value")
    op.drop_column("negotiation_actions", "concession_note")
    op.drop_column("negotiation_actions", "concession_value")
    op.drop_column("approval_records", "version_locked_at")
    op.drop_column("approval_records", "escalated_at")
    op.drop_column("approval_records", "stage_started_at")
    op.drop_column("approval_stages", "deadline_hours")
    op.drop_column("approval_stages", "minimum_approvals")
    op.drop_table("party_identifiers")
    op.drop_table("party_addresses")
    op.drop_table("party_contacts")
    op.drop_table("automation_checkpoints")
    op.drop_table("automation_executions")
    op.drop_table("automation_rules")
