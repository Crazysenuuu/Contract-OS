"""Canonical agreement domain states (spec §39, §66, §67, 1.25.1).

This module is the **single** definition of which agreement statuses exist
and which edges between them are legal. Everything else derives from it:

* ``lifecycle_service.CORE_STATES`` and ``seed_data.AGREEMENT_STATES`` are
  built from :data:`STATE_REGISTRY`;
* ``seed_data.TRANSITION_RULES`` is built from :data:`DEFAULT_TRANSITION_RULES``;
* route code compares against :class:`AgreementStatus` members, never raw
  strings ("avoid random status strings throughout the application").

Spec §66 lifecycle::

    DRAFT → INTERNAL_REVIEW → PENDING_APPROVAL → APPROVED → SENT → (VIEWED)
          → NEGOTIATING → READY_FOR_SIGNATURE → SIGNING → PARTIALLY_SIGNED
          → EXECUTED → ACTIVE → EXPIRING → RENEWED / EXPIRED / TERMINATED

The data-driven table in ``agreement_status_transitions`` may narrow this
per organisation / agreement type, but it may never widen it: every seeded
rule must be a legal edge in :data:`VALID_TRANSITIONS`.
"""

from __future__ import annotations

from enum import StrEnum


class AgreementStatus(StrEnum):
    DRAFT = "draft"
    INTERNAL_REVIEW = "internal_review"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SENT = "sent"
    VIEWED = "viewed"
    NEGOTIATING = "negotiating"
    READY_FOR_SIGNATURE = "ready_for_signature"
    SIGNING = "signing"
    PARTIALLY_SIGNED = "partially_signed"
    EXECUTED = "executed"
    ACTIVE = "active"
    EXPIRING = "expiring"
    RENEWED = "renewed"
    EXPIRED = "expired"
    TERMINATED = "terminated"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


# (status, label, is_terminal, description)
STATE_REGISTRY: list[tuple[AgreementStatus, str, bool, str]] = [
    (AgreementStatus.DRAFT, "Draft", False, "Agreement is being drafted and edited"),
    (AgreementStatus.INTERNAL_REVIEW, "Internal Review", False, "Under internal legal/business review"),
    (AgreementStatus.PENDING_APPROVAL, "Pending Approval", False, "Awaiting decisions from the configured approval chain"),
    (AgreementStatus.APPROVED, "Approved", False, "Approved internally and ready to send or sign"),
    (AgreementStatus.SENT, "Sent to Counterparty", False, "Sent to counterparty for review"),
    (AgreementStatus.VIEWED, "Viewed by Counterparty", False, "Counterparty has opened the shared document"),
    (AgreementStatus.NEGOTIATING, "Negotiating", False, "Terms are being negotiated with the counterparty"),
    (AgreementStatus.READY_FOR_SIGNATURE, "Ready for Signature", False, "All parties accepted the terms; signature collection may begin"),
    (AgreementStatus.SIGNING, "In Signing", False, "Signature collection in progress"),
    (AgreementStatus.PARTIALLY_SIGNED, "Partially Signed", False, "Some, but not all, required signatures collected"),
    (AgreementStatus.EXECUTED, "Executed", False, "All required signatures collected"),
    (AgreementStatus.ACTIVE, "Active", False, "In force and being performed"),
    (AgreementStatus.EXPIRING, "Expiring", False, "Within the renewal notice window"),
    (AgreementStatus.RENEWED, "Renewed", False, "Term renewed for a further period"),
    (AgreementStatus.EXPIRED, "Expired", True, "Term came to an end without renewal"),
    (AgreementStatus.TERMINATED, "Terminated", True, "Ended before its natural term"),
    (AgreementStatus.SUPERSEDED, "Superseded", True, "Replaced by a successor agreement"),
    (AgreementStatus.CANCELLED, "Cancelled", True, "Abandoned before execution"),
]

TERMINAL_STATES: frozenset[str] = frozenset(
    s.value for s, _, terminal, _ in STATE_REGISTRY if terminal
)

# Statuses in which the agreement's terms may still be edited directly.
EDITABLE_STATES: frozenset[str] = frozenset(
    {AgreementStatus.DRAFT, AgreementStatus.NEGOTIATING, AgreementStatus.INTERNAL_REVIEW}
)

# Statuses in which the counterparty is actively reviewing / responding.
COUNTERPARTY_ACTIVE_STATES: frozenset[str] = frozenset(
    {
        AgreementStatus.SENT,
        AgreementStatus.VIEWED,
        AgreementStatus.NEGOTIATING,
        AgreementStatus.READY_FOR_SIGNATURE,
    }
)

