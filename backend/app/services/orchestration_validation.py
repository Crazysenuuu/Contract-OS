"""Workflow definition validation and dry-run simulation (spec §2.11.39/2.11.43).

Validation is static: it inspects the step graph and every step's
configuration — including that ACTION steps reference a *registered* action —
but performs no side effects. Simulation is a walk of the graph with condition
evaluation only; action/task/delay/event steps are recorded as wait points.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orchestration import (
    OrchStepDefinition,
    OrchStepType,
    OrchTransition,
    OrchWorkflowDefinition,
)

MAX_SIMULATION_STEPS = 100

SUPPORTED_STEP_TYPES = frozenset(t.value for t in OrchStepType)


async def validate_workflow_definition(
    db: AsyncSession, definition_id: Any
) -> dict[str, Any]:
    """Validate a workflow definition's graph. Returns ``{valid, errors}``."""

    errors: list[str] = []

    definition = await db.get(OrchWorkflowDefinition, definition_id)
    if definition is None:
        return {"valid": False, "errors": [f"Definition {definition_id} not found"]}

    result = await db.execute(
        select(OrchStepDefinition)
        .where(OrchStepDefinition.workflow_definition_id == definition.id)
        .order_by(OrchStepDefinition.order_index.asc())
    )
    steps = list(result.scalars().all())

    if not steps:
        errors.append("Workflow definition has no steps")

    step_keys = [s.step_key for s in steps]
    if len(step_keys) != len(set(step_keys)):
        errors.append("Duplicate step keys are not allowed")

    result = await db.execute(
        select(OrchTransition).where(
            OrchTransition.workflow_definition_id == definition.id
        )
    )
    transitions = list(result.scalars().all())

    for step in steps:
        if step.step_type.value not in SUPPORTED_STEP_TYPES:
            errors.append(f"Step {step.step_key!r}: unsupported step type {step.step_type!r}")
        _validate_step_config(
            definition, step, transition_sources={
                t.from_step_key for t in transitions if t.from_step_key == step.step_key
            },
            step_keys=step_keys,
            errors=errors,
        )

    transition_steps = {t.to_step_key for t in transitions}.union(
        {t.from_step_key for t in transitions}
    )
    for transition in transitions:
        if transition.from_step_key not in step_keys:
            errors.append(
                f"Transition from {transition.from_step_key!r} references unknown step"
            )
        if transition.to_step_key not in step_keys:
            errors.append(
                f"Transition to {transition.to_step_key!r} references unknown step"
            )
        if transition.condition is not None:
            _validate_condition(
                transition.condition,
                f"transition {transition.from_step_key}->{transition.to_step_key}",
                errors,
            )

    incoming = {t.to_step_key for t in transitions}
    starts = [s for s in steps if s.step_key not in incoming]
    if not starts:
        errors.append("Workflow has no start step (every step has an incoming transition)")

    for step in steps:
        if step.step_key not in transition_steps and not (starts and step.step_key in {s.step_key for s in starts}):
            errors.append(f"Step {step.step_key!r} is unreachable (no incoming transition)")

    return {"valid": not errors, "errors": errors}


def _validate_step_config(
    definition: OrchWorkflowDefinition,
    step: OrchStepDefinition,
    *,
    transition_sources: set[str],
    step_keys: list[str],
    errors: list[str],
) -> None:
    config = step.configuration or {}

    if step.step_type == OrchStepType.ACTION:
        action_key = config.get("action")
        if not action_key:
            errors.append(f"Step {step.step_key!r}: ACTION step needs 'action' key")
            return
        from app.services.orchestration_actions import PLANNED_ACTIONS, ACTION_REGISTRY

        if action_key not in ACTION_REGISTRY:
            if action_key in PLANNED_ACTIONS:
                errors.append(
                    f"Step {step.step_key!r}: action {action_key!r} is spec-planned but not implemented"
                )
            else:
                errors.append(f"Step {step.step_key!r}: unknown action {action_key!r}")
        if config.get("max_attempts", 1) < 1:
            errors.append(f"Step {step.step_key!r}: max_attempts must be >= 1")
        if config.get("on_success") and config["on_success"] not in step_keys:
            errors.append(f"Step {step.step_key!r}: on_success references unknown step")

    elif step.step_type == OrchStepType.CONDITION:
        if not config.get("condition"):
            errors.append(f"Step {step.step_key!r}: CONDITION step needs 'condition'")
        else:
            _validate_condition(config["condition"], f"step {step.step_key!r}", errors)

    elif step.step_type == OrchStepType.DELAY:
        if not config.get("seconds") and not config.get("until") and not config.get("at"):
            errors.append(
                f"Step {step.step_key!r}: DELAY step needs 'seconds' or an absolute datetime"
            )

    elif step.step_type == OrchStepType.EVENT_WAIT:
        if not config.get("event_type"):
            errors.append(f"Step {step.step_key!r}: EVENT_WAIT step needs 'event_type'")

    elif step.step_type == OrchStepType.PARALLEL:
        branches = config.get("branches") or []
        if not branches:
            errors.append(f"Step {step.step_key!r}: PARALLEL step needs 'branches'")
        for branch in branches:
            if branch not in step_keys:
                errors.append(
                    f"Step {step.step_key!r}: PARALLEL branch {branch!r} references unknown step"
                )

    elif step.step_type == OrchStepType.SUBWORKFLOW:
        errors.append(
            f"Step {step.step_key!r}: SUBWORKFLOW step type is not yet implemented"
        )

    elif step.step_type in (OrchStepType.TASK, OrchStepType.APPROVAL):
        if config.get("assignee_user_id") and not config["assignee_user_id"]:
            errors.append(f"Step {step.step_key!r}: assignee_user_id cannot be empty")


