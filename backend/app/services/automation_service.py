"""Automation rules service (spec §3.19).

Data-driven automation on top of the orchestrator's action registry:
- a closed condition DSL (never arbitrary expression evaluation, §3.19.10)
- an event router that matches active rules for an incoming domain event
- idempotent executions keyed by (rule, event, aggregate) (§3.19.16)
- rate limiting per rule (§3.19.39)
- human checkpoints that pause a chain pending a permitted decision
- a dry-run evaluator for rule testing (§3.19.64-65)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import AutomationExecution, AutomationRule, HumanCheckpoint


class AutomationRuleError(Exception):
    pass


# ---------------------------------------------------------------------------
# Condition DSL (§3.19.9-11)
# ---------------------------------------------------------------------------

_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: _num(a) > _num(b),
    "lt": lambda a, b: _num(a) < _num(b),
    "gte": lambda a, b: _num(a) >= _num(b),
    "lte": lambda a, b: _num(a) <= _num(b),
    "in": lambda a, b: a in (b or []),
    "contains": lambda a, b: b in (a or ""),
    "exists": lambda a, b: a is not None,
}


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def resolve_path(context: dict, path: str):
    """Dot-path lookup into the event context (mirrors orchestrator rules)."""
    current = context
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def evaluate_condition(node, context: dict) -> bool:
    """Evaluate the condition DSL tree against a context.

    Supported node shapes:
      {"all": [node, ...]}   — logical AND
      {"any": [node, ...]}   — logical OR
      {"not": node}          — negation
      {"field": "a.b", "op": "eq", "value": x}
    Unknown ops/nodes raise: rules are validated at save time.
    """
    if node is None:
        return True
    if not isinstance(node, dict):
        raise AutomationRuleError("Condition nodes must be objects")

    if "all" in node:
        return all(evaluate_condition(n, context) for n in node["all"])
    if "any" in node:
        return any(evaluate_condition(n, context) for n in node["any"])
    if "not" in node:
        return not evaluate_condition(node["not"], context)
    if "field" in node:
        op = node.get("op", "eq")
        if op not in _OPS:
            raise AutomationRuleError(f"Unknown condition operator: {op!r}")
        actual = resolve_path(context, node["field"])
        return _OPS[op](actual, node.get("value"))
    raise AutomationRuleError("Condition node must contain all/any/not/field")


def validate_condition_tree(node, depth: int = 0) -> None:
    """Structural validation at save time (§3.19.31 semantics for rules)."""
    if depth > 10:
        raise AutomationRuleError("Condition nesting exceeds 10 levels")
    if node is None or not isinstance(node, dict):
        raise AutomationRuleError("Condition nodes must be objects")
    if "all" in node or "any" in node:
        # Key membership, not truthiness: {"all": []} is a valid (vacuously
        # true) node and must not fall through to the any/branch via `or`.
        children = node["all"] if "all" in node else node["any"]
        if not isinstance(children, list):
            raise AutomationRuleError("all/any require a list of nodes")
        for child in children:
            validate_condition_tree(child, depth + 1)
    elif "not" in node:
        validate_condition_tree(node["not"], depth + 1)
    elif "field" in node:
        if node.get("op", "eq") not in _OPS:
            raise AutomationRuleError(
                f"Unknown condition operator: {node.get('op')!r}"
            )
    else:
        raise AutomationRuleError("Condition node must contain all/any/not/field")


# ---------------------------------------------------------------------------
# Event router + execution (§3.19.13-16)
# ---------------------------------------------------------------------------


async def route_event(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    context: dict,
    aggregate_id: uuid.UUID | None = None,
) -> list[dict]:
    """Match active rules for an event and execute them.

    Returns per-rule outcomes for observability (§3.19.52-53). Execution is
    idempotent: a (rule, event, aggregate) triple that already executed
    successfully is skipped on replay.
    """
    rules = (
        await db.execute(
            select(AutomationRule).where(
                AutomationRule.organization_id == organization_id,
                AutomationRule.trigger_event == event_type,
                AutomationRule.is_active.is_(True),
            )
        )
    ).scalars().all()

    outcomes: list[dict] = []
    for rule in rules:
        outcome = await _execute_rule(
            db,
            rule=rule,
            event_type=event_type,
            context=context,
            aggregate_id=aggregate_id,
            commit_side_effects=False,
        )
        outcomes.append(outcome)
    await db.flush()
    return outcomes


async def _execute_rule(
    db: AsyncSession,
    *,
    rule: AutomationRule,
    event_type: str,
    context: dict,
    aggregate_id: uuid.UUID | None,
    commit_side_effects: bool = False,
) -> dict:
    now = datetime.now(timezone.utc)

    # Idempotency: skip if this event already executed for the rule (§3.19.16).
    existing = (
        await db.execute(
            select(AutomationExecution).where(
                AutomationExecution.rule_id == rule.id,
                AutomationExecution.event_type == event_type,
                AutomationExecution.event_aggregate_id == aggregate_id,
                AutomationExecution.status == "executed",
            )
        )
    ).scalars().first()
    if existing is not None:
        return {
            "rule_id": str(rule.id),
            "status": "skipped_already_executed",
            "execution_id": str(existing.id),
        }

    # Rate limiting (§3.19.39).
    if rule.max_executions_per_hour:
        window_start = now - timedelta(hours=1)
        fired = (
            await db.scalar(
                select(func.count())
                .select_from(AutomationExecution)
                .where(
                    AutomationExecution.rule_id == rule.id,
                    AutomationExecution.status == "executed",
                    AutomationExecution.executed_at >= window_start,
                )
            )
        ) or 0
        if fired >= rule.max_executions_per_hour:
            execution = AutomationExecution(
                rule_id=rule.id,
                rule_version=rule.version,
                event_type=event_type,
                event_aggregate_id=aggregate_id,
                status="skipped_rate_limit",
            )
            db.add(execution)
            return {
                "rule_id": str(rule.id),
                "status": "skipped_rate_limit",
            }

    # Condition evaluation.
    try:
        matched = evaluate_condition(rule.conditions, context)
    except AutomationRuleError as exc:
        execution = AutomationExecution(
            rule_id=rule.id,
            rule_version=rule.version,
            event_type=event_type,
            event_aggregate_id=aggregate_id,
            status="failed",
            error=str(exc),
        )
        db.add(execution)
        return {"rule_id": str(rule.id), "status": "failed", "error": str(exc)}

    if not matched:
        return {"rule_id": str(rule.id), "status": "not_matched"}

    # Execute the registered action (allowlist = ACTION_REGISTRY upstream).
    from app.services.orchestration_actions import (
        WorkflowActionError,
        get_action,
    )

    execution = AutomationExecution(
        rule_id=rule.id,
        rule_version=rule.version,
        event_type=event_type,
        event_aggregate_id=aggregate_id,
        status="matched",
    )
    db.add(execution)
    await db.flush()

    try:
        action = get_action(rule.action_key)
        result = await action.execute(
            db,
            config=dict(rule.action_config or {}),
            context=dict(context),
            step_instance_id=execution.id,  # provenance for downstream rows
        )
        execution.status = "executed"
        execution.result = result
        execution.executed_at = now
    except WorkflowActionError as exc:
        execution.status = "failed"
        execution.error = str(exc)
        raise
    finally:
        await db.flush()

    return {
        "rule_id": str(rule.id),
        "status": execution.status,
        "execution_id": str(execution.id),
        "result": execution.result,
    }


# ---------------------------------------------------------------------------
# Human checkpoints (§3.19.25-27)
# ---------------------------------------------------------------------------


async def create_checkpoint(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    title: str,
    instructions: str | None = None,
    required_permission: str | None = None,
    assignee_user_id: uuid.UUID | None = None,
    workflow_step_instance_id: uuid.UUID | None = None,
    expires_in_hours: int | None = None,
) -> HumanCheckpoint:
    checkpoint = HumanCheckpoint(
        organization_id=organization_id,
        title=title,
        instructions=instructions,
        required_permission=required_permission,
        assignee_user_id=assignee_user_id,
        workflow_step_instance_id=workflow_step_instance_id,
        expires_at=(
            datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
            if expires_in_hours
            else None
        ),
    )
    db.add(checkpoint)
    await db.flush()

    # Surface in the Action Center.
    from app.services.action_item_service import upsert_action_item

    await upsert_action_item(
        db,
        organization_id=organization_id,
        action_type="checkpoint",
        title=f"Checkpoint: {title}",
        source_system="automation",
        source_id=checkpoint.id,
        assignee_user_id=assignee_user_id,
        action_url=f"/automation/checkpoints/{checkpoint.id}",
        expires_at=checkpoint.expires_at,
    )
    return checkpoint


async def resolve_checkpoint(
    db: AsyncSession,
    *,
    checkpoint_id: uuid.UUID,
    decision: str,
    resolved_by: uuid.UUID,
    payload: dict | None = None,
) -> HumanCheckpoint:
    """Idempotent resolution (§3.19.27): only a pending checkpoint resolves."""
    checkpoint = await db.get(HumanCheckpoint, checkpoint_id)
    if checkpoint is None:
        raise AutomationRuleError("Checkpoint not found")
    if checkpoint.status != "pending":
        return checkpoint
    if decision not in ("approved", "rejected"):
        raise AutomationRuleError("decision must be approved|rejected")
    checkpoint.status = decision
    checkpoint.resolved_by = resolved_by
    checkpoint.resolved_at = datetime.now(timezone.utc)
    checkpoint.resolution_payload = payload

    from app.services.action_item_service import resolve_source_item

    await resolve_source_item(
        db, source_system="automation", source_id=checkpoint.id
    )
    await db.flush()
    return checkpoint


# ---------------------------------------------------------------------------
# Dry run (§3.19.64-65)
# ---------------------------------------------------------------------------


async def dry_run_rule(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    conditions: dict | None,
    action_key: str,
    action_config: dict | None,
    context: dict,
) -> dict:
    """Evaluate a rule draft against a sample context WITHOUT executing."""
    from app.services.orchestration_actions import action_keys

    report: dict = {
        "event_type": event_type,
        "context_provided": bool(context),
    }

    try:
        validate_condition_tree(conditions)
        report["conditions_valid"] = True
    except AutomationRuleError as exc:
        report["conditions_valid"] = False
        report["condition_error"] = str(exc)
        report["matched"] = False
        report["action_registered"] = action_key in action_keys()
        return report

    report["matched"] = evaluate_condition(conditions, context)
    report["condition_trace"] = _trace(conditions, context)
    report["action_registered"] = action_key in action_keys()
    if not report["action_registered"]:
        report["action_error"] = (
            f"{action_key!r} is not in the action allowlist"
        )
    report["would_execute"] = bool(
        report["matched"] and report["action_registered"]
    )
    return report


def _trace(node, context: dict) -> dict:
    """Explain a condition evaluation path (why matched / why not)."""
    if not isinstance(node, dict):
        return {"node": node, "result": False}
    if "all" in node:
        return {
            "op": "all",
            "children": [_trace(n, context) for n in node["all"]],
            "result": evaluate_condition(node, context),
        }
    if "any" in node:
        return {
            "op": "any",
            "children": [_trace(n, context) for n in node["any"]],
            "result": evaluate_condition(node, context),
        }
    if "not" in node:
        return {
            "op": "not",
            "child": _trace(node["not"], context),
            "result": evaluate_condition(node, context),
        }
    if "field" in node:
        return {
            "field": node["field"],
            "op": node.get("op", "eq"),
            "expected": node.get("value"),
            "actual": resolve_path(context, node["field"]),
            "result": evaluate_condition(node, context),
        }
    return {"node": node, "result": False}