# Statuses from which an internal signer may record a signature.
SIGNABLE_STATES: frozenset[str] = frozenset(
    {
        AgreementStatus.APPROVED,
        AgreementStatus.SENT,
        AgreementStatus.VIEWED,
        AgreementStatus.NEGOTIATING,
        AgreementStatus.READY_FOR_SIGNATURE,
        AgreementStatus.SIGNING,
        AgreementStatus.PARTIALLY_SIGNED,
    }
)

# Statuses after which the base agreement is immutable (edits require an
# amendment) — spec §22 "never modify a signed agreement".
IMMUTABLE_STATES: frozenset[str] = frozenset(
    {
        AgreementStatus.EXECUTED,
        AgreementStatus.ACTIVE,
        AgreementStatus.EXPIRING,
        AgreementStatus.RENEWED,
        AgreementStatus.EXPIRED,
        AgreementStatus.TERMINATED,
        AgreementStatus.SUPERSEDED,
    }
)

# Every status before execution (used by compliance sweeps / redline checks).
PRE_EXECUTION_STATES: frozenset[str] = frozenset(
    {
        AgreementStatus.DRAFT,
        AgreementStatus.INTERNAL_REVIEW,
        AgreementStatus.PENDING_APPROVAL,
        AgreementStatus.APPROVED,
        AgreementStatus.SENT,
        AgreementStatus.VIEWED,
        AgreementStatus.NEGOTIATING,
        AgreementStatus.READY_FOR_SIGNATURE,
        AgreementStatus.SIGNING,
        AgreementStatus.PARTIALLY_SIGNED,
    }
)

# Statuses that count as "in force" for obligations, risk, dashboards.
IN_FORCE_STATES: frozenset[str] = frozenset(
    {AgreementStatus.EXECUTED, AgreementStatus.ACTIVE, AgreementStatus.EXPIRING, AgreementStatus.RENEWED}
)