def _validate_condition(
    condition: dict[str, Any], where: str, errors: list[str]
) -> None:
    from app.services.orchestration_conditions import Operators

    if not isinstance(condition, dict):
        errors.append(f"Condition on {where} is not an object")
        return
    operator = condition.get("operator")
    if operator not in Operators:
        errors.append(f"Condition on {where}: unknown operator {operator!r}")
        return
    if operator in ("AND", "OR"):
        for child in condition.get("children") or []:
            _validate_condition(child, where, errors)
    elif operator == "NOT":
        _validate_condition(condition.get("child") or {}, where, errors)
    else:
        if condition.get("field") is None:
            errors.append(f"Condition on {where}: operator {operator!r} needs 'field'")


async def simulate_workflow(
    db: AsyncSession,
    definition_id: Any,
    context: dict[str, Any] | None = None,
    *,
    max_steps: int = MAX_SIMULATION_STEPS,
) -> dict[str, Any]:
    """Dry-run a workflow against ``context`` without side effects.

    Returns trace entries per step plus a terminal state (``completed`` or the
    wait step the run would pause at). TASK/APPROVAL/DELAY/EVENT_WAIT/ACTION
    steps are treated as pause points; only CONDITION steps are resolved.
    """

    context = dict(context or {})
    definition = await db.get(OrchWorkflowDefinition, definition_id)
    if definition is None:
        return {"completed": False, "error": "Definition not found", "trace": []}

    result = await db.execute(
        select(OrchTransition).where(
            OrchTransition.workflow_definition_id == definition.id
        )
    )
    transitions = list(result.scalars().all())
    steps_result = await db.execute(
        select(OrchStepDefinition)
        .where(OrchStepDefinition.workflow_definition_id == definition.id)
        .order_by(OrchStepDefinition.order_index.asc())
    )
    steps = list(steps_result.scalars().all())
    by_key = {s.step_key: s for s in steps}

    from app.services.orchestration_conditions import evaluate_condition

    incoming = {t.to_step_key for t in transitions}
    starts = sorted(
        (s for s in steps if s.step_key not in incoming),
        key=lambda s: s.order_index,
    )
    if not starts:
        return {
            "completed": False,
            "error": "No start step",
            "trace": [],
            "context": context,
        }

    trace: list[dict[str, Any]] = []
    current_key: str | None = None
    previous_key: str | None = None
    current = starts[0]

    for _iteration in range(max_steps):
        step = by_key.get(current_key or current.step_key)
        if step is None:
            step = current
        trace.append(_trace_entry(step, "reached"))

        if step.step_type == OrchStepType.CONDITION:
            try:
                result_value = evaluate_condition(
                    step.configuration.get("condition"), context
                )
            except ValueError as exc:
                return {
                    "completed": False,
                    "error": str(exc),
                    "trace": trace,
                    "context": context,
                }
            context.setdefault("steps", {})[step.step_key] = {"result": result_value}
            trace[-1]["outcome"] = "condition"
            trace[-1]["result"] = result_value

        pauses = (OrchStepType.TASK, OrchStepType.APPROVAL, OrchStepType.DELAY,
                  OrchStepType.EVENT_WAIT, OrchStepType.ACTION, OrchStepType.PARALLEL)
        if step.step_type in pauses:
            return {
                "completed": False,
                "waiting_at": step.step_key,
                "trace": trace,
                "context": context,
            }

        previous_key = step.step_key
        chosen: OrchStepDefinition | None = None
        out = [
            t for t in transitions
            if t.from_step_key == step.step_key
        ]
        out.sort(key=lambda t: t.priority)
        for transition in out:
            if evaluate_condition(transition.condition, context):
                chosen = by_key.get(transition.to_step_key)
                if chosen is not None:
                    trace[-1]["transitioned_to"] = chosen.step_key
                    current_key = chosen.step_key
                    break
        if chosen is None:
            return {
                "completed": True,
                "transitioned_from": previous_key,
                "trace": trace,
                "context": context,
            }
        current = chosen

    return {
        "completed": False,
        "error": f"Exceeded {max_steps} simulated steps",
        "trace": trace,
        "context": context,
    }


def _trace_entry(step: OrchStepDefinition, outcome: str) -> dict[str, Any]:
    return {
        "step_key": step.step_key,
        "step_type": step.step_type.value if hasattr(step.step_type, "value") else str(step.step_type),
        "outcome": outcome,
    }