"""API schemas for spec 3.15 monitoring."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class IntegrationCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    integration_type: str = Field(..., pattern="^(REST_API|WEBHOOK|SFTP|EMAIL|DATABASE|OBJECT_STORAGE)$")
    provider_key: str = Field(..., min_length=1, max_length=255)
    configuration: dict = Field(default_factory=dict)
    secret_references: list[str] = Field(default_factory=list)


class IntegrationUpdateRequest(BaseModel):
    name: str | None = None
    configuration: dict | None = None
    status: str | None = Field(
        default=None,
        pattern="^(CONNECTING|ACTIVE|DEGRADED|DISCONNECTED|FAILED)$",
    )


class IntegrationHealthOut(BaseModel):
    integration_id: UUID
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = 0
    last_latency_ms: int | None = None
    last_error_code: str | None = None
    last_error: str | None = None


class IntegrationOut(BaseModel):
    id: UUID
    name: str
    integration_type: str
    provider_key: str
    status: str
    configuration: dict
    created_at: datetime | None = None
    health: IntegrationHealthOut | None = None


class CredentialCreateRequest(BaseModel):
    secret_reference: str = Field(..., min_length=1, max_length=500)
    expires_at: datetime | None = None


class CredentialOut(BaseModel):
    id: UUID
    integration_id: UUID
    secret_reference: str
    status: str
    expires_at: datetime | None = None
    last_rotated_at: datetime | None = None
    created_at: datetime | None = None


class RuleCreateRequest(BaseModel):
    obligation_id: UUID
    integration_id: UUID
    source_version_id: UUID
    query_definition: dict = Field(default_factory=dict)
    evaluation_definition: dict = Field(default_factory=dict)
    schedule_definition: dict = Field(default_factory=dict)
    automation: dict | None = None
    status: str = Field(default="DRAFT", pattern="^(DRAFT|ACTIVE|PAUSED|DISABLED)$")


class RuleUpdateRequest(BaseModel):
    query_definition: dict | None = None
    evaluation_definition: dict | None = None
    schedule_definition: dict | None = None
    automation: dict | None = None


class RuleOut(BaseModel):
    id: UUID
    obligation_id: UUID
    integration_id: UUID
    source_version_id: UUID
    status: str
    pause_reason: str | None = None
    query_definition: dict
    evaluation_definition: dict
    schedule_definition: dict
    automation: dict | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_result: str | None = None
    created_at: datetime | None = None


class EvaluationOut(BaseModel):
    id: UUID
    monitoring_id: UUID
    result: str
    status: str
    metrics: dict
    observation_ids: list
    evaluated_at: datetime
    details: dict | None = None


class ObservationOut(BaseModel):
    id: UUID
    integration_id: UUID
    monitoring_id: UUID
    external_id: str
    resource_type: str
    observed_at: datetime
    payload: dict
    payload_hash: str
    status: str


class ExceptionOut(BaseModel):
    id: UUID
    monitoring_id: UUID
    evaluation_id: UUID
    status: str
    reason: str
    resolved_at: datetime | None = None
    resolution_comment: str | None = None


class RunRuleResponse(BaseModel):
    evaluation_id: UUID
    result: str


class WebhookAck(BaseModel):
    received: bool = True
    duplicate: bool = False
    observations: int = 0


class WorkspacePolicyUpdate(BaseModel):
    """Workspace-selected automation actions (spec 3.15.36)."""

    allowed_automation_actions: list[str] = Field(default_factory=list)


class WorkspaceAutomationPolicyOut(BaseModel):
    allowed_automation_actions: list[str]