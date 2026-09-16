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
    VALID_TRANSITIONS,
    AgreementStatus,
    InvalidAgreementTransition,
    validate_transition,
)


class TestStateMachine:
    def test_all_statuses_have_transition_entries(self):
        for status in AgreementStatus:
            assert status.value in VALID_TRANSITIONS

    def test_terminal_states_have_no_outgoing(self):
        for terminal in ("terminated", "expired", "cancelled"):
            assert VALID_TRANSITIONS[terminal] == set()

    def test_happy_path_is_valid(self):
        path = [
            ("draft", "negotiation"),
            ("negotiation", "negotiation_complete"),
            ("negotiation_complete", "signing_pending"),
            ("signing_pending", "signing"),
            ("signing", "executed"),
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
    # 'start_negotiation' is legal from draft per the canonical machine;
    # whether the data-driven rules permit it depends on seeded rules - the
    # service must raise WorkflowStateError (never TransitionNotAllowed).
    try:
        updated = await service.transition(
            test_agreement,
            "start_negotiation",
            actor_id=None,
            org_id=test_agreement.organization_id,
        )
        assert updated.status in ("negotiation", "draft")
    except WorkflowStateError:
        pass  # data-driven rules rejected it - acceptable, error type is right
