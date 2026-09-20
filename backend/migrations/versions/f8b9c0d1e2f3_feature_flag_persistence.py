"""Feature flag persistence: DB-backed flags and user overrides.

The FeatureFlagService previously kept flags in process memory, so flag
changes were lost on restart and invisible across workers. This migration
creates feature_flags / feature_flag_overrides and seeds the default flag
set that the service used to initialize in-memory.
"""

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f8b9c0d1e2f3"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None

# name, description, type, enabled, percentage, rollout_pct, tags
DEFAULT_FLAGS = [
    ("ai_analysis", "Enable AI-powered contract analysis", "boolean", True, 0.0, 0.0, "ai,core"),
    ("compliance_engine", "Enable compliance checking engine", "boolean", True, 0.0, 0.0, "compliance"),
    ("translation_queue", "Enable automated translation queue", "boolean", True, 0.0, 0.0, "i18n"),
    ("canary_deployments", "Enable canary deployment features", "percentage", False, 50.0, 0.0, "deployment"),
    ("advanced_analytics", "Enable advanced analytics dashboard", "gradual_rollout", False, 0.0, 25.0, "analytics,new"),
    ("bulk_operations", "Enable bulk operations for agreements", "boolean", True, 0.0, 0.0, "operations"),
    ("clause_library", "Enable clause library feature", "boolean", True, 0.0, 0.0, "clauses"),
    ("esignature_integration", "Enable e-signature provider integration", "user_segment", False, 0.0, 0.0, "esignature,enterprise"),
]


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("flag_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("percentage", sa.Float(), nullable=False),
        sa.Column("allowed_users", postgresql.JSONB(), nullable=False),
        sa.Column("allowed_groups", postgresql.JSONB(), nullable=False),
        sa.Column("denied_users", postgresql.JSONB(), nullable=False),
        sa.Column("rollout_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rollout_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rollout_percentage", sa.Float(), nullable=False),
        sa.Column("is_kill_switch", sa.Boolean(), nullable=False),
        sa.Column("environments", postgresql.JSONB(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
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
    )
    op.create_index("ix_feature_flags_name", "feature_flags", ["name"], unique=True)

    op.create_table(
        "feature_flag_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "flag_name",
            sa.String(255),
            sa.ForeignKey("feature_flags.name", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
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
        sa.UniqueConstraint(
            "flag_name", "user_id", name="uq_feature_flag_overrides_flag_user"
        ),
    )
    op.create_index(
        "ix_feature_flag_overrides_flag_name",
        "feature_flag_overrides",
        ["flag_name"],
    )
    op.create_index(
        "ix_feature_flag_overrides_user_id",
        "feature_flag_overrides",
        ["user_id"],
    )

    flags_table = sa.table(
        "feature_flags",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("flag_type", sa.String),
        sa.column("status", sa.String),
        sa.column("enabled", sa.Boolean),
        sa.column("percentage", sa.Float),
        sa.column("allowed_users", postgresql.JSONB()),
        sa.column("allowed_groups", postgresql.JSONB()),
        sa.column("denied_users", postgresql.JSONB()),
        sa.column("rollout_percentage", sa.Float),
        sa.column("is_kill_switch", sa.Boolean),
        sa.column("environments", postgresql.JSONB()),
        sa.column("tags", postgresql.JSONB()),
    )
    op.bulk_insert(
        flags_table,
        [
            {
                # bulk_insert is core-level: ORM python defaults (uuid pk)
                # never run, so ids must be supplied explicitly.
                "id": uuid.uuid4(),
                "name": name,
                "description": description,
                "flag_type": flag_type,
                "status": "active",
                "enabled": enabled,
                "percentage": percentage,
                "allowed_users": [],
                "allowed_groups": ["enterprise", "beta_testers"]
                if flag_type == "user_segment"
                else [],
                "denied_users": [],
                "rollout_percentage": rollout_pct,
                "is_kill_switch": False,
                "environments": {},
                "tags": tags.split(","),
            }
            for name, description, flag_type, enabled, percentage, rollout_pct, tags in DEFAULT_FLAGS
        ],
    )


def downgrade() -> None:
    op.drop_table("feature_flag_overrides")
    op.drop_table("feature_flags")
