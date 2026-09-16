"""Canonical agreement domain states (spec 1.25.1).

One authoritative state machine for the agreement lifecycle. This module is
the single definition of which statuses exist; the transition table here is
the fallback/validation reference, while runtime transitions flow through
the data-driven rules in ``lifecycle_service`` (which supports per-type
overrides). No service may invent its own status names.
"""

from enum import StrEnum


class AgreementStatus(StrEnum):
    DRAFT = "draft"
    NEGOTIATION = "negotiation"
    NEGOTIATION_COMPLETE = "negotiation_complete"
    SIGNING_PENDING = "signing_pending"
    SIGNING = "signing"
    EXECUTED = "executed"
    EXECUTED_PENDING_EFFECTIVE_DATE = "executed_pending_effective_date"
    ACTIVE = "active"
    EXPIRING = "expiring"
    RENEWED = "renewed"
    EXPIRED = "expired"
    TERMINATION_REQUESTED = "termination_requested"
    TERMINATING = "terminating"
    TERMINATED = "terminated"
    CANCELLED = "cancelled"


VALID_TRANSITIONS: dict[str, set[str]] = {
    AgreementStatus.DRAFT: {
        AgreementStatus.NEGOTIATION,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.NEGOTIATION: {
        AgreementStatus.NEGOTIATION,
        AgreementStatus.NEGOTIATION_COMPLETE,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.NEGOTIATION_COMPLETE: {
        AgreementStatus.SIGNING_PENDING,
        AgreementStatus.NEGOTIATION,
    },
    AgreementStatus.SIGNING_PENDING: {
        AgreementStatus.SIGNING,
        AgreementStatus.NEGOTIATION,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.SIGNING: {
        AgreementStatus.EXECUTED,
        AgreementStatus.SIGNING,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.EXECUTED: {
        AgreementStatus.EXECUTED_PENDING_EFFECTIVE_DATE,
        AgreementStatus.ACTIVE,
    },
    AgreementStatus.EXECUTED_PENDING_EFFECTIVE_DATE: {
        AgreementStatus.ACTIVE,
    },
    AgreementStatus.ACTIVE: {
        AgreementStatus.EXPIRING,
        AgreementStatus.TERMINATION_REQUESTED,
    },
    AgreementStatus.EXPIRING: {
        AgreementStatus.ACTIVE,
        AgreementStatus.RENEWED,
        AgreementStatus.EXPIRED,
        AgreementStatus.TERMINATION_REQUESTED,
    },
    AgreementStatus.RENEWED: {
        AgreementStatus.ACTIVE,
    },
    AgreementStatus.TERMINATION_REQUESTED: {
        AgreementStatus.TERMINATING,
        AgreementStatus.ACTIVE,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.TERMINATING: {
        AgreementStatus.TERMINATED,
        AgreementStatus.ACTIVE,
    },
    AgreementStatus.TERMINATED: set(),
    AgreementStatus.EXPIRED: set(),
    AgreementStatus.CANCELLED: set(),
}


def validate_transition(current_status: str, new_status: str) -> None:
    """Validate a status change against the canonical state machine.

    Raises InvalidAgreementTransition when the transition is not permitted.
    """
    allowed = VALID_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        raise InvalidAgreementTransition(
            f"Invalid agreement state transition: {current_status} -> {new_status}"
        )


class InvalidAgreementTransition(Exception):
    """Raised when a status change violates the canonical state machine."""
