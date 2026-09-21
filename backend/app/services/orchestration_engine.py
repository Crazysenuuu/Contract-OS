"""Workflow orchestration engine (spec §2.11.12/2.11.25/2.11.26).

The engine is configuration-driven: it walks a definition's step graph,
evaluates transition conditions against the instance context, and produces
side effects only through registered actions. It knows nothing about a given
agreement class; workflow definitions encode that knowledge (spec §2.11.1).

Flow::

    dispatch_event ─▶ resolve_matching_definitions ─▶ start_workflow
                                                          │
       advance ◀──────────────────────────── get_next_step │
        │  ▲                                                  │
        │  │                                                  │
   execute_step ─ TASK → OrchTask          complete_task ──┘
                   DELAY → OrchTimer        process_due_timers ──┘
                   EVENT_WAIT → OrchEventWait  handle_inbound_event ──┘
                   ACTION → registry action
                   CONDITION → context write
                   PARALLEL → ordered branch execution

Idempotency (spec §2.11.32): a domain ``event_id`` can start at most one
instance per workflow definition. Completed workflows leave their step
instances and event log in place for audit.

Retry semantics (spec §2.11.28): an ACTION step runs one attempt per step
instance. On failure with attempts remaining the engine schedules a back-off
``OrchTimer`` (reason ``action_retry_backoff``); when it fires a *fresh* step
instance is created with an incremented attempt counter and ``OrchActionAttempt``
rows accumulate as the trace of every attempt.
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orchestration import (
    OrchActionAttempt,
    OrchDeadLetter,
    OrchEventWait,
    OrchIncident,
    OrchInstanceStatus,
    OrchStepDefinition,
    OrchStepInstance,
    OrchStepType,
    OrchTask,
    OrchTimer,
    OrchTransition,
    OrchWorkflowDefinition,
    OrchWorkflowEventLog,
    OrchWorkflowInstance,
    OrchWorkflowStatus,
)

from app.services.orchestration_actions import (
    CreateTaskAction,
    WorkflowActionError,
    get_action,
)
from app.services.orchestration_conditions import evaluate_condition

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 1
DEFAULT_BACKOFF_SECONDS = 60
MAX_STEP_EXECUTIONS = 200

_RETRY_TIMER_REASON = "action_retry_backoff"


class OrchestrationError(Exception):
    """Base for orchestrator domain errors."""


class DefinitionNotActiveError(OrchestrationError):
    """Raised when dispatching to a definition that is not ACTIVE."""


class TaskNotAssignableError(OrchestrationError):
    """Raised when a user tries to complete a task that is not theirs."""


class NoMatchingEventWaitError(OrchestrationError):
    """Raised when an inbound event does not match any active event wait."""


async def dispatch_event(
    db: AsyncSession,
    *,
    event_id: UUID,
    event_type: str,
    organization_id: UUID | None = None,
    agreement_id: UUID | None = None,
    aggregate_type: str | None = None,
    aggregate_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
    actor_id: UUID | None = None,
) -> list[UUID]:
    """Route a domain event into matching workflows (spec §2.11.26).

    Returns the ids of workflow instances started. Events that already started
    an instance for the same definition are skipped (idempotency, spec
    §2.11.32). Routing failures are recorded in the dead letter table instead
    of raising.
    """

    context: dict[str, Any] = _deepish_copy(payload or {})
    context.setdefault("event_id", str(event_id))
    context.setdefault("event_type", event_type)
    context.setdefault("organization_id", str(organization_id) if organization_id else None)
    context.setdefault("agreement_id", str(agreement_id) if agreement_id else None)
    context.setdefault("aggregate_type", aggregate_type)
    context.setdefault("aggregate_id", str(aggregate_id) if aggregate_id else None)

    definitions = await _resolve_matching_definitions(
        db,
        event_type=event_type,
        organization_id=organization_id,
        aggregate_type=aggregate_type,
        payload=context,
    )

    started: list[UUID] = []
    for definition in definitions:
        if await _instance_exists_for_event(db, definition.id, event_id):
            continue
        try:
            instance = await start_workflow(
                db,
                definition=definition,
                context=context,
                organization_id=organization_id,
                agreement_id=agreement_id,
                source_event_id=event_id,
                actor_id=actor_id,
            )
            started.append(instance.id)
        except OrchestrationError as exc:
            db.add(
                OrchDeadLetter(
                    event_id=event_id,
                    event_type=event_type,
                    payload=context,
                    error=str(exc),
                )
            )
            await db.flush()
            logger.warning("workflow routing failed for event %s: %s", event_id, exc)
    return started


async def start_workflow(
    db: AsyncSession,
    *,
    definition: OrchWorkflowDefinition,
    context: dict[str, Any],
    organization_id: UUID | None = None,
    agreement_id: UUID | None = None,
    source_event_id: UUID | None = None,
    actor_id: UUID | None = None,
) -> OrchWorkflowInstance:
    """Create an instance for ``definition`` and run it to completion."""

    if definition.status != OrchWorkflowStatus.ACTIVE:
        raise DefinitionNotActiveError(
            f"Workflow {definition.code} v{definition.version} is not active"
        )

    instance = OrchWorkflowInstance(
        workflow_definition_id=definition.id,
        organization_id=organization_id,
        agreement_id=agreement_id,
        status=OrchInstanceStatus.RUNNING,
        context=_deepish_copy(context),
        source_event_id=source_event_id,
        started_at=datetime.now(timezone.utc),
    )
    db.add(instance)
    await db.flush()

    await _log_instance_event(
        db, instance.id, "instance.started",
        {"definition_code": definition.code, "version": definition.version},
        actor_id=actor_id,
    )
    await _advance(db, instance, actor_id=actor_id)
    return instance


async def handle_inbound_event(
    db: AsyncSession,
    *,
    wait_id: UUID,
    event: dict[str, Any],
    actor_id: UUID | None = None,
) -> OrchWorkflowInstance:
    """Resolve a workflow that was waiting on an external event (spec §2.11.27)."""

    wait = await db.get(OrchEventWait, wait_id)
    if wait is None or not wait.active:
        raise NoMatchingEventWaitError(f"Event wait {wait_id} is not active")

    step_instance = await db.get(OrchStepInstance, wait.workflow_step_instance_id)
    if step_instance is None:
        raise NoMatchingEventWaitError("Event wait has no step instance")

    instance = await _require_instance(db, step_instance.workflow_instance_id)

    wait.active = False
    wait.matched_at = datetime.now(timezone.utc)
    wait.matched_event_id = _uuid_or_none(event.get("event_id"))
    step_instance.status = "completed"
    step_instance.completed_at = datetime.now(timezone.utc)
    step_instance.output_data = _deepish_copy(event)

    instance.current_step_instance_id = step_instance.id
    await _log_instance_event(
        db, instance.id, "instance.event_received",
        {"wait_id": str(wait.id), "event_type": wait.event_type},
        actor_id=actor_id,
        step_instance_id=step_instance.id,
    )
    instance.status = OrchInstanceStatus.RUNNING
    await db.flush()
    await _advance(db, instance, actor_id=actor_id)
    return instance


async def process_due_timers(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """Fire elapsed DELAY and action-retry timers (spec §2.11.23).

    Returns the number of timers fired. Routinely safe: pending timers are
    selected once, marked fired before re-entry, and each advanced workflow is
    committed by the caller at its convenience.
    """

    now = now or datetime.now(timezone.utc)
    result = await db.execute(
        select(OrchTimer)
        .where(OrchTimer.status == "pending", OrchTimer.scheduled_at <= now)
        .order_by(OrchTimer.scheduled_at.asc())
        .limit(limit)
    )
    timers = list(result.scalars().all())
    fired = 0
    for timer in timers:
        if timer.status != "pending":
            continue
        timer.status = "fired"
        fired_at = datetime.now(timezone.utc)
        timer.fired_at = fired_at
        step_instance = await db.get(OrchStepInstance, timer.workflow_step_instance_id)
        if step_instance is None:
            continue
        instance = await _require_instance(db, step_instance.workflow_instance_id)

        if timer.config.get("reason") == _RETRY_TIMER_REASON:
            await _resume_action_retry(db, instance, step_instance, fired_at=fired_at)
        else:
            step_instance.status = "completed"
            step_instance.completed_at = now
            step_instance.output_data = dict(step_instance.output_data or {})
            step_instance.output_data["fired_at"] = now.isoformat()
            instance.current_step_instance_id = step_instance.id
            await _log_instance_event(
                db, instance.id, "instance.timer_fired",
                {"timer_id": str(timer.id)},
                step_instance_id=step_instance.id,
            )
            instance.status = OrchInstanceStatus.RUNNING
            await db.flush()
            await _advance(db, instance)
        fired += 1
    return fired


async def complete_task(
    db: AsyncSession,
    *,
    task_id: UUID,
    actor_id: UUID,
    action: str = "complete",
    resolution: dict[str, Any] | None = None,
) -> OrchWorkflowInstance:
    """Complete or reject a human task and continue its workflow (spec §2.11.18).

    Authorization (spec §2.11.18/2.11.67): a task with an assignee may only be
    acted on by that user. Tasks with no assignee are self-service and rely on
    the caller's permission layer for role checks.
    """

    task = await db.get(OrchTask, task_id)
    if task is None:
        raise OrchestrationError(f"Task {task_id} not found")
    if task.status != "open":
        raise OrchestrationError(f"Task {task_id} is not open")

    if task.assignee_user_id is not None and task.assignee_user_id != actor_id:
        raise TaskNotAssignableError(
            f"Task {task_id} is assigned to {task.assignee_user_id}, not {actor_id}"
        )

    now = datetime.now(timezone.utc)
    task.status = "closed"
    task.completed_at = now
    task.context = dict(task.context or {})
    task.context["action"] = action
    if resolution:
        task.context["resolution"] = resolution

    step_instance = await db.get(OrchStepInstance, task.workflow_step_instance_id)
    if step_instance is None:
        raise OrchestrationError(f"Task {task_id} has no step instance")
    step_instance.status = "completed"
    step_instance.completed_at = now
    step_instance.output_data = {"action": action, "resolution": resolution or {}}

    instance = await _require_instance(db, step_instance.workflow_instance_id)
    instance.current_step_instance_id = step_instance.id
    await _log_instance_event(
        db, instance.id, "instance.task_completed",
        {"task_id": str(task.id), "action": action},
        actor_id=actor_id,
        step_instance_id=step_instance.id,
    )
    instance.status = OrchInstanceStatus.RUNNING
    await db.flush()
    await _advance(db, instance, actor_id=actor_id)
    return instance


async def suspend_instance(db: AsyncSession, instance_id: UUID) -> OrchWorkflowInstance:
    instance = await _require_instance(db, instance_id)
    if instance.status not in (OrchInstanceStatus.RUNNING, OrchInstanceStatus.WAITING):
        raise OrchestrationError(f"Instance {instance_id} cannot be suspended")
    instance.status = OrchInstanceStatus.SUSPENDED
    await _log_instance_event(db, instance_id, "instance.suspended", {})
    await db.flush()
    return instance


async def resume_instance(db: AsyncSession, instance_id: UUID) -> OrchWorkflowInstance:
    instance = await _require_instance(db, instance_id)
    if instance.status != OrchInstanceStatus.SUSPENDED:
        raise OrchestrationError(f"Instance {instance_id} is not suspended")
    instance.status = OrchInstanceStatus.RUNNING
    await _log_instance_event(db, instance_id, "instance.resumed", {})
    await db.flush()
    await _advance(db, instance)
    return instance


async def cancel_instance(db: AsyncSession, instance_id: UUID) -> OrchWorkflowInstance:
    instance = await _require_instance(db, instance_id)
    if instance.status in (OrchInstanceStatus.COMPLETED, OrchInstanceStatus.CANCELLED):
        raise OrchestrationError(f"Instance {instance_id} is already closed")
    instance.status = OrchInstanceStatus.CANCELLED
    instance.completed_at = datetime.now(timezone.utc)
    await _log_instance_event(db, instance_id, "instance.cancelled", {})
    await db.flush()
    return instance


async def complete_instance(db: AsyncSession, instance_id: UUID) -> OrchWorkflowInstance:
    instance = await _require_instance(db, instance_id)
    if instance.status not in (
        OrchInstanceStatus.RUNNING,
        OrchInstanceStatus.WAITING,
        OrchInstanceStatus.SUSPENDED,
    ):
        raise OrchestrationError(f"Instance {instance_id} cannot be completed")
    instance.status = OrchInstanceStatus.COMPLETED
    instance.completed_at = datetime.now(timezone.utc)
    await _log_instance_event(db, instance_id, "instance.completed_manually", {})
    await db.flush()
    return instance


async def resolve_incident(
    db: AsyncSession,
    incident_id: UUID,
    *,
    resolution: str | None = None,
    actor_id: UUID | None = None,
) -> OrchIncident:
    """Close an incident and retry its failed step (spec §2.11.33)."""

    incident = await db.get(OrchIncident, incident_id)
    if incident is None:
        raise OrchestrationError(f"Incident {incident_id} not found")
    if incident.status != "open":
        raise OrchestrationError(f"Incident {incident_id} is not open")

    incident.status = "resolved"
    incident.resolved_at = datetime.now(timezone.utc)

    instance = await _require_instance(db, incident.workflow_instance_id)
    if instance.status == OrchInstanceStatus.HUMAN_INTERVENTION_REQUIRED:
        instance.status = OrchInstanceStatus.RUNNING
        await _log_instance_event(
            db, instance.id, "instance.retrying",
            {"incident_id": str(incident.id), "resolution": resolution},
            actor_id=actor_id,
        )
        retried_action = False
        if incident.step_instance_id is not None:
            failed_step = await db.get(OrchStepInstance, incident.step_instance_id)
            if failed_step is not None:
                step_def = await db.get(OrchStepDefinition, failed_step.step_definition_id)
                if step_def is not None and step_def.step_type == OrchStepType.ACTION:
                    retried_action = True
                    await db.flush()
                    await _resume_action_retry(
                        db, instance, failed_step,
                        fired_at=datetime.now(timezone.utc),
                    )
        if not retried_action:
            await db.flush()
            await _advance(db, instance)
    return incident


async def _advance(
    db: AsyncSession,
    instance: OrchWorkflowInstance,
    *,
    actor_id: UUID | None = None,
) -> OrchWorkflowInstance:
    """Run steps until the instance waits, finishes, or fails (spec §2.11.25)."""

    executions = 0
    while instance.status == OrchInstanceStatus.RUNNING:
        executions += 1
        if executions > MAX_STEP_EXECUTIONS:
            await _raise_incident(
                db, instance, "runaway_step_loop",
                "Aborted after 200 step executions without reaching a wait point",
            )
            break

        current_step_id = instance.current_step_instance_id
        next_step = await get_next_step(db, instance, current_step_id)

        if next_step is None:
            await _finish(db, instance)
            break

        step_instance = await _execute_step(db, instance, next_step, actor_id=actor_id)
        if step_instance is None:
            continue
        if step_instance.status in ("waiting", "retry_scheduled"):
            instance.status = OrchInstanceStatus.WAITING
            await db.flush()
            break
        if instance.status in (
            OrchInstanceStatus.FAILED,
            OrchInstanceStatus.HUMAN_INTERVENTION_REQUIRED,
        ):
            await db.flush()
            break
        instance.current_step_instance_id = step_instance.id
        await db.flush()

    return instance


async def get_next_step(
    db: AsyncSession,
    instance: OrchWorkflowInstance,
    current_step_instance_id: UUID | None,
) -> OrchStepDefinition | None:
    """Choose the next step from outgoing transitions (spec §2.11.25).

    The very first step is the one with no incoming transitions (ties broken
    by ``order_index``). For every other step, outgoing transitions are tested
    in ``priority`` order against ``instance.context``; the first passing
    transition wins. No passing transition (or no outgoing edges) terminates
    the workflow.
    """

    definition_id = instance.workflow_definition_id

    if current_step_instance_id is None:
        result = await db.execute(
            select(OrchTransition).where(
                OrchTransition.workflow_definition_id == definition_id
            )
        )
        all_transitions = list(result.scalars().all())
        incoming = {t.to_step_key for t in all_transitions}
        steps = await db.execute(
            select(OrchStepDefinition).where(
                OrchStepDefinition.workflow_definition_id == definition_id
            )
        )
        candidates = [s for s in steps.scalars().all() if s.step_key not in incoming]
        if not candidates:
            return None
        return min(candidates, key=lambda s: s.order_index)

    from_step = await db.get(OrchStepInstance, current_step_instance_id)
    if from_step is None:
        return None
    step_def = await db.get(OrchStepDefinition, from_step.step_definition_id)
    if step_def is None:
        return None

    result = await db.execute(
        select(OrchTransition)
        .where(
            OrchTransition.workflow_definition_id == definition_id,
            OrchTransition.from_step_key == step_def.step_key,
        )
        .order_by(OrchTransition.priority.asc())
    )
    transitions = list(result.scalars().all())
    for transition in transitions:
        if evaluate_condition(transition.condition, instance.context):
            target = await db.execute(
                select(OrchStepDefinition).where(
                    OrchStepDefinition.workflow_definition_id == definition_id,
                    OrchStepDefinition.step_key == transition.to_step_key,
                )
            )
            next_def = target.scalar_one_or_none()
            if next_def is not None:
                return next_def
    return None


async def _execute_step(
    db: AsyncSession,
    instance: OrchWorkflowInstance,
    step_def: OrchStepDefinition,
    *,
    actor_id: UUID | None = None,
    retry_from: OrchStepInstance | None = None,
) -> OrchStepInstance | None:
    """Create and run one step instance. Returns the created row, or None."""

    step_instance = OrchStepInstance(
        workflow_instance_id=instance.id,
        step_definition_id=step_def.id,
        status="running",
        input_data=_deepish_copy(instance.context),
        current_attempt=(retry_from.current_attempt if retry_from else 0) + 1,
        loop_iteration=(retry_from.loop_iteration if retry_from else 0),
        started_at=datetime.now(timezone.utc),
    )
    db.add(step_instance)
    await db.flush()

    try:
        if step_def.step_type == OrchStepType.ACTION:
            await _run_action_step(
                db, instance, step_def, step_instance,
                current_attempt=step_instance.current_attempt or 1,
            )
        elif step_def.step_type == OrchStepType.CONDITION:
            await _run_condition_step(db, instance, step_def, step_instance)
        elif step_def.step_type == OrchStepType.TASK:
            await _run_task_step(db, instance, step_def, step_instance, kind="TASK")
        elif step_def.step_type == OrchStepType.APPROVAL:
            await _run_task_step(db, instance, step_def, step_instance, kind="APPROVAL")
        elif step_def.step_type == OrchStepType.DELAY:
            await _run_delay_step(db, instance, step_def, step_instance)
        elif step_def.step_type == OrchStepType.EVENT_WAIT:
            await _run_event_wait_step(db, instance, step_def, step_instance)
        elif step_def.step_type == OrchStepType.PARALLEL:
            await _run_parallel_step(db, instance, step_def, step_instance)
        elif step_def.step_type == OrchStepType.SUBWORKFLOW:
            raise OrchestrationError(
                "SUBWORKFLOW step type is not yet implemented by the orchestrator"
            )
        else:
            raise OrchestrationError(f"Unsupported step type: {step_def.step_type!r}")
    except (OrchestrationError, WorkflowActionError, ValueError) as exc:
        await _handle_step_failure(
            db, instance, step_def, step_instance, str(exc), actor_id=actor_id
        )
        return None

    await _log_instance_event(
        db, instance.id, "instance.step_" + step_instance.status,
        {"step_key": step_def.step_key, "step_type": step_def.step_type.value},
        actor_id=actor_id,
        step_instance_id=step_instance.id,
    )
    return step_instance


async def _run_action_step(
    db: AsyncSession,
    instance: OrchWorkflowInstance,
    step_def: OrchStepDefinition,
    step_instance: OrchStepInstance,
    *,
    current_attempt: int = 1,
) -> None:
    """Run one attempt of an ACTION step (spec §2.11.16/2.11.28)."""

    config = step_def.configuration or {}
    action_key = config.get("action")
    if not action_key:
        raise OrchestrationError(f"ACTION step {step_def.step_key} has no 'action' key")

    action = get_action(action_key)
    max_attempts = int(config.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
    backoff = int(config.get("backoff_seconds") or DEFAULT_BACKOFF_SECONDS)

    attempt_row = OrchActionAttempt(
        step_instance_id=step_instance.id,
        attempt_number=current_attempt,
        status="running",
        request_metadata=_deepish_copy(config),
    )
    db.add(attempt_row)
    await db.flush()
    try:
        output = await action.execute(db, config, instance.context, step_instance.id)
    except Exception as exc:  # noqa: BLE001 - surfaced into workflow state
        attempt_row.status = "failed"
        attempt_row.error = f"{type(exc).__name__}: {exc}"
        await db.flush()
        if current_attempt >= max_attempts:
            raise OrchestrationError(
                f"Action {action_key} failed after {current_attempt} attempts: {attempt_row.error}"
            ) from exc
        wait = backoff * current_attempt
        timer = OrchTimer(
            workflow_step_instance_id=step_instance.id,
            scheduled_at=datetime.now(timezone.utc) + timedelta(seconds=wait),
            status="pending",
            config={"reason": _RETRY_TIMER_REASON, "backoff_seconds": wait},
        )
        db.add(timer)
        step_instance.status = "retry_scheduled"
        step_instance.error_data = {"error": attempt_row.error, "retry_in": wait}
        await _log_instance_event(
            db, instance.id, "instance.step_retry_scheduled",
            {
                "step_key": step_def.step_key,
                "attempt": current_attempt,
                "next_attempt": current_attempt + 1,
                "backoff_seconds": wait,
            },
            step_instance_id=step_instance.id,
        )
        await db.flush()
        return

    attempt_row.status = "succeeded"
    attempt_row.response_metadata = _deepish_copy(output)
    step_instance.output_data = output
    step_instance.status = "completed"
    step_instance.completed_at = datetime.now(timezone.utc)
    _merge_step_output(instance, step_def.step_key, output)
    await db.flush()


async def _run_condition_step(
    db: AsyncSession,
    instance: OrchWorkflowInstance,
    step_def: OrchStepDefinition,
    step_instance: OrchStepInstance,
) -> None:
    config = step_def.configuration or {}
    condition = config.get("condition")
    if condition is None:
        raise OrchestrationError(
            f"CONDITION step {step_def.step_key} has no 'condition' config"
        )
    result = evaluate_condition(condition, instance.context)
    step_instance.output_data = {"result": result}
    step_instance.status = "completed"
    step_instance.completed_at = datetime.now(timezone.utc)
    _merge_step_output(instance, step_def.step_key, {"result": result})
    await db.flush()


async def _run_task_step(
    db: AsyncSession,
    instance: OrchWorkflowInstance,
    step_def: OrchStepDefinition,
    step_instance: OrchStepInstance,
    *,
    kind: str,
) -> None:
    config = dict(step_def.configuration or {})
    config.setdefault("task_type", kind)
    output = await CreateTaskAction().execute(db, config, instance.context, step_instance.id)
    step_instance.output_data = output
    step_instance.status = "waiting"
    _merge_step_output(instance, step_def.step_key, output)
    await db.flush()


async def _run_delay_step(
    db: AsyncSession,
    instance: OrchWorkflowInstance,
    step_def: OrchStepDefinition,
    step_instance: OrchStepInstance,
) -> None:
    config = step_def.configuration or {}
    seconds = config.get("seconds")
    if not seconds or int(seconds) <= 0:
        raise OrchestrationError(f"DELAY step {step_def.step_key} needs positive 'seconds'")
    absolute = config.get("until") or config.get("at") or config.get("datetime")
    if absolute:
        try:
            scheduled = datetime.fromisoformat(str(absolute).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise OrchestrationError(
                f"DELAY step {step_def.step_key} has invalid 'until' datetime: {absolute}"
            ) from exc
    else:
        scheduled = datetime.now(timezone.utc) + timedelta(seconds=int(seconds))

    timer = OrchTimer(
        workflow_step_instance_id=step_instance.id,
        scheduled_at=scheduled,
        status="pending",
        config={"seconds": int(seconds), "until": absolute},
    )
    db.add(timer)
    step_instance.output_data = {"timer_id": str(timer.id), "scheduled_at": scheduled.isoformat()}
    step_instance.status = "waiting"
    await db.flush()


async def _run_event_wait_step(
    db: AsyncSession,
    instance: OrchWorkflowInstance,
    step_def: OrchStepDefinition,
    step_instance: OrchStepInstance,
) -> None:
    config = step_def.configuration or {}
    event_type = config.get("event_type")
    if not event_type:
        raise OrchestrationError(f"EVENT_WAIT step {step_def.step_key} has no 'event_type'")
    scope_to_agreement = bool(
        config.get("scope_agreement") or instance.agreement_id
    )
    wait = OrchEventWait(
        workflow_step_instance_id=step_instance.id,
        event_type=event_type,
        aggregate_type=config.get("aggregate_type"),
        aggregate_id=config.get("aggregate_id") or (instance.agreement_id if scope_to_agreement else None),
        correlation_key=config.get("correlation_key"),
        active=True,
    )
    db.add(wait)
    step_instance.output_data = {"wait_id": str(wait.id), "event_type": event_type}
    step_instance.status = "waiting"
    await db.flush()


async def _run_parallel_step(
    db: AsyncSession,
    instance: OrchWorkflowInstance,
    step_def: OrchStepDefinition,
    step_instance: OrchStepInstance,
) -> None:
    """Run branch steps in deterministic order (spec §2.11.44 simplified).

    ``configuration.branches`` lists step keys. Each branch is executed as its
    own step instance immediately (no true concurrency yet — see report). The
    orchestrator then continues from the PARALLEL step using its transitions.
    """

    config = step_def.configuration or {}
    branches = config.get("branches") or []
    if not branches:
        raise OrchestrationError(f"PARALLEL step {step_def.step_key} needs 'branches'")

    definition = await db.get(OrchWorkflowDefinition, instance.workflow_definition_id)
    if definition is None:
        raise OrchestrationError("Workflow definition missing")

    branch_results: dict[str, Any] = {}
    for branch_key in branches:
        result = await db.execute(
            select(OrchStepDefinition).where(
                OrchStepDefinition.workflow_definition_id == definition.id,
                OrchStepDefinition.step_key == branch_key,
            )
        )
        branch_step = result.scalar_one_or_none()
        if branch_step is None:
            raise OrchestrationError(
                f"PARALLEL step {step_def.step_key} references unknown branch {branch_key!r}"
            )
        branch_step_instance = await _execute_step(db, instance, branch_step)
        if branch_step_instance is not None:
            branch_results[branch_key] = branch_step_instance.output_data
            if branch_step_instance.status == "completed":
                _merge_step_output(instance, branch_key, branch_step_instance.output_data)

    step_instance.output_data = {"branches": list(branches), "results": branch_results}
    step_instance.status = "completed"
    if step_instance.started_at:
        step_instance.completed_at = datetime.now(timezone.utc)
    await db.flush()


async def _resume_action_retry(
    db: AsyncSession,
    instance: OrchWorkflowInstance,
    step_instance: OrchStepInstance,
    *,
    fired_at: datetime,
) -> None:
    """Run the next attempt of a failed ACTION step after back-off."""

    step_def = await db.get(OrchStepDefinition, step_instance.step_definition_id)
    if step_def is None:
        raise OrchestrationError("Step definition missing during retry")

    await _log_instance_event(
        db, instance.id, "instance.step_attempting_retry",
        {"step_key": step_def.step_key, "attempt": step_instance.current_attempt + 1},
        step_instance_id=step_instance.id,
    )
    await db.flush()

    new_step = await _execute_step(
        db, instance, step_def, retry_from=step_instance, actor_id=None
    )
    if new_step is None:
        instance.status = OrchInstanceStatus.HUMAN_INTERVENTION_REQUIRED
        await db.flush()
        return
    if new_step.status == "completed":
        instance.current_step_instance_id = new_step.id
        instance.status = OrchInstanceStatus.RUNNING
        await db.flush()
        await _advance(db, instance)
    elif new_step.status in ("waiting", "retry_scheduled"):
        instance.status = OrchInstanceStatus.WAITING
        instance.current_step_instance_id = new_step.id
        await db.flush()


async def _handle_step_failure(
    db: AsyncSession,
    instance: OrchWorkflowInstance,
    step_def: OrchStepDefinition,
    step_instance: OrchStepInstance,
    error: str,
    *,
    actor_id: UUID | None = None,
) -> None:
    step_instance.status = "failed"
    step_instance.error_data = {"error": error}
    incident = OrchIncident(
        workflow_instance_id=instance.id,
        step_instance_id=step_instance.id,
        incident_type="step_failure",
        severity="high",
        description=f"{step_def.step_key}: {error}",
        status="open",
    )
    db.add(incident)
    instance.status = OrchInstanceStatus.HUMAN_INTERVENTION_REQUIRED
    await _log_instance_event(
        db, instance.id, "instance.failed",
        {"step_key": step_def.step_key, "error": error},
        actor_id=actor_id,
        step_instance_id=step_instance.id,
    )
    await db.flush()


async def _raise_incident(
    db: AsyncSession,
    instance: OrchWorkflowInstance,
    incident_type: str,
    description: str,
) -> None:
    incident = OrchIncident(
        workflow_instance_id=instance.id,
        incident_type=incident_type,
        severity="critical",
        description=description,
        status="open",
    )
    db.add(incident)
    instance.status = OrchInstanceStatus.HUMAN_INTERVENTION_REQUIRED
    await _log_instance_event(db, instance.id, "instance.failed", {"error": description})


async def _finish(db: AsyncSession, instance: OrchWorkflowInstance) -> None:
    instance.status = OrchInstanceStatus.COMPLETED
    instance.completed_at = datetime.now(timezone.utc)
    await _log_instance_event(db, instance.id, "instance.completed", {})
    await db.flush()


def _merge_step_output(
    instance: OrchWorkflowInstance,
    step_key: str,
    output: dict[str, Any] | None,
) -> None:
    # Rebuild the context dict so the JSONB column is marked dirty: in-place
    # mutation of a loaded JSON document is invisible to SQLAlchemy.
    updated = dict(instance.context or {})
    merged_steps = dict(updated.get("steps") or {})
    merged_steps[step_key] = _deepish_copy(dict(output or {}))
    updated["steps"] = merged_steps
    if output and "context" in output and isinstance(output["context"], dict):
        for key, value in output["context"].items():
            updated[key] = value
    instance.context = updated


async def _resolve_matching_definitions(
    db: AsyncSession,
    *,
    event_type: str,
    organization_id: UUID | None,
    aggregate_type: str | None,
    payload: dict[str, Any],
) -> list[OrchWorkflowDefinition]:
    """Find ACTIVE definitions whose trigger matches the event (spec §2.11.8)."""

    result = await db.execute(
        select(OrchWorkflowDefinition).where(
            OrchWorkflowDefinition.status == OrchWorkflowStatus.ACTIVE
        )
    )
    candidates = list(result.scalars().all())

    matched: list[OrchWorkflowDefinition] = []
    for definition in candidates:
        trigger = definition.trigger or {}
        if trigger.get("event_type") and trigger["event_type"] != event_type:
            continue
        scope = definition.scope.value if hasattr(definition.scope, "value") else str(definition.scope or "")
        if scope == "organization":
            if not organization_id or definition.organization_id != organization_id:
                continue
        elif scope == "agreement_type":
            want_type = trigger.get("agreement_type")
            got_type = payload.get("agreement_type")
            if want_type and got_type != want_type:
                continue
        trigger_condition = trigger.get("condition")
        if trigger_condition and not evaluate_condition(trigger_condition, payload):
            continue
        matched.append(definition)
    return matched


async def _instance_exists_for_event(
    db: AsyncSession, definition_id: UUID, event_id: UUID
) -> bool:
    result = await db.execute(
        select(OrchWorkflowInstance.id).where(
            OrchWorkflowInstance.workflow_definition_id == definition_id,
            OrchWorkflowInstance.source_event_id == event_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def _require_instance(
    db: AsyncSession, instance_id: UUID
) -> OrchWorkflowInstance:
    instance = await db.get(OrchWorkflowInstance, instance_id)
    if instance is None:
        raise OrchestrationError(f"Workflow instance {instance_id} not found")
    return instance


async def _log_instance_event(
    db: AsyncSession,
    instance_id: UUID,
    event_type: str,
    data: dict[str, Any],
    *,
    actor_id: UUID | None = None,
    step_instance_id: UUID | None = None,
) -> None:
    db.add(
        OrchWorkflowEventLog(
            workflow_instance_id=instance_id,
            event_type=event_type,
            step_instance_id=step_instance_id,
            actor_id=actor_id,
            data=data,
        )
    )


def _deepish_copy(value: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(value)


def _uuid_or_none(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None