VALID_TRANSITIONS: dict[str, set[str]] = {
    AgreementStatus.DRAFT: {
        AgreementStatus.INTERNAL_REVIEW,
        AgreementStatus.PENDING_APPROVAL,
        AgreementStatus.SENT,
        AgreementStatus.NEGOTIATING,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.INTERNAL_REVIEW: {
        AgreementStatus.DRAFT,
        AgreementStatus.PENDING_APPROVAL,
        AgreementStatus.APPROVED,
        AgreementStatus.SENT,
        AgreementStatus.NEGOTIATING,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.PENDING_APPROVAL: {
        AgreementStatus.APPROVED,
        AgreementStatus.DRAFT,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.APPROVED: {
        AgreementStatus.SENT,
        AgreementStatus.READY_FOR_SIGNATURE,
        AgreementStatus.SIGNING,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.SENT: {
        AgreementStatus.VIEWED,
        AgreementStatus.NEGOTIATING,
        AgreementStatus.READY_FOR_SIGNATURE,
        AgreementStatus.SIGNING,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.VIEWED: {
        AgreementStatus.VIEWED,
        AgreementStatus.NEGOTIATING,
        AgreementStatus.READY_FOR_SIGNATURE,
        AgreementStatus.SIGNING,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.NEGOTIATING: {
        AgreementStatus.NEGOTIATING,
        AgreementStatus.VIEWED,
        AgreementStatus.READY_FOR_SIGNATURE,
        AgreementStatus.SIGNING,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.READY_FOR_SIGNATURE: {
        AgreementStatus.SIGNING,
        AgreementStatus.NEGOTIATING,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.SIGNING: {
        AgreementStatus.SIGNING,
        AgreementStatus.PARTIALLY_SIGNED,
        AgreementStatus.EXECUTED,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.PARTIALLY_SIGNED: {
        AgreementStatus.PARTIALLY_SIGNED,
        AgreementStatus.EXECUTED,
        AgreementStatus.CANCELLED,
    },
    AgreementStatus.EXECUTED: {
        AgreementStatus.ACTIVE,
        AgreementStatus.TERMINATED,
        AgreementStatus.EXPIRED,
        AgreementStatus.SUPERSEDED,
    },
    AgreementStatus.ACTIVE: {
        AgreementStatus.ACTIVE,
        AgreementStatus.EXPIRING,
        AgreementStatus.EXPIRED,
        AgreementStatus.TERMINATED,
        AgreementStatus.SUPERSEDED,
    },
    AgreementStatus.EXPIRING: {
        AgreementStatus.ACTIVE,
        AgreementStatus.RENEWED,
        AgreementStatus.EXPIRED,
        AgreementStatus.TERMINATED,
    },
    AgreementStatus.RENEWED: {
        AgreementStatus.ACTIVE,
    },
    AgreementStatus.EXPIRED: set(),
    AgreementStatus.TERMINATED: set(),
    AgreementStatus.SUPERSEDED: set(),
    AgreementStatus.CANCELLED: set(),
}


class InvalidAgreementTransition(Exception):
    """Raised when a status change violates the canonical state machine."""


def validate_transition(current_status: str, new_status: str) -> None:
    """Validate a status change against the canonical state machine.

    Raises InvalidAgreementTransition when the transition is not permitted.
    """
    allowed = VALID_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        raise InvalidAgreementTransition(
            f"Invalid agreement state transition: {current_status} -> {new_status}"
        )


def _rule(
    action: str,
    frm: AgreementStatus,
    to: AgreementStatus,
    description: str,
    permission: str | None = None,
    conditions: dict | None = None,
) -> dict:
    validate_transition(frm, to)  # seeded rules can never widen the machine
    return {
        "action_key": action,
        "from_status": frm.value,
        "to_status": to.value,
        "description": description,
        "required_permission": permission,
        "conditions": conditions,
    }


S = AgreementStatus

# Global default transition rules seeded into agreement_status_transitions.
# ``action_key`` is the verb requested by routes/services; the lifecycle
# service resolves the row by (action, from_status) with org/type overrides.
DEFAULT_TRANSITION_RULES: list[dict] = [
    # --- internal review & approval (spec §24, §67) ---------------------
    _rule("submit", S.DRAFT, S.INTERNAL_REVIEW, "Submit for internal review"),
    _rule("submit_for_review", S.DRAFT, S.INTERNAL_REVIEW, "Submit for internal review"),
    _rule("submit_for_approval", S.DRAFT, S.PENDING_APPROVAL, "Route to the approval chain", "agreement.submit_approval"),
    _rule("submit_for_approval", S.INTERNAL_REVIEW, S.PENDING_APPROVAL, "Route to the approval chain", "agreement.submit_approval"),
    _rule("approve", S.INTERNAL_REVIEW, S.APPROVED, "Approve for sending / signing", "agreement.approve"),
    _rule("approve", S.PENDING_APPROVAL, S.APPROVED, "All approval stages complete", "agreement.approve"),
    _rule("reject", S.PENDING_APPROVAL, S.DRAFT, "Approval rejected; return to drafting", "agreement.approve"),
    _rule("reopen", S.INTERNAL_REVIEW, S.DRAFT, "Return to drafting"),
    # --- send to counterparty --------------------------------------------
    _rule("send", S.DRAFT, S.SENT, "Send to counterparty", "agreement.send"),
    _rule("send", S.INTERNAL_REVIEW, S.SENT, "Send to counterparty", "agreement.send"),
    _rule("send", S.APPROVED, S.SENT, "Send to counterparty", "agreement.send"),
    _rule("view", S.SENT, S.VIEWED, "Counterparty opened the document"),
    _rule("view", S.VIEWED, S.VIEWED, "Counterparty opened the document again"),
    _rule("view", S.NEGOTIATING, S.VIEWED, "Counterparty opened the document"),
    _rule("counterparty_views", S.SENT, S.VIEWED, "Counterparty opened the document"),
    _rule("counterparty_views", S.VIEWED, S.VIEWED, "Counterparty opened the document again"),
    # --- negotiation (spec §37) -------------------------------------------
    _rule("to_negotiating", S.DRAFT, S.NEGOTIATING, "Open negotiation"),
    _rule("to_negotiating", S.INTERNAL_REVIEW, S.NEGOTIATING, "Open negotiation"),
    _rule("request_change", S.SENT, S.NEGOTIATING, "Counterparty requested a change"),
    _rule("request_change", S.VIEWED, S.NEGOTIATING, "Counterparty requested a change"),
    _rule("counter_propose", S.NEGOTIATING, S.NEGOTIATING, "Counter-proposal during negotiation"),
    _rule("accept", S.SENT, S.READY_FOR_SIGNATURE, "Counterparty accepted the terms as-is"),
    _rule("accept", S.VIEWED, S.READY_FOR_SIGNATURE, "Counterparty accepted the terms as-is"),
    _rule("accept", S.NEGOTIATING, S.READY_FOR_SIGNATURE, "Both parties accepted the final terms"),
    _rule("prepare_signing", S.APPROVED, S.READY_FOR_SIGNATURE, "Mark ready for signature collection", "agreement.sign"),
    _rule("reopen_negotiation", S.READY_FOR_SIGNATURE, S.NEGOTIATING, "Re-open negotiation before signing"),
    # --- signing (spec §21, §67) -----------------------------------------
    _rule("sign", S.APPROVED, S.SIGNING, "Begin signature collection", "agreement.sign"),
    _rule("sign", S.SENT, S.SIGNING, "Record signature", "agreement.sign"),
    _rule("sign", S.VIEWED, S.SIGNING, "Record signature", "agreement.sign"),
    _rule("sign", S.NEGOTIATING, S.SIGNING, "Sign out of negotiation", "agreement.sign"),
    _rule("sign", S.READY_FOR_SIGNATURE, S.SIGNING, "Begin signature collection", "agreement.sign"),
    _rule("sign", S.SIGNING, S.SIGNING, "Record additional signature", "agreement.sign"),
    _rule("sign", S.PARTIALLY_SIGNED, S.PARTIALLY_SIGNED, "Record additional signature", "agreement.sign"),
    _rule("partial_sign", S.SIGNING, S.PARTIALLY_SIGNED, "Some required signatures collected"),
    _rule("partial_sign", S.PARTIALLY_SIGNED, S.PARTIALLY_SIGNED, "Further signature collected; still incomplete"),
    _rule("execute", S.SIGNING, S.EXECUTED, "All required signatures collected", "agreement.sign", {"all_signed": True}),
    _rule("execute", S.PARTIALLY_SIGNED, S.EXECUTED, "All required signatures collected", "agreement.sign", {"all_signed": True}),
    # --- in force (spec §25, §35) ----------------------------------------
    _rule("activate", S.EXECUTED, S.ACTIVE, "Move into force"),
    _rule("activate", S.RENEWED, S.ACTIVE, "Renewal in force"),
    _rule("expiring", S.ACTIVE, S.EXPIRING, "Within the renewal notice window"),
    _rule("renew", S.ACTIVE, S.ACTIVE, "Extend term via renewal"),
    _rule("renew", S.EXPIRING, S.RENEWED, "Extend term via renewal"),
    _rule("expire", S.ACTIVE, S.EXPIRED, "Term ended without renewal"),
    _rule("expire", S.EXECUTED, S.EXPIRED, "Term ended without renewal"),
    _rule("expire", S.EXPIRING, S.EXPIRED, "Term ended without renewal"),
    _rule("terminate", S.ACTIVE, S.TERMINATED, "Terminate with effect", "agreement.terminate"),
    _rule("terminate", S.EXECUTED, S.TERMINATED, "Terminate with effect", "agreement.terminate"),
    _rule("terminate", S.EXPIRING, S.TERMINATED, "Terminate with effect", "agreement.terminate"),
    _rule("supersede", S.ACTIVE, S.SUPERSEDED, "Replaced by a successor agreement"),
    _rule("supersede", S.EXECUTED, S.SUPERSEDED, "Replaced by a successor agreement"),
    # --- abandonment -------------------------------------------------------
    _rule("cancel", S.DRAFT, S.CANCELLED, "Abandon the agreement"),
    _rule("cancel", S.INTERNAL_REVIEW, S.CANCELLED, "Abandon the agreement"),
    _rule("cancel", S.PENDING_APPROVAL, S.CANCELLED, "Abandon the agreement"),
    _rule("cancel", S.NEGOTIATING, S.CANCELLED, "Abandon the agreement"),
    _rule("cancel", S.SENT, S.CANCELLED, "Withdraw the agreement"),
    _rule("cancel", S.VIEWED, S.CANCELLED, "Withdraw the agreement"),
]


# Predicted target status per action key, used by AgreementStateService as
# a pre-check before consulting the data-driven table. ``None`` means the
# outcome depends on the from-status (e.g. ``sign``) and is not pre-checked.
ACTION_TARGETS: dict[str, str | None] = {
    r["action_key"]: r["to_status"]
    for r in DEFAULT_TRANSITION_RULES
    if len({x["to_status"] for x in DEFAULT_TRANSITION_RULES if x["action_key"] == r["action_key"]}) == 1
}


def state_registry_rows() -> list[dict]:
    """Registry rows in the shape the seed / conftest expect."""
    return [
        {"status": s.value, "label": label, "is_terminal": terminal, "description": desc}
        for s, label, terminal, desc in STATE_REGISTRY
    ]
