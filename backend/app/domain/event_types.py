"""Event type catalogue (spec §62).

Defines the 18 canonical event types for the contract lifecycle.
Every event in the system should be one of these types (or an extension
that's explicitly registered).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventType:
    key: str
    label: str
    description: str
    category: str  # lifecycle | signature | obligation | amendment | system


# --- Lifecycle events (spec §62) -------------------------------------------

AGREEMENT_CREATED = EventType(
    key="agreement.created",
    label="Agreement Created",
    description="A new agreement draft has been created",
    category="lifecycle",
)

AGREEMENT_UPDATED = EventType(
    key="agreement.updated",
    label="Agreement Updated",
    description="Agreement data or answers have been modified",
    category="lifecycle",
)

AGREEMENT_SUBMITTED = EventType(
    key="agreement.submitted",
    label="Agreement Submitted",
    description="Agreement submitted for review/approval",
    category="lifecycle",
)

AGREEMENT_APPROVED = EventType(
    key="agreement.approved",
    label="Agreement Approved",
    description="Agreement has been approved through the approval chain",
    category="lifecycle",
)

AGREEMENT_REJECTED = EventType(
    key="agreement.rejected",
    label="Agreement Rejected",
    description="Agreement has been rejected during review",
    category="lifecycle",
)

AGREEMENT_SENT = EventType(
    key="agreement.sent",
    label="Agreement Sent",
    description="Agreement sent to counterparty for review/signing",
    category="lifecycle",
)

AGREEMENT_VIEWED = EventType(
    key="agreement.viewed",
    label="Agreement Viewed",
    description="Counterparty opened the agreement review link",
    category="lifecycle",
)

NEGOTIATION_STARTED = EventType(
    key="agreement.negotiation_started",
    label="Negotiation Started",
    description="Counterparty requested a change to the agreement",
    category="lifecycle",
)

VERSION_CREATED = EventType(
    key="agreement.version_created",
    label="Version Created",
    description="A new version of the agreement has been created",
    category="lifecycle",
)

# --- Signature events -------------------------------------------------------

SIGNATURE_REQUESTED = EventType(
    key="signature.requested",
    label="Signature Requested",
    description="A signature request has been sent to a signer",
    category="signature",
)

SIGNATURE_COMPLETED = EventType(
    key="signature.completed",
    label="Signature Completed",
    description="A signer has completed their signature",
    category="signature",
)

AGREEMENT_EXECUTED = EventType(
    key="agreement.executed",
    label="Agreement Executed",
    description="All required signatures collected, agreement is now executed",
    category="signature",
)

# --- Obligation events ------------------------------------------------------

OBLIGATION_CREATED = EventType(
    key="obligation.created",
    label="Obligation Created",
    description="A new obligation has been extracted or manually created",
    category="obligation",
)

OBLIGATION_COMPLETED = EventType(
    key="obligation.completed",
    label="Obligation Completed",
    description="An obligation has been fulfilled",
    category="obligation",
)

# --- Amendment / renewal / termination events -------------------------------

RENEWAL_APPROACHING = EventType(
    key="renewal.approaching",
    label="Renewal Approaching",
    description="A renewal decision deadline is approaching",
    category="amendment",
)

AGREEMENT_RENEWED = EventType(
    key="agreement.renewed",
    label="Agreement Renewed",
    description="The agreement has been renewed for another term",
    category="amendment",
)

AMENDMENT_CREATED = EventType(
    key="amendment.created",
    label="Amendment Created",
    description="An amendment to the agreement has been created",
    category="amendment",
)

AGREEMENT_TERMINATED = EventType(
    key="agreement.terminated",
    label="Agreement Terminated",
    description="The agreement has been terminated",
    category="amendment",
)


# --- Registry ---------------------------------------------------------------

ALL_EVENT_TYPES: dict[str, EventType] = {
    et.key: et
    for et in [
        AGREEMENT_CREATED,
        AGREEMENT_UPDATED,
        AGREEMENT_SUBMITTED,
        AGREEMENT_APPROVED,
        AGREEMENT_REJECTED,
        AGREEMENT_SENT,
        AGREEMENT_VIEWED,
        NEGOTIATION_STARTED,
        VERSION_CREATED,
        SIGNATURE_REQUESTED,
        SIGNATURE_COMPLETED,
        AGREEMENT_EXECUTED,
        OBLIGATION_CREATED,
        OBLIGATION_COMPLETED,
        RENEWAL_APPROACHING,
        AGREEMENT_RENEWED,
        AMENDMENT_CREATED,
        AGREEMENT_TERMINATED,
    ]
}


def validate_event_type(key: str) -> EventType:
    """Return the EventType for *key*, raising ValueError if unknown."""
    if key not in ALL_EVENT_TYPES:
        raise ValueError(
            f"Unknown event type '{key}'. "
            f"Valid types: {', '.join(sorted(ALL_EVENT_TYPES.keys()))}"
        )
    return ALL_EVENT_TYPES[key]
