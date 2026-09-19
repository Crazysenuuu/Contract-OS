"""Centralized agreement state service (spec 1.25.2-1.25.5).

Single entry point for legal status changes. Every mutation of
``agreement.status`` goes through here, which:

1. checks the canonical state machine (``domain.agreement_states``) as a
   safety net, then
2. delegates to the data-driven ``lifecycle_service.apply_transition`` so
   per-type rules, conditions, audit and events behave identically to the
   existing API routes.

Services must never assign ``agreement.status`` directly.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import WorkflowStateError
from app.domain.agreement_states import ACTION_TARGETS, validate_transition
from app.models.agreement import Agreement


class AgreementStateService:
    """Transactional domain service for agreement status transitions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def transition(
        self,
        agreement: Agreement,
        action_key: str,
        *,
        actor_id: UUID | None,
        org_id: UUID,
        actor_type: str = "user",
        ip_address: str | None = None,
        metadata_json: dict | None = None,
    ) -> Agreement:
        """Apply a lifecycle action, validating against the canonical
        machine first. Raises WorkflowStateError (409) when the transition
        is not allowed."""
        from app.services.lifecycle_service import TransitionNotAllowed, apply_transition

        # 1. Canonical safety net: predict the target status from the
        #    action key where possible and validate the edge.
        target = self.predict_target(action_key)
        if target is not None:
            try:
                validate_transition(agreement.status, target)
            except Exception as exc:
                raise WorkflowStateError(str(exc)) from exc

        # 2. Data-driven transition (rule conditions, audit, events).
        try:
            return await apply_transition(
                self.db,
                agreement=agreement,
                action_key=action_key,
                actor_id=actor_id,
                org_id=org_id,
                actor_type=actor_type,
                ip_address=ip_address,
                metadata_json=metadata_json,
            )
        except TransitionNotAllowed as exc:
            raise WorkflowStateError(str(exc)) from exc

    @staticmethod
    def predict_target(action_key: str) -> str | None:
        """Map an action key to its canonical target status for the
        pre-check. Returns None when the target depends on the from-status
        (e.g. ``sign``) or the action is type-specific, in which case only
        the data-driven rules decide."""
        return ACTION_TARGETS.get(action_key)
