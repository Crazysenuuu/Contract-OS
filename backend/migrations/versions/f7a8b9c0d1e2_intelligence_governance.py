"""AI governance layer: conversation memory, feedback, prompt/model registry

Revision ID: f7a8b9c0d1e2
Revises: 8421f73f77d9
Create Date: 2026-09-09

Adds the intelligence governance tables (spec 2.10.36–2.10.45):
  - intelligence_conversations / intelligence_messages (conversation memory)
  - intelligence_access_checks (re-authorization ledger, 2.10.37)
  - intelligence_feedback (2.10.38)
  - intelligence_evaluation_examples (2.10.40)
  - intelligence_configurations (2.10.43)
  - intelligence_prompt_versions (2.10.44)
  - intelligence_evaluation_runs (2.10.42)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "8421f73f77d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _base_columns() -> list:
    """id/created_at/updated_at shared by all governance tables."""
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


def upgrade() -> None:
    # Parents first: conversations, then the registries that messages
    # reference, then messages and the remaining children.
    op.create_table(
        "intelligence_conversations",
        *_base_columns(),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_intelligence_conversations_organization_id",
        "intelligence_conversations",
        ["organization_id"],
    )
    op.create_index(
        "ix_intelligence_conversations_created_by",
        "intelligence_conversations",
        ["created_by"],
    )
    op.create_index(
        "ix_intelligence_conversations_agreement_id",
        "intelligence_conversations",
        ["agreement_id"],
    )

    op.create_table(
        "intelligence_configurations",
        *_base_columns(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("llm_provider", sa.String(length=100), nullable=False),
        sa.Column("llm_model", sa.String(length=255), nullable=False),
        sa.Column("embedding_provider", sa.String(length=100), nullable=False),
        sa.Column("embedding_model", sa.String(length=255), nullable=False),
        sa.Column("retrieval_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_intelligence_configurations_active",
        "intelligence_configurations",
        ["active"],
    )

    op.create_table(
        "intelligence_prompt_versions",
        *_base_columns(),
        sa.Column("purpose", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("output_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "purpose", "version", name="uq_intelligence_prompt_purpose_version"
        ),
    )
    op.create_index(
        "ix_intelligence_prompt_purpose_active",
        "intelligence_prompt_versions",
        ["purpose", "active"],
    )

    op.create_table(
        "intelligence_messages",
        *_base_columns(),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("configuration_id", sa.UUID(), nullable=True),
        sa.Column("prompt_version_id", sa.UUID(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("answer_status", sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["intelligence_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["intelligence_configurations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_version_id"],
            ["intelligence_prompt_versions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_intelligence_messages_conversation_id",
        "intelligence_messages",
        ["conversation_id"],
    )

    op.create_table(
        "intelligence_feedback",
        *_base_columns(),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("rating", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("converted_example_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["message_id"], ["intelligence_messages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            "user_id",
            name="uq_intelligence_feedback_message_user",
        ),
    )
    op.create_index(
        "ix_intelligence_feedback_message_id",
        "intelligence_feedback",
        ["message_id"],
    )
    op.create_index(
        "ix_intelligence_feedback_user_id",
        "intelligence_feedback",
        ["user_id"],
    )

    op.create_table(
        "intelligence_evaluation_examples",
        *_base_columns(),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_behavior", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("source_feedback_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_intelligence_evaluation_examples_organization_id",
        "intelligence_evaluation_examples",
        ["organization_id"],
    )

    op.create_table(
        "intelligence_evaluation_runs",
        *_base_columns(),
        sa.Column("configuration_id", sa.UUID(), nullable=True),
        sa.Column("prompt_version_id", sa.UUID(), nullable=True),
        sa.Column("example_count", sa.Integer(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("triggered_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["intelligence_configurations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_version_id"],
            ["intelligence_prompt_versions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_intelligence_evaluation_runs_configuration_id",
        "intelligence_evaluation_runs",
        ["configuration_id"],
    )

    op.create_table(
        "intelligence_access_checks",
        *_base_columns(),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("agreement_id", sa.UUID(), nullable=True),
        sa.Column("accessible_agreement_count", sa.Integer(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("answer_status", sa.String(length=50), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["intelligence_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_intelligence_access_checks_conversation_id",
        "intelligence_access_checks",
        ["conversation_id"],
    )


def downgrade() -> None:
    # Strict child-first order (FK dependencies):
    #   feedback, runs, access_checks -> messages -> registries -> conversations
    op.drop_index(
        "ix_intelligence_feedback_user_id", table_name="intelligence_feedback"
    )
    op.drop_index(
        "ix_intelligence_feedback_message_id", table_name="intelligence_feedback"
    )
    op.drop_table("intelligence_feedback")

    op.drop_index(
        "ix_intelligence_evaluation_runs_configuration_id",
        table_name="intelligence_evaluation_runs",
    )
    op.drop_table("intelligence_evaluation_runs")

    op.drop_index(
        "ix_intelligence_access_checks_conversation_id",
        table_name="intelligence_access_checks",
    )
    op.drop_table("intelligence_access_checks")

    op.drop_index(
        "ix_intelligence_messages_conversation_id",
        table_name="intelligence_messages",
    )
    op.drop_table("intelligence_messages")

    op.drop_index(
        "ix_intelligence_prompt_purpose_active",
        table_name="intelligence_prompt_versions",
    )
    op.drop_table("intelligence_prompt_versions")

    op.drop_index(
        "ix_intelligence_configurations_active",
        table_name="intelligence_configurations",
    )
    op.drop_table("intelligence_configurations")

    op.drop_index(
        "ix_intelligence_evaluation_examples_organization_id",
        table_name="intelligence_evaluation_examples",
    )
    op.drop_table("intelligence_evaluation_examples")

    op.drop_index(
        "ix_intelligence_conversations_agreement_id",
        table_name="intelligence_conversations",
    )
    op.drop_index(
        "ix_intelligence_conversations_created_by",
        table_name="intelligence_conversations",
    )
    op.drop_index(
        "ix_intelligence_conversations_organization_id",
        table_name="intelligence_conversations",
    )
    op.drop_table("intelligence_conversations")
