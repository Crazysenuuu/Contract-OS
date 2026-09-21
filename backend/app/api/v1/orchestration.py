"""Workflow Automation orchestration API (spec §2.11.45).

Exposes the config-driven workflow definition lifecycle, instance lifecycle,
human tasks, incidents and dead letters. Paths follow spec §2.11.45
(/workflows, /workflow-instances, /workflow-tasks, /workflow-incidents);
domain events are dispatched through /workflow-events for triggering.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.rbac import require_permission
from app.dependencies.tenant import get_current_organization_id
from app.models.orchestration import OrchStepDefinition, OrchTransition
from app.models.user import User

from app.services import orchestration_engine, orchestration_service

workflows_router = APIRouter(prefix="/workflows", tags=["Workflow Orchestration"])
instances_router = APIRouter(prefix="/workflow-instances", tags=["Workflow Orchestration"])
tasks_router = APIRouter(prefix="/workflow-tasks", tags=["Workflow Orchestration"])
incidents_router = APIRouter(prefix="/workflow-incidents", tags=["Workflow Orchestration"])
deadletters_router = APIRouter(prefix="/workflow-dead-letters", tags=["Workflow Orchestration"])
events_router = APIRouter(prefix="/workflow-events", tags=["Workflow Orchestration"])

_perm_manage = Depends(require_permission("workflow.manage"))
_perm_execute = Depends(require_permission("workflow.execute"))
_perm_incident = Depends(require_permission("workflow.resolve_incident"))


# ── Payload models ─────────────────────────────────────────────────────────

class WorkflowStepPayload(BaseModel):
    step_key: str
    name: Optional[str] = None
    step_type: str
    configuration: dict[str, Any] = {}
    required: bool = True
    timeout_seconds: Optional[int] = None


class WorkflowTransitionPayload(BaseModel):
    from_step_key: str
    to_step_key: str
    condition: Optional[dict[str, Any]] = None
    priority: int = 0


class WorkflowCreateRequest(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    scope: str = "global"
    trigger: dict[str, Any] = {}
    configuration: dict[str, Any] = {}
    steps: list[WorkflowStepPayload] = []
    transitions: list[WorkflowTransitionPayload] = []


class WorkflowUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    trigger: Optional[dict[str, Any]] = None
    configuration: Optional[dict[str, Any]] = None
    steps: Optional[list[WorkflowStepPayload]] = None
    transitions: Optional[list[WorkflowTransitionPayload]] = None


class SimulateRequest(BaseModel):
    context: dict[str, Any] = {}


class DispatchEventRequest(BaseModel):
    event_id: UUID
    event_type: str
    organization_id: Optional[UUID] = None
    agreement_id: Optional[UUID] = None
    aggregate_type: Optional[str] = None
    aggregate_id: Optional[UUID] = None
    payload: dict[str, Any] = {}


class TaskActionRequest(BaseModel):
    resolution: Optional[dict[str, Any]] = None


class ResolveIncidentRequest(BaseModel):
    resolution: Optional[str] = None


# ── Definition endpoints ───────────────────────────────────────────────────

@workflows_router.get("")
async def list_workflows(
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """List ACTIVE (or all, if requested) workflow definitions scoped to the org + globals."""
    definitions = await orchestration_service.list_workflow_definitions(
        db, organization_id=org_id, include_inactive=include_inactive
    )
    return {
        "workflows": [
            {
                "id": str(d.id),
                "code": d.code,
                "name": d.name,
                "version": d.version,
                "status": d.status.value if hasattr(d.status, "value") else str(d.status),
                "scope": d.scope.value if hasattr(d.scope, "value") else str(d.scope),
                "trigger": d.trigger,
            }
            for d in definitions
        ]
    }


@workflows_router.post("", dependencies=[_perm_manage])
async def create_workflow(
    request: WorkflowCreateRequest,
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        definition = await orchestration_service.create_workflow_definition(
            db,
            code=request.code,
            name=request.name,
            description=request.description,
            scope=request.scope,
            trigger=request.trigger,
            configuration=request.configuration,
            steps=[s.model_dump() for s in request.steps],
            transitions=[t.model_dump() for t in request.transitions],
            actor_id=current_user.id,
            organization_id=org_id,
        )
    except (orchestration_service.WorkflowDefinitionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return {
        "id": str(definition.id),
        "code": definition.code,
        "version": definition.version,
        "status": definition.status.value if hasattr(definition.status, "value") else str(definition.status),
    }


@workflows_router.get("/versions")
async def list_versions(
    code: str = Query(...),
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    versions = await orchestration_service.list_workflow_versions(
        db, code, organization_id=org_id
    )
    return {
        "versions": [
            {
                "id": str(v.id),
                "version": v.version,
                "status": v.status.value if hasattr(v.status, "value") else str(v.status),
                "name": v.name,
                "updated_at": v.updated_at.isoformat() if v.updated_at else None,
            }
            for v in versions
        ]
    }


@workflows_router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    definition = await orchestration_service.get_workflow_definition(db, workflow_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    steps_result = await db.execute(
        select(OrchStepDefinition)
        .where(OrchStepDefinition.workflow_definition_id == definition.id)
        .order_by(OrchStepDefinition.order_index.asc())
    )
    transitions_result = await db.execute(
        select(OrchTransition)
        .where(OrchTransition.workflow_definition_id == definition.id)
        .order_by(OrchTransition.priority.asc())
    )
    return {
        "id": str(definition.id),
        "code": definition.code,
        "name": definition.name,
        "description": definition.description,
        "version": definition.version,
        "status": definition.status.value if hasattr(definition.status, "value") else str(definition.status),
        "scope": definition.scope.value if hasattr(definition.scope, "value") else str(definition.scope),
        "trigger": definition.trigger,
        "configuration": definition.configuration,
        "steps": [
            {
                "step_key": s.step_key,
                "name": s.name,
                "step_type": s.step_type.value if hasattr(s.step_type, "value") else str(s.step_type),
                "configuration": s.configuration,
                "order_index": s.order_index,
            }
            for s in steps_result.scalars().all()
        ],
        "transitions": [
            {
                "from_step_key": t.from_step_key,
                "to_step_key": t.to_step_key,
                "condition": t.condition,
                "priority": t.priority,
            }
            for t in transitions_result.scalars().all()
        ],
    }


@workflows_router.patch("/{workflow_id}", dependencies=[_perm_manage])
async def update_workflow(
    workflow_id: UUID,
    request: WorkflowUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        definition = await orchestration_service.update_workflow_definition(
            db,
            workflow_id,
            name=request.name,
            description=request.description,
            trigger=request.trigger,
            configuration=request.configuration,
            steps=[s.model_dump() for s in request.steps] if request.steps is not None else None,
            transitions=[t.model_dump() for t in request.transitions] if request.transitions is not None else None,
        )
    except orchestration_service.WorkflowDefinitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return {"id": str(definition.id), "status": "updated"}


@workflows_router.post("/{workflow_id}/validate", dependencies=[_perm_manage])
async def validate_workflow(
    workflow_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await orchestration_service.validate_definition(db, workflow_id)
    if result["valid"]:
        return {"valid": True, "errors": []}
    return {"valid": False, "errors": result["errors"]}


@workflows_router.post("/{workflow_id}/simulate", dependencies=[_perm_manage])
async def simulate_workflow(
    workflow_id: UUID,
    request: SimulateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await orchestration_service.simulate_definition(
        db, workflow_id, request.context
    )
    return result


@workflows_router.post("/{workflow_id}/publish", dependencies=[_perm_manage])
async def publish_workflow(
    workflow_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        active = await orchestration_service.publish_workflow_definition(
            db, workflow_id, actor_id=current_user.id
        )
    except orchestration_service.WorkflowDefinitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return {
        "id": str(active.id),
        "code": active.code,
        "version": active.version,
        "status": "active",
    }


@workflows_router.post("/{workflow_id}/disable", dependencies=[_perm_manage])
async def disable_workflow(
    workflow_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        definition = await orchestration_service.disable_workflow_definition(db, workflow_id)
    except orchestration_service.WorkflowDefinitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return {"id": str(definition.id), "status": "disabled"}


# ── Instance endpoints ─────────────────────────────────────────────────────

@instances_router.get("")
async def list_instances(
    org_id: UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    status: Optional[str] = Query(None),
    workflow_id: Optional[UUID] = Query(None, alias="workflow_id"),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
):
    instances = await orchestration_service.list_instances(
        db, organization_id=org_id, status=status, definition_id=workflow_id, limit=limit
    )
    return {
        "instances": [
            {
                "id": str(i.id),
                "workflow_definition_id": str(i.workflow_definition_id),
                "agreement_id": str(i.agreement_id) if i.agreement_id else None,
                "status": i.status.value if hasattr(i.status, "value") else str(i.status),
                "started_at": i.started_at.isoformat() if i.started_at else None,
            }
            for i in instances
        ]
    }


@instances_router.get("/{instance_id}")
async def get_instance(
    instance_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    instance = await orchestration_service.get_workflow_instance(db, instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    return {
        "id": str(instance.id),
        "workflow_definition_id": str(instance.workflow_definition_id),
        "agreement_id": str(instance.agreement_id) if instance.agreement_id else None,
        "status": instance.status.value if hasattr(instance.status, "value") else str(instance.status),
        "context": instance.context,
        "source_event_id": str(instance.source_event_id) if instance.source_event_id else None,
        "started_at": instance.started_at.isoformat() if instance.started_at else None,
        "completed_at": instance.completed_at.isoformat() if instance.completed_at else None,
    }


def _lifecycle_action_handler(action: str):
    async def handler(
        instance_id: UUID,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        try:
            instance = await getattr(orchestration_engine, action)(db, instance_id)
        except orchestration_engine.OrchestrationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        await db.commit()
        return {
            "id": str(instance.id),
            "status": instance.status.value if hasattr(instance.status, "value") else str(instance.status),
        }

    return handler


@instances_router.post("/{instance_id}/suspend", dependencies=[_perm_execute])
async def suspend_instance(instance_id: UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _lifecycle_action_handler("suspend_instance")(instance_id, current_user, db)


@instances_router.post("/{instance_id}/resume", dependencies=[_perm_execute])
async def resume_instance(instance_id: UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _lifecycle_action_handler("resume_instance")(instance_id, current_user, db)


@instances_router.post("/{instance_id}/cancel", dependencies=[_perm_execute])
async def cancel_instance(instance_id: UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _lifecycle_action_handler("cancel_instance")(instance_id, current_user, db)


@instances_router.get("/{instance_id}/events")
async def instance_events(
    instance_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    events = await orchestration_service.get_instance_events(db, instance_id)
    return {"events": [
        {
            "id": str(e.id),
            "event_type": e.event_type,
            "data": e.data,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]}


@instances_router.get("/{instance_id}/trace")
async def instance_trace(
    instance_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trace = await orchestration_service.get_instance_trace(db, instance_id)
    return {"trace": trace.get("steps", [])}


# ── Task endpoints ─────────────────────────────────────────────────────────

@tasks_router.get("")
async def list_tasks(
    current_user: User = Depends(get_current_user),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
):
    """List tasks assigned to the caller (spec §2.11.18)."""
    tasks = await orchestration_service.list_tasks(
        db, user_id=current_user.id, status=status_filter, limit=limit
    )
    return {"tasks": [
        {
            "id": str(t.id),
            "title": t.title,
            "task_type": t.task_type,
            "status": t.status,
            "due_at": t.due_at.isoformat() if t.due_at else None,
            "assignee_user_id": str(t.assignee_user_id) if t.assignee_user_id else None,
        }
        for t in tasks
    ]}


@tasks_router.post("/{task_id}/complete", dependencies=[_perm_execute])
async def complete_task(
    task_id: UUID,
    request: TaskActionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        instance = await orchestration_engine.complete_task(
            db, task_id=task_id, actor_id=current_user.id,
            action="complete", resolution=request.resolution,
        )
    except orchestration_engine.TaskNotAssignableError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except orchestration_engine.OrchestrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return {
        "ok": True,
        "instance_id": str(instance.id),
        "instance_status": instance.status.value if hasattr(instance.status, "value") else str(instance.status),
    }


@tasks_router.post("/{task_id}/reject", dependencies=[_perm_execute])
async def reject_task(
    task_id: UUID,
    request: TaskActionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        instance = await orchestration_engine.complete_task(
            db, task_id=task_id, actor_id=current_user.id,
            action="reject", resolution=request.resolution,
        )
    except orchestration_engine.TaskNotAssignableError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except orchestration_engine.OrchestrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return {
        "ok": True,
        "instance_id": str(instance.id),
        "instance_status": instance.status.value if hasattr(instance.status, "value") else str(instance.status),
    }


# ── Incident endpoints ─────────────────────────────────────────────────────

@incidents_router.get("")
async def list_incidents(
    current_user: User = Depends(get_current_user),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
):
    incidents = await orchestration_service.list_incidents(
        db, status=status_filter, limit=limit
    )
    return {"incidents": [
        {
            "id": str(i.id),
            "workflow_instance_id": str(i.workflow_instance_id),
            "incident_type": i.incident_type,
            "severity": i.severity,
            "description": i.description,
            "status": i.status,
        }
        for i in incidents
    ]}


@incidents_router.post("/{incident_id}/resolve", dependencies=[_perm_incident])
async def resolve_incident(
    incident_id: UUID,
    request: ResolveIncidentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        incident = await orchestration_engine.resolve_incident(
            db, incident_id, resolution=request.resolution, actor_id=current_user.id
        )
    except orchestration_engine.OrchestrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return {"id": str(incident.id), "status": incident.status}


# ── Dead letter endpoints ──────────────────────────────────────────────────

@deadletters_router.get("")
async def list_dead_letters(
    current_user: User = Depends(get_current_user),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
):
    letters = await orchestration_service.list_dead_letters(db, limit=limit)
    return {"dead_letters": [
        {
            "id": str(d.id),
            "event_id": str(d.event_id),
            "event_type": d.event_type,
            "error": d.error,
            "status": d.status,
        }
        for d in letters
    ]}


# ── Event dispatch ─────────────────────────────────────────────────────────

@events_router.post("/dispatch", dependencies=[_perm_execute])
async def dispatch_event(
    request: DispatchEventRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deliver a domain event into the orchestrator (spec §2.11.26)."""
    started = await orchestration_engine.dispatch_event(
        db,
        event_id=request.event_id,
        event_type=request.event_type,
        organization_id=request.organization_id,
        agreement_id=request.agreement_id,
        aggregate_type=request.aggregate_type,
        aggregate_id=request.aggregate_id,
        payload=request.payload,
        actor_id=current_user.id,
    )
    await db.commit()
    return {"started_instances": [str(i) for i in started]}