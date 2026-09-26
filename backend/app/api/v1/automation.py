"""Automation rules API (spec §3.19.61-66).

CRUD  /automation/rules            — versioned rule management
POST  /automation/rules/dry-run    — test a rule draft against a context
POST  /automation/rules/{id}/test  — dry-run a saved rule
POST  /automation/events           — route a domain event through the rules
GET   /automation/checkpoints      — pending human checkpoints
POST  /automation/checkpoints/{id}/resolve — resolve a checkpoint
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.automation import AutomationRule, HumanCheckpoint
from app.models.user import User
from app.services import automation_service

router = APIRouter(prefix="/automation", tags=["Automation"])


class RuleCreate(BaseModel):
    name: str
    description: str | None = None
    trigger_event: str
    conditions: dict | None = None
    action_key: str
    action_config: dict | None = None
    max_executions_per_hour: int | None = Field(default=None, ge=1, le=1000)


class DryRunRequest(BaseModel):
    event_type: str
    conditions: dict | None = None
    action_key: str
    action_config: dict | None = None
    context: dict = Field(default_factory=dict)


class CheckpointResolve(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    payload: dict | None = None


def _serialize_rule(rule: AutomationRule) -> dict:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "description": rule.description,
        "trigger_event": rule.trigger_event,
        "conditions": rule.conditions,
        "action_key": rule.action_key,
        "action_config": rule.action_config,
        "is_active": rule.is_active,
        "version": rule.version,
        "scope": rule.scope,
        "max_executions_per_hour": rule.max_executions_per_hour,
    }


@router.get("/rules")
async def list_rules(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    rules = (
        await db.execute(
            select(AutomationRule)
            .where(AutomationRule.organization_id == uuid.UUID(str(org_id)))
            .order_by(AutomationRule.created_at.desc())
        )
    ).scalars().all()
    return [_serialize_rule(r) for r in rules]


@router.post("/rules")
async def create_rule(
    body: RuleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    from app.services.orchestration_actions import action_keys

    if body.action_key not in action_keys():
        raise HTTPException(
            status_code=422,
            detail=f"action_key {body.action_key!r} is not in the allowlist",
        )
    try:
        automation_service.validate_condition_tree(body.conditions)
    except automation_service.AutomationRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    rule = AutomationRule(
        organization_id=uuid.UUID(str(org_id)),
        name=body.name,
        description=body.description,
        trigger_event=body.trigger_event,
        conditions=body.conditions,
        action_key=body.action_key,
        action_config=body.action_config,
        max_executions_per_hour=body.max_executions_per_hour,
        created_by=user.id,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return _serialize_rule(rule)


@router.patch("/rules/{rule_id}")
async def update_rule(
    rule_id: str,
    body: RuleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    rule = await db.get(AutomationRule, uuid.UUID(rule_id))
    if rule is None or rule.organization_id != uuid.UUID(str(org_id)):
        raise HTTPException(status_code=404, detail="Rule not found")
    if rule.scope == "system":
        raise HTTPException(
            status_code=403, detail="System rules cannot be edited"
        )

    if body.action_key != rule.action_key:
        from app.services.orchestration_actions import action_keys

        if body.action_key not in action_keys():
            raise HTTPException(status_code=422, detail="Unknown action_key")
    try:
        automation_service.validate_condition_tree(body.conditions)
    except automation_service.AutomationRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Content changes bump the version (§3.19.7 — why version rules).
    rule.name = body.name
    rule.description = body.description
    rule.trigger_event = body.trigger_event
    rule.conditions = body.conditions
    rule.action_key = body.action_key
    rule.action_config = body.action_config
    rule.max_executions_per_hour = body.max_executions_per_hour
    rule.version += 1
    await db.commit()
    await db.refresh(rule)
    return _serialize_rule(rule)


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    rule = await db.get(AutomationRule, uuid.UUID(rule_id))
    if rule is None or rule.organization_id != uuid.UUID(str(org_id)):
        raise HTTPException(status_code=404, detail="Rule not found")
    if rule.scope == "system":
        raise HTTPException(status_code=403, detail="System rules cannot be deleted")
    rule.is_active = False
    await db.commit()
    return {"status": "disabled"}


@router.post("/rules/dry-run")
async def dry_run(
    body: DryRunRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Test a rule draft against a sample context — never executes (§3.19.65)."""
    return await automation_service.dry_run_rule(
        db,
        organization_id=uuid.UUID(str(org_id)),
        event_type=body.event_type,
        conditions=body.conditions,
        action_key=body.action_key,
        action_config=body.action_config,
        context=body.context,
    )


