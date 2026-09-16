"""AI governance models (spec 2.10.36–2.10.45).

Conversation memory, user feedback, evaluation dataset, and the
model/prompt version registries that make every AI answer traceable to
the exact configuration that produced it.

Security boundaries enforced here and in the service layer:
  - Conversation history never resurrects access (2.10.37): every query
    re-authorizes against current access before retrieval; citations are
    re-verified at answer time, never replayed from stored history.
  - Feedback never mutates production models (2.10.39): it only feeds the
    evaluation dataset for offline review.
  - Configuration/prompt rows are append-only in practice: activating a
    version deactivates siblings rather than mutating history (2.10.43/44).
"""

import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

# Allowed feedback ratings (spec 2.10.38).
FEEDBACK_RATINGS = {
    "helpful",
    "not_helpful",
    "incorrect",
    "missing_source",
    "wrong_source",
    "incomplete",
}

# Roles allowed on IntelligenceMessage.role.
MESSAGE_ROLES = {"user", "assistant"}


class IntelligenceConversation(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A contract Q&A conversation (spec 2.10.36).

    Optionally scoped to one agreement ("ask this agreement"), otherwise
    organization-wide. Membership/access is enforced at query time from
    current state — a stored conversation grants nothing by itself.
    """

    __tablename__ = "intelligence_conversations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        # Soft reference to the agreement under discussion; access is
        # re-checked per query (2.10.37), so no hard FK cascade is wanted.
    )

    title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )


class IntelligenceMessage(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """One turn in an intelligence conversation (spec 2.10.36).

    Assistant messages persist their citations, the configuration and
    prompt version that produced them (traceability, 2.10.43/44), and
    basic run telemetry (latency / token usage, 2.10.45).
    """

    __tablename__ = "intelligence_messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "intelligence_conversations.id", ondelete="CASCADE"
        ),
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        # 'user' | 'assistant'
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    citations: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    # Traceability: exact configuration + prompt that produced this answer.
    configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("intelligence_configurations.id", ondelete="SET NULL"),
        nullable=True,
    )

    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("intelligence_prompt_versions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Run telemetry (2.10.45). No chain-of-thought is ever stored (2.10.68).
    latency_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    tokens_in: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    tokens_out: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # 'ANSWERED' | 'INSUFFICIENT_EVIDENCE' | 'REQUIRES_HUMAN_REVIEW'
    answer_status: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )


class IntelligenceFeedback(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """User feedback on an assistant message (spec 2.10.38).

    Feedback flows only into the evaluation dataset for offline review —
    it never reconfigures or retrain production models directly
    (spec 2.10.39).
    """

    __tablename__ = "intelligence_feedback"

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("intelligence_messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    rating: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # helpful | not_helpful | incorrect | missing_source |
        # wrong_source | incomplete
    )

    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Set when this feedback has been promoted into the evaluation
    # dataset; None means it is still awaiting review (2.10.39).
    converted_example_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    __table_args__ = (
        # One rating per user per message.
        UniqueConstraint(
            "message_id", "user_id", name="uq_intelligence_feedback_message_user"
        ),
    )


class IntelligenceEvaluationExample(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Evaluation dataset example (spec 2.10.40).

    Built from reviewed feedback and authorized, de-identified data only.
    Consumed by the offline evaluation pipeline; never used to retrain
    production models directly.
    """

    __tablename__ = "intelligence_evaluation_examples"

    question: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    expected_behavior: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        # e.g. {"answer_status": "ANSWERED", "must_refuse": false,
        #        "expected_citation_quotes": [...]}
    )

    source_ids: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        # Agreement/version IDs the expected answer must cite.
    )

    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        # 'citation_validity' | 'refusal_accuracy' | 'conflict_detection' | ...
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        # None = global/shared example.
    )

    source_feedback_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        # Provenance: feedback row this example was promoted from.
    )


class IntelligenceConfiguration(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Model + retrieval configuration registry (spec 2.10.43).

    Exactly one configuration should be active at a time; activating one
    deactivates the rest (enforced in the service). Every assistant
    message references the configuration that produced it.
    """

    __tablename__ = "intelligence_configurations"

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    llm_provider: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    llm_model: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    embedding_provider: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="hash-bow-256",
    )

    embedding_model: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="hash-bow-256",
    )

    retrieval_config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        # e.g. {"limit": 8, "min_score": 0.05, "hybrid_boost": 0.2}
    )

    prompt_config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        # Maps purpose -> prompt version string, e.g.
        # {"ask": "v3", "summarize": "v2"}
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )


class IntelligencePromptVersion(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Versioned system prompts (spec 2.10.44).

    Prompts are production configuration, stored as data with the exact
    text and expected output schema — never buried in random Python
    strings.
    """

    __tablename__ = "intelligence_prompt_versions"

    purpose: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        # 'ask' | 'summarize' | 'compare' | 'negotiation_insight' | ...
    )

    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    system_prompt: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    output_schema: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "purpose", "version", name="uq_intelligence_prompt_purpose_version"
        ),
        Index(
            "ix_intelligence_prompt_purpose_active",
            "purpose",
            "active",
        ),
    )


class IntelligenceEvaluationRun(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """One offline evaluation pipeline execution (spec 2.10.42/41).

    Stores aggregate metrics (citation validity, unsupported claim rate,
    refusal accuracy, latency, ...) so candidate configurations can be
    compared before an approved deployment (2.10.39's final gate).
    """

    __tablename__ = "intelligence_evaluation_runs"

    configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("intelligence_configurations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("intelligence_prompt_versions.id", ondelete="SET NULL"),
        nullable=True,
    )

    example_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    metrics: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        # {"unsupported_claim_rate": 0.0, "citation_validity": 0.98, ...}
    )

    passed: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        # None = run not judged yet.
    )

    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class IntelligenceAccessCheck(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Re-authorization ledger for conversation queries (spec 2.10.37).

    One row per assistant turn recording that access was re-verified at
    answer time against current agreement access — the audit trail
    proving conversation history did not resurrect access.
    """

    __tablename__ = "intelligence_access_checks"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("intelligence_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    accessible_agreement_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    evidence_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Current answer status at re-check time.
    answer_status: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    latency_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )


# Latency metric for evaluation runs (used by the evaluation pipeline).
LATENCY_METRIC = "latency_p50_ms"

# Confidence floor for float-typed metrics stored in IntelligenceEvaluationRun.
MIN_METRIC_VALUE = 0.0
MAX_METRIC_VALUE = 1.0

__all__ = [
    "IntelligenceConversation",
    "IntelligenceMessage",
    "IntelligenceFeedback",
    "IntelligenceEvaluationExample",
    "IntelligenceConfiguration",
    "IntelligencePromptVersion",
    "IntelligenceEvaluationRun",
    "IntelligenceAccessCheck",
    "FEEDBACK_RATINGS",
    "MESSAGE_ROLES",
    "LATENCY_METRIC",
    "MIN_METRIC_VALUE",
    "MAX_METRIC_VALUE",
]
