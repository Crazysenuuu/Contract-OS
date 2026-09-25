"""Monitoring models (spec 3.15).

Tables:
  - integration_connections      workspace-scoped external data sources
  - integration_credentials      secret references (never inline secrets)
  - integration_health           connector health counters
  - obligation_monitoring        monitoring rule bound to an obligation
  - external_observations        normalized, hashed external observations
- monitoring_evaluations       immutable evaluation history
   - monitoring_exceptions        operational exceptions (not contract breaches)
   - monitoring_runs              idempotent scheduler runs
   - monitoring_evidence          traceable evidence records (spec 3.15.37-38)
   - monitoring_webhook_events    raw verified webhook events (replay-protected)

Credentials are referenced via ``secret_reference`` (which points at a real
secret manager / env binding) and are never returned by the API (3.15.6).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

from .enums import (
    CredentialStatus,
    EvaluationResult,
    EvaluationStatus,
    IntegrationStatus,
    IntegrationType,
    MonitoringStatus,
    ObservationStatus,
)


class IntegrationConnection(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A workspace-scoped monitoring integration (spec 3.15.5)."""

    __tablename__ = "integration_connections"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    integration_type: Mapped[IntegrationType] = mapped_column(
        String(64),
        nullable=False,
    )

    provider_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    status: Mapped[IntegrationStatus] = mapped_column(
        String(32),
        nullable=False,
        default=IntegrationStatus.CONNECTING.value,
    )

    # Transport-level configuration only. Never contains access tokens —
    # those live in IntegrationCredential via a secret reference.
    configuration: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    # Failure policy (spec 3.15.27): e.g.
    # {"failure_policy": {"max_consecutive_failures": 5, "action": "PAUSE_MONITORING"}}
    # is stored inside ``configuration`` so the workspace controls it.

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    created_by_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    credentials = relationship(
        "IntegrationCredential",
        back_populates="integration",
        cascade="all, delete-orphan",
    )

    health = relationship(
        "IntegrationHealth",
        back_populates="integration",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    __table_args__ = (
        Index(
            "ix_integration_connections_org_status",
            "organization_id",
            "status",
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            name="uq_integration_connections_org_id",
        ),
    )


class IntegrationCredential(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Credential record for a monitoring integration (spec 3.15.6)."""

    __tablename__ = "integration_credentials"

    integration_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("integration_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Pointer into a secret manager / env binding, e.g.
    # "env://MONITORING_SFTP_PASSPHRASE" or "secretman://aws/sm/contractos/monitor-sap".
    # The referenced value is resolved by app.monitoring.credentials and is
    # never stored inline nor returned through the API.
    secret_reference: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    status: Mapped[CredentialStatus] = mapped_column(
        String(32),
        nullable=False,
        default=CredentialStatus.ACTIVE.value,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    integration = relationship(
        "IntegrationConnection",
        back_populates="credentials",
    )


class IntegrationHealth(
    TimestampMixin,
    Base,
):
    """Connector health counters (spec 3.15.26)."""

    __tablename__ = "integration_health"

    integration_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("integration_connections.id", ondelete="CASCADE"),
        primary_key=True,
    )

    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    last_latency_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    last_error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    integration = relationship(
        "IntegrationConnection",
        back_populates="health",
    )


class ObligationMonitoring(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Monitoring definition bound to an obligation (spec 3.15.7-3.15.8)."""

    __tablename__ = "obligation_monitoring"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    obligation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("obligations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    source_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agreement_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("integration_connections.id", ondelete="RESTRICT"),
        nullable=False,
    )

    status: Mapped[MonitoringStatus] = mapped_column(
        String(32),
        nullable=False,
        default=MonitoringStatus.DRAFT.value,
    )

    pause_reason: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    query_definition: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    evaluation_definition: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    schedule_definition: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    # Conservative automation config (spec 3.15.36):
    # {"on_pass": "NO_ACTION"} | "MARK_TASK_READY" | "ATTACH_EVIDENCE" | "COMPLETE_TASK"
    automation: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # External data retention (spec 3.15.45): days raw observations/events
    # are kept for this rule, and which payload fields are redacted before
    # storage. None = keep forever / no field redaction (workspace-managed).
    retention_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    redact_fields: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_result: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    __table_args__ = (
        Index(
            "ix_obligation_monitoring_due",
            "status",
            "next_run_at",
        ),
        Index(
            "ix_obligation_monitoring_obligation",
            "obligation_id",
            "status",
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            name="uq_obligation_monitoring_org_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "integration_id"],
            ["integration_connections.organization_id", "integration_connections.id"],
            name="fk_obligation_monitoring_integration_org",
            ondelete="RESTRICT",
        ),
    )


class ExternalObservationRecord(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A preserved external observation (spec 3.15.12)."""

    __tablename__ = "external_observations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("integration_connections.id", ondelete="RESTRICT"),
        nullable=False,
    )

    monitoring_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("obligation_monitoring.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    external_id: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    resource_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    source_reference: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    # Hash over the *original* payload (spec 3.15.13) so tampering with the
    # stored payload is detectable, even after redaction for storage.
    payload_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    status: Mapped[ObservationStatus] = mapped_column(
        String(32),
        nullable=False,
        default=ObservationStatus.VALIDATED.value,
    )

    __table_args__ = (
        Index(
            "ix_external_observations_lookup",
            "organization_id",
            "monitoring_id",
            "observed_at",
        ),
        UniqueConstraint(
            "integration_id",
            "monitoring_id",
            "external_id",
            "observed_at",
            "payload_hash",
            name="uq_external_observations_dedup",
        ),
        ForeignKeyConstraint(
            ["organization_id", "integration_id"],
            ["integration_connections.organization_id", "integration_connections.id"],
            name="fk_external_observations_integration_org",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "monitoring_id"],
            ["obligation_monitoring.organization_id", "obligation_monitoring.id"],
            name="fk_external_observations_monitoring_org",
            ondelete="RESTRICT",
        ),
    )


class MonitoringEvaluation(
    UUIDPrimaryKeyMixin,
    Base,
):
    """Immutable evaluation history (spec 3.15.20-3.15.21)."""

    __tablename__ = "monitoring_evaluations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    monitoring_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("obligation_monitoring.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # The obligation-instances table does not exist yet (spec 3.13 rolling
    # out later); the column is reserved and nullable so the provenance
    # chain stays compatible. For now evaluations anchor to the obligation
    # via monitoring.obligation_id.
    obligation_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )

    evaluation_definition_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    result: Mapped[EvaluationResult] = mapped_column(
        String(32),
        nullable=False,
    )

    status: Mapped[EvaluationStatus] = mapped_column(
        String(32),
        nullable=False,
        default=EvaluationStatus.COMPLETED.value,
    )

    metrics: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    observation_ids: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Failure metadata: when the result is INCONCLUSIVE this explains why
    # without ever minting a FAIL (spec 3.15.25).
    details: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    __table_args__ = (
        Index(
            "ix_monitoring_evaluations_lookup",
            "monitoring_id",
            "evaluated_at",
        ),
        ForeignKeyConstraint(
            ["organization_id", "monitoring_id"],
            ["obligation_monitoring.organization_id", "obligation_monitoring.id"],
            name="fk_monitoring_evaluations_monitoring_org",
            ondelete="CASCADE",
        ),
    )


class MonitoringException(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Operational exception opened on FAIL (spec 3.15.24).

    A FAIL is an operational exception for human review — never an automatic
    "contract breach" declaration (3.15.23).
    """

    __tablename__ = "monitoring_exceptions"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    monitoring_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("obligation_monitoring.id", ondelete="CASCADE"),
        nullable=False,
    )

    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_evaluations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="OPEN",
    )

    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    details: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    resolution_comment: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    __table_args__ = (
        Index(
            "ix_monitoring_exceptions_status",
            "status",
            "created_at",
        ),
        ForeignKeyConstraint(
            ["organization_id", "monitoring_id"],
            ["obligation_monitoring.organization_id", "obligation_monitoring.id"],
            name="fk_monitoring_exceptions_monitoring_org",
            ondelete="CASCADE",
        ),
    )


class MonitoringRun(
    UUIDPrimaryKeyMixin,
    Base,
):
    """Idempotent scheduler run (spec 3.15.33-3.15.34)."""

    __tablename__ = "monitoring_runs"

    monitoring_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("obligation_monitoring.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # monitoring_id + scheduled_period — a repeated worker invocation must
    # not create another logical monitoring result.
    idempotency_key: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        unique=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="PENDING",
    )

    scheduled_period: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    error: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "monitoring_id"],
            ["obligation_monitoring.organization_id", "obligation_monitoring.id"],
            name="fk_monitoring_runs_monitoring_org",
            ondelete="CASCADE",
        ),
    )