class RuleTestRequest(BaseModel):
    context: dict = Field(default_factory=dict)


@router.post("/rules/{rule_id}/test")
async def test_saved_rule(
    rule_id: str,
    body: RuleTestRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    rule = await db.get(AutomationRule, uuid.UUID(rule_id))
    if rule is None or rule.organization_id != uuid.UUID(str(org_id)):
        raise HTTPException(status_code=404, detail="Rule not found")
    return await automation_service.dry_run_rule(
        db,
        organization_id=uuid.UUID(str(org_id)),
        event_type=rule.trigger_event,
        conditions=rule.conditions,
        action_key=rule.action_key,
        action_config=rule.action_config,
        context=body.context,
    )


class EventRouteRequest(BaseModel):
    event_type: str
    context: dict = Field(default_factory=dict)
    aggregate_id: str | None = None


@router.post("/events")
async def route_event(
    body: EventRouteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Route a domain event through the workspace's active rules."""
    outcomes = await automation_service.route_event(
        db,
        organization_id=uuid.UUID(str(org_id)),
        event_type=body.event_type,
        context=body.context,
        aggregate_id=(
            uuid.UUID(body.aggregate_id) if body.aggregate_id else None
        ),
    )
    await db.commit()
    return {"event_type": body.event_type, "outcomes": outcomes}


class CheckpointCreate(BaseModel):
    title: str
    instructions: str | None = None
    required_permission: str | None = None
    assignee_user_id: str | None = None
    expires_in_hours: int | None = Field(default=None, ge=1, le=720)


@router.post("/checkpoints")
async def create_checkpoint(
    body: CheckpointCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    checkpoint = await automation_service.create_checkpoint(
        db,
        organization_id=uuid.UUID(str(org_id)),
        title=body.title,
        instructions=body.instructions,
        required_permission=body.required_permission,
        assignee_user_id=(
            uuid.UUID(body.assignee_user_id) if body.assignee_user_id else None
        ),
        expires_in_hours=body.expires_in_hours,
    )
    await db.commit()
    return {
        "id": str(checkpoint.id),
        "title": checkpoint.title,
        "status": checkpoint.status,
    }


@router.get("/checkpoints")
async def list_checkpoints(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    checkpoints = (
        await db.execute(
            select(HumanCheckpoint)
            .where(
                HumanCheckpoint.organization_id == uuid.UUID(str(org_id)),
                HumanCheckpoint.status == "pending",
            )
            .order_by(HumanCheckpoint.created_at.asc())
        )
    ).scalars().all()
    return [
        {
            "id": str(c.id),
            "title": c.title,
            "instructions": c.instructions,
            "status": c.status,
            "assignee_user_id": (
                str(c.assignee_user_id) if c.assignee_user_id else None
            ),
            "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        }
        for c in checkpoints
    ]


@router.post("/checkpoints/{checkpoint_id}/resolve")
async def resolve_checkpoint(
    checkpoint_id: str,
    body: CheckpointResolve,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    checkpoint = await db.get(HumanCheckpoint, uuid.UUID(checkpoint_id))
    if checkpoint is None or checkpoint.organization_id != uuid.UUID(str(org_id)):
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    try:
        resolved = await automation_service.resolve_checkpoint(
            db,
            checkpoint_id=checkpoint.id,
            decision=body.decision,
            resolved_by=user.id,
            payload=body.payload,
        )
    except automation_service.AutomationRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return {"id": str(resolved.id), "status": resolved.status}
