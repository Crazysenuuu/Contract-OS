"""Monitoring enums (spec 3.15.4)."""

from enum import StrEnum


class IntegrationType(StrEnum):
    REST_API = "REST_API"
    WEBHOOK = "WEBHOOK"
    SFTP = "SFTP"
    EMAIL = "EMAIL"
    DATABASE = "DATABASE"
    OBJECT_STORAGE = "OBJECT_STORAGE"


class IntegrationStatus(StrEnum):
    CONNECTING = "CONNECTING"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"
    FAILED = "FAILED"


class MonitoringStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"


class ObservationStatus(StrEnum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class EvaluationResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvaluationStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ConnectorCapability(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    WEBHOOK = "WEBHOOK"


class CredentialStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    INVALID = "INVALID"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ExceptionStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class PauseReason(StrEnum):
    MANUAL = "MANUAL"
    STALE_VERSION = "STALE_VERSION"
    FAILURE_POLICY = "FAILURE_POLICY"


class AutomationAction(StrEnum):
    NO_ACTION = "NO_ACTION"
    MARK_TASK_READY = "MARK_TASK_READY"
    ATTACH_EVIDENCE = "ATTACH_EVIDENCE"
    COMPLETE_TASK = "COMPLETE_TASK"