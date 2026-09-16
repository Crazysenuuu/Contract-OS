import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit import AuditEvent
from app.services.audit_service import record_event
from app.models.workflow import (
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowState,
    WorkflowTransition,
)


@dataclass
class TransitionResult:
    previous_state: str
    current_state: str
    transition_id: uuid.UUID
    workflow_instance_id: uuid.UUID


@dataclass
class StateInfo:
    key: str
    name: str
    is_initial: bool
    is_terminal: bool


class WorkflowEngine:
    """Server-enforced state machine for agreement lifecycle."""

    async def get_or_create_instance(
        self,
        db: AsyncSession,
        *,
        agreement_id: uuid.UUID,
        workflow_key: str,
    ) -> WorkflowInstance:
        result = await db.execute(
            select(WorkflowInstance)
            .where(WorkflowInstance.agreement_id == agreement_id)
        )
        instance = result.scalar_one_or_none()

        if instance is not None:
            return instance

        workflow = await self._get_workflow(db, workflow_key)

        initial_state = await self._get_initial_state(db, workflow.id)

        instance = WorkflowInstance(
            agreement_id=agreement_id,
            workflow_id=workflow.id,
            current_state_id=initial_state.id,
            revision=0,
        )
        db.add(instance)
        await db.flush()

        return instance

    async def transition(
        self,
        db: AsyncSession,
        *,
        agreement_id: uuid.UUID,
        action_key: str,
        actor_id: uuid.UUID,
        actor_type: str = "user",
        tenant_id: uuid.UUID | None = None,
        metadata: dict | None = None,
    ) -> TransitionResult:
        instance = await self._get_instance(db, agreement_id)

        current_state = await self._get_state(db, instance.current_state_id)

        transition = await self._get_transition(
            db,
            workflow_id=instance.workflow_id,
            from_state_id=instance.current_state_id,
            action_key=action_key,
        )

        next_state = await self._get_state(db, transition.to_state_id)

        await self._enforce_permission(
            db,
            transition=transition,
            agreement_id=agreement_id,
            actor_id=actor_id,
            actor_type=actor_type,
            tenant_id=tenant_id,
        )

        await self._enforce_gates(
            db,
            transition=transition,
            agreement_id=agreement_id,
        )

        previous_state_key = current_state.key

        # Mirror the workflow state onto the authoritative lifecycle column so
        # the two machines stay in sync. Maps workflow action_keys to lifecycle
        # action_keys; when no mapping exists the lifecycle status is left
        # untouched (the workflow engine continues to track its own state).
        lifecycle_action = self._lifecycle_action_for(action_key)
        if lifecycle_action is not None and agreement_id is not None:
            try:
                from app.models.agreement import Agreement
                from app.services.lifecycle_service import (
                    apply_transition,
                    TransitionNotAllowed,
                )

                agr_result = await db.execute(
                    select(Agreement).where(Agreement.id == agreement_id)
                )
                agreement = agr_result.scalar_one_or_none()
                if agreement is not None:
                    await apply_transition(
                        db,
                        agreement=agreement,
                        action_key=lifecycle_action,
                        actor_id=actor_id,
                        org_id=tenant_id or uuid.UUID(int=0),
                        actor_type=actor_type,
                        metadata_json={
                            "via": "workflow_engine",
                            "workflow_key": action_key,
                        },
                    )
            except TransitionNotAllowed:
                # Revert the workflow-side move so both machines stay aligned
                # with the lifecycle's allowed actions.
                raise HTTPException(
                    status_code=409,
                    detail=f"Action '{action_key}' is not valid from the current lifecycle state",
                )

        instance.current_state_id = next_state.id
        instance.revision += 1

        await db.flush()

        await self._record_audit_event(
            db,
            tenant_id=tenant_id or uuid.UUID(int=0),
            agreement_id=agreement_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=f"WORKFLOW_{action_key.upper()}",
            resource_type="agreement",
            resource_id=agreement_id,
            metadata={
                "previous_state": previous_state_key,
                "current_state": next_state.key,
                "action_key": action_key,
                **(metadata or {}),
            },
        )

        await db.flush()

        return TransitionResult(
            previous_state=previous_state_key,
            current_state=next_state.key,
            transition_id=transition.id,
            workflow_instance_id=instance.id,
        )

    async def _enforce_permission(
        self,
        db: AsyncSession,
        *,
        transition: WorkflowTransition,
        agreement_id: uuid.UUID,
        actor_id: uuid.UUID,
        actor_type: str,
        tenant_id: uuid.UUID | None,
    ) -> None:
        """Enforce the transition's requires_permission for the actor."""
        permission = transition.requires_permission
        if not permission:
            return

        if actor_type != "user":
            raise HTTPException(
                status_code=403,
                detail=f"System actors cannot perform '{transition.action_key}'",
            )

        from app.models.user import User

        result = await db.execute(select(User).where(User.id == actor_id))
        actor = result.scalar_one_or_none()
        if actor is None:
            raise HTTPException(
                status_code=403,
                detail=f"Unknown actor '{actor_id}' cannot perform '{transition.action_key}'",
            )

        org_provided = tenant_id is not None and tenant_id != uuid.UUID(int=0)
        if org_provided:
            from app.dependencies.agreement_access import verify_agreement_access

            await verify_agreement_access(
                agreement_id=agreement_id,
                permission=permission,
                current_user=actor,
                org_id=tenant_id,
                db=db,
            )
            return

        from app.models.agreement import Agreement
        from app.models.agreement_access import AgreementParticipant
        from app.dependencies.agreement_access import _has_permission

        agr_result = await db.execute(
            select(Agreement).where(Agreement.id == agreement_id)
        )
        agreement = agr_result.scalar_one_or_none()
        if agreement is None:
            raise HTTPException(
                status_code=404,
                detail="Agreement not found",
            )

        # Agreement creator always holds full authority.
        if agreement.created_by == actor.id:
            return

        part_result = await db.execute(
            select(AgreementParticipant).where(
                AgreementParticipant.agreement_id == agreement_id,
                AgreementParticipant.user_id == actor.id,
                AgreementParticipant.status == "active",
            )
        )
        participant = part_result.scalar_one_or_none()
        if participant is not None and _has_permission(participant, permission):
            return

        raise HTTPException(
            status_code=403,
            detail=f"Missing permission: {permission} for '{transition.action_key}'",
        )

    async def _enforce_gates(
        self,
        db: AsyncSession,
        *,
        transition: WorkflowTransition,
        agreement_id: uuid.UUID,
    ) -> None:
        """Evaluate declarative prerequisite gates before advancing.

        Gates are one-way hard guards (a violated invariant blocks the
        transition with HTTP 409) keyed by action_key so the state machine
        can never advance into a state whose legal prerequisites are unmet.
        """
        gate = self._GATES.get(transition.action_key)
        if gate is None:
            return

        ok, reason = await gate(db, agreement_id)
        if not ok:
            raise HTTPException(
                status_code=409,
                detail=f"Action '{transition.action_key}' cannot proceed: {reason}",
            )

    @staticmethod
    async def _gate_no_open_changes(
        db: AsyncSession,
        agreement_id: uuid.UUID,
    ) -> tuple[bool, str]:
        """Require no unresolved negotiation change on the agreement."""
        from app.models.negotiation import AgreementChange

        result = await db.execute(
            select(AgreementChange.id).where(
                AgreementChange.agreement_id == agreement_id,
                AgreementChange.status.in_(
                    ["proposed", "client_confirmed", "released"]
                ),
            )
        )
        if result.first() is not None:
            return False, "agreement has open negotiation changes"
        return True, ""

    _GATES: dict[str, object] = {
        # Cannot send, execute, or terminate while negotiations are open.
        "approve_and_send": _gate_no_open_changes,
        "send_directly": _gate_no_open_changes,
        "start_signing": _gate_no_open_changes,
        "complete_signing": _gate_no_open_changes,
        "terminate": _gate_no_open_changes,
    }

    @staticmethod
    def _lifecycle_action_for(action_key: str) -> str | None:
        """Map a workflow action_key to the equivalent lifecycle action_key.

        Returns None when the workflow action has no lifecycle equivalent, in
        which case the workflow engine tracks the move without touching the
        agreement.status column.
        """
        mapping = {
            "submit_for_review": "submit",
            "send_directly": "send",
            "approve_and_send": "send",
            "reject_to_draft": "reopen",
            "counterparty_views": "view",
            "request_change": "to_negotiating",
            "accept": "to_negotiating",
            "counter_propose": "to_negotiating",
            "start_signing": "sign",
            "complete_signing": "execute",
            "terminate": "terminate",
            "cancel": "cancel",
        }
        return mapping.get(action_key)

    async def get_current_state(
        self,
        db: AsyncSession,
        *,
        agreement_id: uuid.UUID,
    ) -> StateInfo:
        instance = await self.get_or_create_instance(
            db, agreement_id=agreement_id, workflow_key="mutual_nda_lk_v1"
        )
        state = await self._get_state(db, instance.current_state_id)

        return StateInfo(
            key=state.key,
            name=state.name,
            is_initial=state.is_initial,
            is_terminal=state.is_terminal,
        )

    async def get_available_actions(
        self,
        db: AsyncSession,
        *,
        agreement_id: uuid.UUID,
    ) -> list[dict]:
        instance = await self.get_or_create_instance(
            db, agreement_id=agreement_id, workflow_key="mutual_nda_lk_v1"
        )

        result = await db.execute(
            select(WorkflowTransition)
            .where(
                WorkflowTransition.workflow_id == instance.workflow_id,
                WorkflowTransition.from_state_id == instance.current_state_id,
            )
        )
        transitions = result.scalars().all()

        return [
            {
                "action_key": t.action_key,
                "name": t.name,
                "description": t.description,
                "requires_confirmation": t.requires_confirmation,
                "requires_permission": t.requires_permission,
            }
            for t in transitions
        ]

    async def _get_workflow(
        self,
        db: AsyncSession,
        key: str,
    ) -> WorkflowDefinition:
        result = await db.execute(
            select(WorkflowDefinition)
            .where(
                WorkflowDefinition.key == key,
                WorkflowDefinition.is_active == True,
            )
        )
        workflow = result.scalar_one_or_none()

        if workflow is None:
            raise HTTPException(
                status_code=404,
                detail=f"Workflow '{key}' not found",
            )

        return workflow

    async def _get_initial_state(
        self,
        db: AsyncSession,
        workflow_id: uuid.UUID,
    ) -> WorkflowState:
        result = await db.execute(
            select(WorkflowState)
            .where(
                WorkflowState.workflow_id == workflow_id,
                WorkflowState.is_initial == True,
            )
        )
        state = result.scalar_one_or_none()

        if state is None:
            raise HTTPException(
                status_code=500,
                detail="Workflow has no initial state",
            )

        return state

    async def _get_instance(
        self,
        db: AsyncSession,
        agreement_id: uuid.UUID,
    ) -> WorkflowInstance:
        result = await db.execute(
            select(WorkflowInstance)
            .where(WorkflowInstance.agreement_id == agreement_id)
        )
        instance = result.scalar_one_or_none()

        if instance is None:
            raise HTTPException(
                status_code=404,
                detail="Workflow instance not found for this agreement",
            )

        return instance

    async def _get_state(
        self,
        db: AsyncSession,
        state_id: uuid.UUID,
    ) -> WorkflowState:
        result = await db.execute(
            select(WorkflowState).where(WorkflowState.id == state_id)
        )
        state = result.scalar_one_or_none()

        if state is None:
            raise HTTPException(
                status_code=500,
                detail="Workflow state not found",
            )

        return state

    async def _get_transition(
        self,
        db: AsyncSession,
        *,
        workflow_id: uuid.UUID,
        from_state_id: uuid.UUID,
        action_key: str,
    ) -> WorkflowTransition:
        result = await db.execute(
            select(WorkflowTransition)
            .where(
                WorkflowTransition.workflow_id == workflow_id,
                WorkflowTransition.from_state_id == from_state_id,
                WorkflowTransition.action_key == action_key,
            )
        )
        transition = result.scalar_one_or_none()

        if transition is None:
            raise HTTPException(
                status_code=409,
                detail=f"Action '{action_key}' is not valid from the current state",
            )

        return transition

    async def _record_audit_event(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agreement_id: uuid.UUID | None,
        actor_id: uuid.UUID | None,
        actor_type: str,
        action: str,
        resource_type: str | None,
        resource_id: uuid.UUID | None,
        metadata: dict | None = None,
    ) -> AuditEvent:
        return await record_event(
            db,
            tenant_id=tenant_id,
            agreement_id=agreement_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_json=metadata,
        )
