"""Workflow definition/instance CRUD and lifecycle service (spec §2.11.45).

Definition publishing (spec §2.11.38): the active definition is immutable —
publishing creates a new ACTIVE version copying the current step graph and
marks the previously active version DISABLED. Instances keep the definition id
they started from, so the published graph can never rewrite a live run.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orchestration import (
    OrchActionAttempt,
    OrchDeadLetter,
    OrchIncident,
    OrchInstanceStatus,
    OrchStepDefinition,
    OrchStepInstance,
    OrchTask,
    OrchTransition,
    OrchWorkflowDefinition,
    OrchWorkflowEventLog,
    OrchWorkflowInstance,
    OrchWorkflowStatus,
)


class WorkflowDefinitionError(Exception):
    """Raised for invalid lifecycle transitions on definitions."""


async def create_workflow_definition(
    db: AsyncSession,
    *,
    code: str,
    name: str,
    description: str | None,
    scope: str,
    trigger: dict[str, Any],
    configuration: dict[str, Any] | None,
    steps: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    actor_id: UUID | None = None,
    organization_id: UUID | None = None,
) -> OrchWorkflowDefinition:
    """Create a DRAFT v1 definition with its step graph."""

    from app.models.orchestration import OrchScope

    definition = OrchWorkflowDefinition(
        organization_id=organization_id,
        code=code,
        name=name,
        description=description,
        version=1,
        status=OrchWorkflowStatus.DRAFT,
        scope=OrchScope(scope),
        trigger=trigger or {},
        configuration=configuration or {},
        created_by_user_id=actor_id,
    )
    db.add(definition)
    await db.flush()
    await _apply_step_graph(db, definition, steps, transitions)
    return definition


async def get_workflow_definition(
    db: AsyncSession, definition_id: UUID
) -> OrchWorkflowDefinition | None:
    return await db.get(OrchWorkflowDefinition, definition_id)


async def list_workflow_definitions(
    db: AsyncSession,
    *,
    organization_id: UUID | None = None,
    include_inactive: bool = False,
) -> list[OrchWorkflowDefinition]:
    query = select(OrchWorkflowDefinition).order_by(
        OrchWorkflowDefinition.code.asc(),
        OrchWorkflowDefinition.version.desc(),
    )
    if organization_id is not None:
        query = query.where(
            (OrchWorkflowDefinition.organization_id.is_(None))
            | (OrchWorkflowDefinition.organization_id == organization_id)
        )
    if not include_inactive:
        query = query.where(OrchWorkflowDefinition.status == OrchWorkflowStatus.ACTIVE)
    result = await db.execute(query)
    return list(result.scalars().all())


async def update_workflow_definition(
    db: AsyncSession,
    definition_id: UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    trigger: dict[str, Any] | None = None,
    configuration: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
    transitions: list[dict[str, Any]] | None = None,
) -> OrchWorkflowDefinition:
    """Edit a definition. Only DRAFT definitions may be changed."""

    definition = await db.get(OrchWorkflowDefinition, definition_id)
    if definition is None:
        raise WorkflowDefinitionError(f"Definition {definition_id} not found")
    if definition.status != OrchWorkflowStatus.DRAFT:
        raise WorkflowDefinitionError(
            "Only DRAFT definitions can be edited; publish a new version instead"
        )
    if name is not None:
        definition.name = name
    if description is not None:
        definition.description = description
    if trigger is not None:
        definition.trigger = trigger
    if configuration is not None:
        definition.configuration = configuration
    if steps is not None:
        await _apply_step_graph(db, definition, steps, transitions or [])
    elif transitions is not None:
        await _apply_step_graph(db, definition, [], transitions)
    await db.flush()
    return definition


async def validate_definition(
    db: AsyncSession, definition_id: UUID
) -> dict[str, Any]:
    from app.services.orchestration_validation import validate_workflow_definition

    return await validate_workflow_definition(db, definition_id)


async def simulate_definition(
    db: AsyncSession,
    definition_id: UUID,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.services.orchestration_validation import simulate_workflow

    return await simulate_workflow(db, definition_id, context)


async def publish_workflow_definition(
    db: AsyncSession, definition_id: UUID, *, actor_id: UUID | None = None
) -> OrchWorkflowDefinition:
    """Validate then publish ``definition`` as a new ACTIVE version (spec §2.11.38)."""

    definition = await db.get(OrchWorkflowDefinition, definition_id)
    if definition is None:
        raise WorkflowDefinitionError(f"Definition {definition_id} not found")
    if definition.status != OrchWorkflowStatus.DRAFT:
        raise WorkflowDefinitionError("Only DRAFT definitions can be published")

    result = await validate_definition(db, definition.id)
    if not result["valid"]:
        raise WorkflowDefinitionError(
            f"Definition {definition.code} failed validation: {'; '.join(result['errors'])}"
        )

    current_active = await db.execute(
        select(OrchWorkflowDefinition).where(
            OrchWorkflowDefinition.code == definition.code,
            OrchWorkflowDefinition.status == OrchWorkflowStatus.ACTIVE,
            (
                (OrchWorkflowDefinition.organization_id.is_(None))
                if definition.organization_id is None
                else (OrchWorkflowDefinition.organization_id == definition.organization_id)
            ),
        )
    )
    for active in current_active.scalars().all():
        active.status = OrchWorkflowStatus.DISABLED

    next_version_result = await db.execute(
        select(func.max(OrchWorkflowDefinition.version)).where(
            OrchWorkflowDefinition.code == definition.code
        )
    )
    next_version = (next_version_result.scalar() or 0) + 1

    new_version = OrchWorkflowDefinition(
        organization_id=definition.organization_id,
        code=definition.code,
        name=definition.name,
        description=definition.description,
        version=next_version,
        status=OrchWorkflowStatus.ACTIVE,
        scope=definition.scope,
        trigger=dict(definition.trigger or {}),
        configuration=dict(definition.configuration or {}),
        created_by_user_id=actor_id or definition.created_by_user_id,
    )
    db.add(new_version)
    await db.flush()

    steps_result = await db.execute(
        select(OrchStepDefinition).where(
            OrchStepDefinition.workflow_definition_id == definition.id
        )
    )
    for step in steps_result.scalars().all():
        db.add(
            OrchStepDefinition(
                workflow_definition_id=new_version.id,
                step_key=step.step_key,
                name=step.name,
                step_type=step.step_type,
                configuration=dict(step.configuration or {}),
                order_index=step.order_index,
                required=step.required,
                timeout_seconds=step.timeout_seconds,
            )
        )
    await db.flush()

    transitions_result = await db.execute(
        select(OrchTransition).where(
            OrchTransition.workflow_definition_id == definition.id
        )
    )
    for transition in transitions_result.scalars().all():
        db.add(
            OrchTransition(
                workflow_definition_id=new_version.id,
                from_step_key=transition.from_step_key,
                to_step_key=transition.to_step_key,
                condition=transition.condition,
                priority=transition.priority,
            )
        )
    await db.flush()
    return new_version


async def disable_workflow_definition(
    db: AsyncSession, definition_id: UUID
) -> OrchWorkflowDefinition:
    definition = await db.get(OrchWorkflowDefinition, definition_id)
    if definition is None:
        raise WorkflowDefinitionError(f"Definition {definition_id} not found")
    if definition.status not in (OrchWorkflowStatus.ACTIVE, OrchWorkflowStatus.DRAFT):
        raise WorkflowDefinitionError(f"Definition {definition_id} cannot be disabled")
    definition.status = OrchWorkflowStatus.DISABLED
    await db.flush()
    return definition


async def list_workflow_versions(
    db: AsyncSession, code: str, *, organization_id: UUID | None = None
) -> list[OrchWorkflowDefinition]:
    query = (
        select(OrchWorkflowDefinition)
        .where(OrchWorkflowDefinition.code == code)
        .order_by(OrchWorkflowDefinition.version.desc())
    )
    if organization_id is not None:
        query = query.where(
            (OrchWorkflowDefinition.organization_id.is_(None))
            | (OrchWorkflowDefinition.organization_id == organization_id)
        )
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_instances(
    db: AsyncSession,
    *,
    organization_id: UUID | None = None,
    status: str | None = None,
    definition_id: UUID | None = None,
    limit: int = 100,
) -> list[OrchWorkflowInstance]:
    query = select(OrchWorkflowInstance).order_by(
        OrchWorkflowInstance.created_at.desc()
    )
    if organization_id is not None:
        query = query.where(OrchWorkflowInstance.organization_id == organization_id)
    if status:
        query = query.where(OrchWorkflowInstance.status == OrchInstanceStatus(status))
    if definition_id:
        query = query.where(
            OrchWorkflowInstance.workflow_definition_id == definition_id
        )
    query = query.limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_workflow_instance(
    db: AsyncSession, instance_id: UUID
) -> OrchWorkflowInstance | None:
    return await db.get(OrchWorkflowInstance, instance_id)


async def get_instance_events(
    db: AsyncSession, instance_id: UUID
) -> list[OrchWorkflowEventLog]:
    result = await db.execute(
        select(OrchWorkflowEventLog)
        .where(OrchWorkflowEventLog.workflow_instance_id == instance_id)
        .order_by(OrchWorkflowEventLog.created_at.asc())
    )
    return list(result.scalars().all())


async def get_instance_steps(
    db: AsyncSession, instance_id: UUID
) -> list[OrchStepInstance]:
    result = await db.execute(
        select(OrchStepInstance)
        .where(OrchStepInstance.workflow_instance_id == instance_id)
        .order_by(OrchStepInstance.created_at.asc())
    )
    return list(result.scalars().all())


async def get_instance_trace(
    db: AsyncSession, instance_id: UUID
) -> dict[str, Any]:
    steps = await get_instance_steps(db, instance_id)
    trace: list[dict[str, Any]] = []
    step_ids = [s.id for s in steps]
    if step_ids:
        attempts = await db.execute(
            select(OrchActionAttempt).where(
                OrchActionAttempt.step_instance_id.in_(step_ids)
            )
        )
        attempts_by_step: dict[UUID, list[OrchActionAttempt]] = {}
        for attempt in attempts.scalars().all():
            attempts_by_step.setdefault(attempt.step_instance_id, []).append(attempt)
    else:
        attempts_by_step = {}

    for step in steps:
        step_def = None
        if step.step_definition_id:
            step_def = await db.get(OrchStepDefinition, step.step_definition_id)
        trace.append(
            {
                "step_instance_id": str(step.id),
                "step_key": step_def.step_key if step_def else None,
                "step_type": step_def.step_type.value if step_def and hasattr(step_def.step_type, "value") else None,
                "status": step.status,
                "started_at": step.started_at.isoformat() if step.started_at else None,
                "completed_at": step.completed_at.isoformat() if step.completed_at else None,
                "attempts": [
                    {
                        "attempt_number": a.attempt_number,
                        "status": a.status,
                        "error": a.error,
                    }
                    for a in attempts_by_step.get(step.id, [])
                ],
            }
        )
    return {"steps": trace}


async def list_tasks(
    db: AsyncSession,
    *,
    user_id: UUID | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[OrchTask]:
    query = select(OrchTask).order_by(OrchTask.created_at.asc())
    if user_id is not None:
        query = query.where(OrchTask.assignee_user_id == user_id)
    if status:
        query = query.where(OrchTask.status == status)
    query = query.limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_incidents(
    db: AsyncSession, *, status: str | None = None, limit: int = 100
) -> list[OrchIncident]:
    query = select(OrchIncident).order_by(OrchIncident.created_at.desc())
    if status:
        query = query.where(OrchIncident.status == status)
    query = query.limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_dead_letters(
    db: AsyncSession, *, limit: int = 100
) -> list[OrchDeadLetter]:
    result = await db.execute(
        select(OrchDeadLetter)
        .order_by(OrchDeadLetter.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def _apply_step_graph(
    db: AsyncSession,
    definition: OrchWorkflowDefinition,
    steps: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
) -> None:
    """Synchronise the DRAFT definition's steps and transitions.

    Existing steps are replaced by ``step_key``; removed keys are deleted. The
    same applies to transitions (matched by from/to/priority).
    """

    from app.models.orchestration import OrchStepType

    existing = await db.execute(
        select(OrchStepDefinition).where(
            OrchStepDefinition.workflow_definition_id == definition.id
        )
    )
    step_rows = list(existing.scalars().all())
    by_key = {s.step_key: s for s in step_rows}

    wanted_keys = {s.get("step_key") for s in steps}
    for row in step_rows:
        if row.step_key not in wanted_keys:
            await db.delete(row)

    for order, item in enumerate(steps):
        step_key = item.get("step_key")
        if not step_key:
            raise WorkflowDefinitionError("Every step needs a 'step_key'")
        step_type = item.get("step_type")
        if step_type not in {t.value for t in OrchStepType}:
            raise WorkflowDefinitionError(f"Unknown step type {step_type!r}")
        row = by_key.get(step_key)
        if row is None:
            row = OrchStepDefinition(
                workflow_definition_id=definition.id,
                step_key=step_key,
                name=item.get("name") or step_key,
                step_type=OrchStepType(step_type),
                configuration=item.get("configuration") or {},
                order_index=order,
                required=bool(item.get("required", True)),
                timeout_seconds=item.get("timeout_seconds"),
            )
            db.add(row)
        else:
            row.name = item.get("name") or row.name
            row.step_type = OrchStepType(step_type)
            row.configuration = item.get("configuration") or {}
            row.order_index = order
            row.required = bool(item.get("required", True))
            row.timeout_seconds = item.get("timeout_seconds")
    await db.flush()

    existing_trans = await db.execute(
        select(OrchTransition).where(
            OrchTransition.workflow_definition_id == definition.id
        )
    )
    trans_rows = list(existing_trans.scalars().all())
    wanted_trans = {
        (t.get("from_step_key"), t.get("to_step_key"), int(t.get("priority") or 0))
        for t in transitions
    }
    for row in trans_rows:
        if (row.from_step_key, row.to_step_key, row.priority) not in wanted_trans:
            await db.delete(row)

    for item in transitions:
        from_step = item.get("from_step_key")
        to_step = item.get("to_step_key")
        priority = int(item.get("priority") or 0)
        if not from_step or not to_step:
            raise WorkflowDefinitionError("Every transition needs from_step_key and to_step_key")
        exists = next(
            (
                r
                for r in trans_rows
                if (r.from_step_key, r.to_step_key, r.priority)
                == (from_step, to_step, priority)
            ),
            None,
        )
        if exists is None:
            db.add(
                OrchTransition(
                    workflow_definition_id=definition.id,
                    from_step_key=from_step,
                    to_step_key=to_step,
                    condition=item.get("condition"),
                    priority=priority,
                )
            )
        else:
            exists.condition = item.get("condition")
    await db.flush()