class MonitoringEvidence(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Traceable evidence derived from real external observations (3.15.37).

    Every evaluation is backed by evidence records so the chain
    ``External Observation → Evidence Record → Obligation`` is reconstructable:

      - source / source_identifier   which provider + external record
      - observed_at / received_at    when the source saw it vs when we got it
      - payload_hash (+value.payload_hash)  tamper-evident digest (3.15.38)
      - integration_id / monitoring_run_id  full provenance back to a run

    ``obligation_id`` anchors the evidence to the obligation the monitoring is
    bound to (the obligation operates as the instance surrogate until the
    3.13 ``obligation_instances`` table lands; the column is nullable so the
    schema stays forward compatible). Webhook-ingested evidence has no
    ``monitoring_run_id`` because a webhook does not mint a run.
    """

    __tablename__ = "monitoring_evidence"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    monitoring_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("obligation_monitoring.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("integration_connections.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    monitoring_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    obligation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("obligations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    evidence_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="SYSTEM_RECORD",
    )

    source: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    source_identifier: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    payload_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    value: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    attached_to_obligation: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    __table_args__ = (
        Index(
            "ix_monitoring_evidence_lookup",
            "organization_id",
            "monitoring_id",
            "received_at",
        ),
        ForeignKeyConstraint(
            ["organization_id", "monitoring_id"],
            ["obligation_monitoring.organization_id", "obligation_monitoring.id"],
            name="fk_monitoring_evidence_monitoring_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "integration_id"],
            ["integration_connections.organization_id", "integration_connections.id"],
            name="fk_monitoring_evidence_integration_org",
            ondelete="RESTRICT",
        ),
    )


class MonitoringWebhookEvent(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Raw, verified webhook delivery (spec 3.15.28-3.15.30).

    Persists the raw body, signature and provider replay metadata BEFORE
    normalization so that replays and tampering are provably rejected.
    """

    __tablename__ = "monitoring_webhook_events"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("integration_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider_event_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    signature: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    payload_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    payload: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="RECEIVED",
        # 'RECEIVED' | 'ACCEPTED' | 'REJECTED' | 'DUPLICATE'
    )

    __table_args__ = (
        UniqueConstraint(
            "integration_id",
            "provider_event_id",
            "payload_hash",
            name="uq_monitoring_webhook_dedup",
        ),
        ForeignKeyConstraint(
            ["organization_id", "integration_id"],
            ["integration_connections.organization_id", "integration_connections.id"],
            name="fk_monitoring_webhook_events_integration_org",
            ondelete="CASCADE",
        ),
    )


class MonitoringWorkspacePolicy(
    Base,
):
    """Workspace-selected automation policy (spec 3.15.36).

    The workspace chooses which ``automation.on_pass`` actions may be used on
    its monitoring rules (NO_ACTION / MARK_TASK_READY / ATTACH_EVIDENCE /
    COMPLETE_TASK). With no row configured, every action is permitted — the
    conservative default is configuration, not a hardcoded policy.
    """

    __tablename__ = "monitoring_workspace_policies"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )

    allowed_actions: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


__all__ = [
    "IntegrationConnection",
    "IntegrationCredential",
    "IntegrationHealth",
    "ObligationMonitoring",
    "ExternalObservationRecord",
    "MonitoringEvaluation",
    "MonitoringException",
    "MonitoringRun",
    "MonitoringEvidence",
    "MonitoringWebhookEvent",
    "MonitoringWorkspacePolicy",
]