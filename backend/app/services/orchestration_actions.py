"""Workflow action registry for the orchestrator (spec §2.11.16/2.11.17).

Actions are the *only* places a workflow definition may cause side effects.
Each action is code-backed and registered by a stable key — a definition
selects actions by key, never by import path (spec §2.11.17). Unknown keys are
rejected at validation/engine time.

Implemented actions:
  - CREATE_TASK          human task (TASK/APPROVAL steps also create tasks
                         directly, but ACTION steps may too)
  - CREATE_NOTIFICATION  writes a Notification row (spec §2.11.34)
  - CREATE_OBLIGATION    delegates to ``ObligationService``
  - WAIT_FOR_EVENT       opens a durable event wait (spec §2.11.27 / 2.11.30)

CREATE_APPROVAL, REQUEST_SIGNATURE, CREATE_AMENDMENT, RUN_RISK_ANALYSIS and
CALL_INTEGRATION are intentionally *not* registered yet — requesting them
raises a clear error rather than pretending to work.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.orchestration import OrchEventWait, OrchTask

# Keys requested by spec §2.11.17 that are planned but not yet implemented.
PLANNED_ACTIONS: frozenset[str] = frozenset(
    {
        "CREATE_APPROVAL",
        "REQUEST_SIGNATURE",
        "CREATE_AMENDMENT",
        "RUN_RISK_ANALYSIS",
        "CALL_INTEGRATION",
    }
)


class WorkflowActionError(Exception):
    """Raised when an action fails; the engine maps it to retry/incident."""


class BaseAction(ABC):
    key: str

    @abstractmethod
    async def execute(
        self,
        db: AsyncSession,
        config: dict[str, Any],
        context: dict[str, Any],
        step_instance_id: UUID,
    ) -> dict[str, Any]:
        """Perform the action and return the step's output payload."""


class CreateTaskAction(BaseAction):
    key = "CREATE_TASK"

    async def execute(self, db, config, context, step_instance_id) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        due_in = config.get("due_in_seconds")
        task = OrchTask(
            workflow_step_instance_id=step_instance_id,
            task_type=config.get("task_type") or "TASK",
            title=_render_template(config.get("title") or "Task", context),
            description=_render_template(config.get("description") or "", context),
            assignee_user_id=_resolve_user_id(config.get("assignee_user_id")),
            assignee_rule=config.get("assignee_rule"),
            due_at=now + timedelta(seconds=int(due_in)) if due_in else None,
            status="open",
            context=config,
        )
        db.add(task)
        await db.flush()
        return {"task_id": str(task.id), "title": task.title}


class CreateNotificationAction(BaseAction):
    key = "CREATE_NOTIFICATION"

    async def execute(self, db, config, context, step_instance_id) -> dict[str, Any]:
        organization_id = config.get("organization_id") or _uuid_or_none(
            context.get("organization_id")
        )
        agreement_id = config.get("agreement_id")
        if agreement_id is None:
            agreement_id = _uuid_or_none(context.get("agreement_id"))
        to_email = config.get("to_email")
        if organization_id is None:
            raise WorkflowActionError(
                "CREATE_NOTIFICATION requires organization_id in config or context"
            )
        if not to_email:
            raise WorkflowActionError("CREATE_NOTIFICATION requires to_email in config")
        notification = Notification(
            organization_id=organization_id,
            agreement_id=agreement_id,
            notification_type=config.get("notification_type") or "workflow",
            to_email=config.get("to_email"),
            subject=_render_template(config.get("subject") or "", context),
            status="queued",
            metadata_=config,
        )
        db.add(notification)
        await db.flush()
        return {"notification_id": str(notification.id), "subject": notification.subject}


class CreateObligationAction(BaseAction):
    key = "CREATE_OBLIGATION"

    async def execute(self, db, config, context, step_instance_id) -> dict[str, Any]:
        agreement_id = config.get("agreement_id") or _uuid_or_none(
            context.get("agreement_id")
        )
        if agreement_id is None:
            raise WorkflowActionError("CREATE_OBLIGATION requires agreement_id in config or context")
        from app.services.obligation_service import ObligationService

        obligation = await ObligationService(db).create_obligation(
            agreement_id=agreement_id,
            owner_party=config.get("owner_party") or "internal",
            description=_render_template(config.get("description") or "", context),
            obligation_type=config.get("obligation_type") or "delivery",
            amount=config.get("amount"),
            frequency=config.get("frequency"),
            due_date=config.get("due_date"),
            clause_identifier=config.get("clause_identifier"),
            evidence=config.get("evidence"),
        )
        return {"obligation_id": str(obligation.id)}


class WaitForEventAction(BaseAction):
    key = "WAIT_FOR_EVENT"

    async def execute(self, db, config, context, step_instance_id) -> dict[str, Any]:
        wait = OrchEventWait(
            workflow_step_instance_id=step_instance_id,
            event_type=config.get("event_type") or "",
            aggregate_type=config.get("aggregate_type"),
            aggregate_id=config.get("aggregate_id")
            or _uuid_or_none(context.get("aggregate_id")),
            correlation_key=config.get("correlation_key"),
            active=True,
        )
        db.add(wait)
        await db.flush()
        return {"event_wait_id": str(wait.id), "event_type": wait.event_type}


ACTION_REGISTRY: dict[str, BaseAction] = {
    action.key: action for action in (CreateTaskAction(), CreateNotificationAction(), CreateObligationAction(), WaitForEventAction())
}


def get_action(action_key: str) -> BaseAction:
    """Resolve a registered action; raise for unknown/planned keys."""

    if action_key in PLANNED_ACTIONS:
        raise WorkflowActionError(
            f"Action {action_key!r} is part of the spec but not yet implemented"
        )
    action = ACTION_REGISTRY.get(action_key)
    if action is None:
        raise WorkflowActionError(f"Unknown workflow action key: {action_key!r}")
    return action


def action_keys() -> set[str]:
    return set(ACTION_REGISTRY.keys())


def _render_template(template: str, context: dict[str, Any]) -> str:
    """Expand ``{path.to.value}`` placeholders from the instance context."""

    if "{" not in template:
        return template
    from app.services.orchestration_conditions import resolve_path

    def _replace(match) -> str:
        value = resolve_path(context, match.group(1))
        if value is None:
            return ""
        return str(value)

    import re

    return re.sub(r"\{([a-zA-Z0-9_.]+)\}", _replace, template)


def _resolve_user_id(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _uuid_or_none(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None