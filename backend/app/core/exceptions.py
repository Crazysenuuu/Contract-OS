"""Exception taxonomy (spec 1.23.25).

Stable, machine-readable error codes so clients never parse free-text
messages. Every platform error carries: a machine code, an HTTP status, a
human message and optional structured details.

    VALIDATION_ERROR        -> 422
    NOT_FOUND               -> 404
    UNAUTHENTICATED         -> 401
    FORBIDDEN               -> 403
    VERSION_CONFLICT        -> 409
    WORKFLOW_STATE_ERROR    -> 409
    RESOURCE_LOCKED         -> 423
    INVITATION_EXPIRED      -> 410
    RATE_LIMITED            -> 429
    ENTITLEMENT_REQUIRED    -> 402
    DEPENDENCY_FAILURE      -> 503
    INTERNAL_ERROR          -> 500
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all platform errors."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None):
        self.message = message or self.__class__.__name__
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    status_code = 422


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404


class UnauthenticatedError(AppError):
    code = "UNAUTHENTICATED"
    status_code = 401


class ForbiddenError(AppError):
    code = "FORBIDDEN"
    status_code = 403


class InsufficientPermissionError(ForbiddenError):
    code = "INSUFFICIENT_PERMISSION"


class VersionConflictError(AppError):
    code = "VERSION_CONFLICT"
    status_code = 409


class WorkflowStateError(AppError):
    code = "WORKFLOW_STATE_ERROR"
    status_code = 409


class InvitationExpiredError(AppError):
    code = "INVITATION_EXPIRED"
    status_code = 410


class ResourceLockedError(AppError):
    code = "RESOURCE_LOCKED"
    status_code = 423


class RateLimitedError(AppError):
    code = "RATE_LIMITED"
    status_code = 429


class EntitlementRequiredError(AppError):
    code = "ENTITLEMENT_REQUIRED"
    status_code = 402


class DependencyFailureError(AppError):
    """A downstream dependency (DB, storage, AI provider, email) failed."""

    code = "DEPENDENCY_FAILURE"
    status_code = 503
