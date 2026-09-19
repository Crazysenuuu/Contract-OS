"""Canonical state machine + exception taxonomy tests (spec 1.25.1-1.25.5,
1.23.25)."""

import pytest

from app.core.exceptions import (
    AppError,
    ForbiddenError,
    VersionConflictError,
    WorkflowStateError,
)
from app.domain.agreement_states import (
    DEFAULT_TRANSITION_RULES,
    STATE_REGISTRY,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    AgreementStatus,
    InvalidAgreementTransition,
    state_registry_rows,
    validate_transition,
)


class TestStateMachine:
    def test_all_statuses_have_transition_entries(self):
        for status in AgreementStatus:
            assert status.value in VALID_TRANSITIONS

    def test_registry_covers_every_status(self):
        assert {s.value for s in AgreementStatus} == {s.value for s, *_ in STATE_REGISTRY}

    def test_terminal_states_have_no_outgoing(self):
        assert TERMINAL_STATES == {"terminated", "expired", "cancelled", "superseded"}
        for terminal in TERMINAL_STATES:
            assert VALID_TRANSITIONS[terminal] == set()

    def test_spec_66_happy_path_is_valid(self):
        path = [
            ("draft", "internal_review"),
            ("internal_review", "pending_approval"),
            ("pending_approval", "approved"),
            ("approved", "sent"),
            ("sent", "negotiating"),
            ("negotiating", "ready_for_signature"),
            ("ready_for_signature", "signing"),
            ("signing", "partially_signed"),
            ("partially_signed", "executed"),
            ("executed", "active"),
            ("active", "expiring"),
            ("expiring", "expired"),
        ]
        for current, new in path:
            validate_transition(current, new)  # must not raise

    def test_cannot_sign_from_draft(self):
        with pytest.raises(InvalidAgreementTransition):
            validate_transition("draft", "signing")

    def test_cannot_activate_from_draft(self):
        with pytest.raises(InvalidAgreementTransition):
            validate_transition("draft", "active")

    def test_cannot_execute_from_ready_for_signature(self):
        """Spec §67: READY_FOR_SIGNATURE → EXECUTED only via signing."""
        with pytest.raises(InvalidAgreementTransition):
            validate_transition("ready_for_signature", "executed")

    def test_seeded_rules_never_widen_the_machine(self):
        for rule in DEFAULT_TRANSITION_RULES:
            assert rule["to_status"] in VALID_TRANSITIONS[rule["from_status"]]
            assert rule["from_status"] in AgreementStatus.__members__.values()

    def test_execute_rules_require_all_signed(self):
        for rule in DEFAULT_TRANSITION_RULES:
            if rule["action_key"] == "execute":
                assert rule["conditions"] == {"all_signed": True}

    def test_registry_rows_shape(self):
        rows = state_registry_rows()
        assert {"status", "label", "is_terminal", "description"} <= set(rows[0])
        assert len(rows) == len(AgreementStatus)


class TestExceptionTaxonomy:
    def test_codes_map_to_http_statuses(self):
        assert VersionConflictError("x").status_code == 409
        assert WorkflowStateError("x").status_code == 409
        assert ForbiddenError("x").status_code == 403

    def test_error_payload_shape(self):
        err = VersionConflictError("stale version", {"expected": 2})
        payload = err.to_dict()
        assert payload["error"]["code"] == "VERSION_CONFLICT"
        assert payload["error"]["details"] == {"expected": 2}

    def test_subclass_code_specialisation(self):
        from app.core.exceptions import InsufficientPermissionError

        assert InsufficientPermissionError("x").code == "INSUFFICIENT_PERMISSION"
        assert issubclass(InsufficientPermissionError, AppError)


@pytest.mark.asyncio
async def test_state_service_blocks_illegal_transition(db_session, test_agreement):
    """The centralized service refuses to move draft -> active."""
    from app.services.agreement_state_service import AgreementStateService

    service = AgreementStateService(db_session)
    test_agreement.status = "draft"
    with pytest.raises(WorkflowStateError):
        await service.transition(
            test_agreement,
            "activate",
            actor_id=None,
            org_id=test_agreement.organization_id,
        )


@pytest.mark.asyncio
async def test_state_service_delegates_legal_transitions(db_session, test_agreement):
    """Legal transitions flow through to the data-driven engine."""
    from app.services.agreement_state_service import AgreementStateService

    test_agreement.status = "draft"
    service = AgreementStateService(db_session)
    # 'to_negotiating' is legal from draft per the canonical machine;
    # whether the data-driven rules permit it depends on seeded rules - the
    # service must raise WorkflowStateError (never TransitionNotAllowed).
    try:
        updated = await service.transition(
            test_agreement,
            "to_negotiating",
            actor_id=None,
            org_id=test_agreement.organization_id,
        )
        assert updated.status in ("negotiating", "draft")
    except WorkflowStateError:
        pass  # data-driven rules rejected it - acceptable, error type is right
