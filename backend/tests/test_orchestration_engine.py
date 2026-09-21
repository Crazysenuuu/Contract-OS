"""Engine integration tests: dispatch, routing, tasks, timers, events, retries.

All tests run through ``AsyncSession`` via the ``db_session`` fixture and the
real ``Base.metadata.create_all`` SQLite rig from ``conftest``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.orchestration import (
    OrchActionAttempt,
    OrchEventWait,
    OrchIncident,
    OrchInstanceStatus,
    OrchStepDefinition,
    OrchTask,
    OrchTimer,
    OrchWorkflowDefinition,
    OrchWorkflowEventLog,
    OrchWorkflowInstance,
    OrchWorkflowStatus,
)
from app.models.user import User
from app.services import orchestration_engine, orchestration_service
from app.services.orchestration_engine import (
    DefinitionNotActiveError,
    TaskNotAssignableError,
    handle_inbound_event,
    process_due_timers,
)
from app.services.orchestration_service import create_workflow_definition

pytestmark = pytest.mark.asyncio

EVENT = "agreement.negotiated"


async def _publish(db: AsyncSession, *, code: str, steps, transitions, scope="global"):
    definition = await create_workflow_definition(
        db,
        code=code,
        name=code,
        description=None,
        scope=scope,
        trigger={"event_type": EVENT},
        configuration={},
        steps=steps,
        transitions=transitions,
    )
    return await orchestration_service.publish_workflow_definition(db, definition.id)


async def _count(db: AsyncSession, model) -> int:
    result = await db.execute(select(func.count()).select_from(model))
    return result.scalar()


async def test_dispatch_starts_and_completes_single_action(db_session: AsyncSession):
    active = await _publish(
        db_session,
        code="wf_single_action",
        steps=[
            {
                "step_key": "notify",
                "step_type": "action",
                "configuration": {"action": "CREATE_TASK", "title": "Manual review"},
            }
        ],
        transitions=[],
    )
    event_id = uuid.uuid4()
    started = await orchestration_engine.dispatch_event(
        db_session,
        event_id=event_id,
        event_type=EVENT,
        payload={"amount": 2500},
    )
    assert len(started) == 1

    instance = await db_session.get(OrchWorkflowInstance, started[0])
    assert instance.workflow_definition_id == active.id
    assert instance.status == OrchInstanceStatus.COMPLETED
    assert instance.source_event_id == event_id
    assert instance.context.get("amount") == 2500
    assert instance.context["steps"]["notify"]["task_id"]

    tasks = (await db_session.execute(select(OrchTask))).scalars().all()
    assert len(tasks) == 1
    assert tasks[0].title == "Manual review"

    events = (
        await db_session.execute(
            select(OrchWorkflowEventLog).where(
                OrchWorkflowEventLog.workflow_instance_id == instance.id
            )
        )
    ).scalars().all()
    assert any(e.event_type == "instance.completed" for e in events)


async def test_dispatch_is_idempotent_per_event(db_session: AsyncSession):
    await _publish(
        db_session,
        code="wf_idem",
        steps=[
            {
                "step_key": "notify",
                "step_type": "action",
                "configuration": {"action": "CREATE_TASK", "title": "Review"},
            }
        ],
        transitions=[],
    )
    event_id = uuid.uuid4()
    first = await orchestration_engine.dispatch_event(
        db_session, event_id=event_id, event_type=EVENT
    )
    second = await orchestration_engine.dispatch_event(
        db_session, event_id=event_id, event_type=EVENT
    )
    assert len(first) == 1
    assert len(second) == 0
    assert await _count(db_session, OrchWorkflowInstance) == 1


async def test_no_matching_definition_returns_no_instances(db_session: AsyncSession):
    await _publish(
        db_session,
        code="wf_other",
        steps=[
            {
                "step_key": "notify",
                "step_type": "action",
                "configuration": {"action": "CREATE_TASK", "title": "Review"},
            }
        ],
        transitions=[],
    )
    started = await orchestration_engine.dispatch_event(
        db_session,
        event_id=uuid.uuid4(),
        event_type="completely.different",
    )
    assert started == []


async def test_start_workflow_requires_active_definition(db_session: AsyncSession):
    definition = await create_workflow_definition(
        db_session,
        code="wf_inactive",
        name="wf_inactive",
        description=None,
        scope="global",
        trigger={"event_type": EVENT},
        configuration={},
        steps=[],
        transitions=[],
    )
    with pytest.raises(DefinitionNotActiveError):
        await orchestration_engine.start_workflow(
            db_session, definition=definition, context={}
        )


async def test_granular_trigger_condition_filters_event(db_session: AsyncSession):
    await _publish(
        db_session,
        code="wf_cond_trigger",
        steps=[
            {
                "step_key": "notify",
                "step_type": "action",
                "configuration": {"action": "CREATE_TASK", "title": "Review"},
            }
        ],
        transitions=[],
    )
    definition = (
        await db_session.execute(
            select(OrchWorkflowDefinition).where(
                OrchWorkflowDefinition.code == "wf_cond_trigger",
                OrchWorkflowDefinition.status == OrchWorkflowStatus.ACTIVE,
            )
        )
    ).scalar_one()
    definition.trigger = {
        "event_type": EVENT,
        "condition": {"operator": "EQUALS", "field": "amount", "value": 9999},
    }

    started = await orchestration_engine.dispatch_event(
        db_session,
        event_id=uuid.uuid4(),
        event_type=EVENT,
        payload={"amount": 100},
    )
    assert started == []

    started_high = await orchestration_engine.dispatch_event(
        db_session, event_id=uuid.uuid4(), event_type=EVENT, payload={"amount": 9999}
    )
    assert len(started_high) == 1


async def test_task_assignee_authorization_and_successive_advance(
    db_session: AsyncSession,
    test_user,
):
    second = User(
        email="assignee@example.com",
        name="Assignee",
        password_hash="x",
        status="active",
    )
    db_session.add(second)
    await db_session.flush()

    active = await _publish(
        db_session,
        code="wf_two_tasks",
        steps=[
            {
                "step_key": "review",
                "step_type": "task",
                "configuration": {
                    "title": "Review deal",
                    "assignee_user_id": str(second.id),
                },
            },
            {
                "step_key": "confirm",
                "step_type": "task",
                "configuration": {"title": "Confirm signature"},
            },
        ],
        transitions=[{"from_step_key": "review", "to_step_key": "confirm"}],
    )
    started = await orchestration_engine.dispatch_event(
        db_session, event_id=uuid.uuid4(), event_type=EVENT
    )
    instance = await db_session.get(OrchWorkflowInstance, started[0])
    assert instance.status == OrchInstanceStatus.WAITING

    task1 = (
        await db_session.execute(
            select(OrchTask).where(OrchTask.title == "Review deal")
        )
    ).scalar_one()
    assert task1.assignee_user_id == second.id

    with pytest.raises(TaskNotAssignableError):
        await orchestration_engine.complete_task(
            db_session, task_id=task1.id, actor_id=test_user.id
        )

    await orchestration_engine.complete_task(
        db_session, task_id=task1.id, actor_id=second.id, resolution={"approved": True}
    )
    await db_session.refresh(instance)
    assert instance.status == OrchInstanceStatus.WAITING
    task2 = (
        await db_session.execute(
            select(OrchTask).where(OrchTask.title == "Confirm signature")
        )
    ).scalar_one()

    await orchestration_engine.complete_task(
        db_session, task_id=task2.id, actor_id=test_user.id
    )
    await db_session.refresh(instance)
    assert instance.status == OrchInstanceStatus.COMPLETED
    assert active.version >= 1


async def test_conditional_routing_through_transitions(
    db_session: AsyncSession, test_user
):
    await _publish(
        db_session,
        code="wf_route",
        steps=[
            {
                "step_key": "triage",
                "step_type": "task",
                "configuration": {"title": "Triage"},
            },
            {
                "step_key": "auto",
                "step_type": "task",
                "configuration": {"title": "Auto-desk"},
            },
            {"step_key": "manual", "step_type": "task", "configuration": {"title": "Manual"}},
        ],
        transitions=[
            {
                "from_step_key": "triage",
                "to_step_key": "auto",
                "condition": {"operator": "EQUALS", "field": "risk", "value": "low"},
            },
            {"from_step_key": "triage", "to_step_key": "manual"},
        ],
    )
    high_instance = await orchestration_engine.dispatch_event(
        db_session, event_id=uuid.uuid4(), event_type=EVENT, payload={"risk": "high"}
    )
    low_instance = await orchestration_engine.dispatch_event(
        db_session, event_id=uuid.uuid4(), event_type=EVENT, payload={"risk": "low"}
    )
    assert len(high_instance) == 1
    assert len(low_instance) == 1

    triages = (
        await db_session.execute(select(OrchTask).where(OrchTask.title == "Triage"))
    ).scalars().all()
    assert len(triages) == 2
    for triage in triages:
        await orchestration_engine.complete_task(
            db_session, task_id=triage.id, actor_id=test_user.id
        )

    tasks = (await db_session.execute(select(OrchTask.title))).scalars().all()
    # One "Triage" per dispatched instance, each routed onward by risk.
    assert tasks.count("Triage") == 2
    assert tasks.count("Auto-desk") == 1
    assert tasks.count("Manual") == 1


async def test_delay_step_then_timer_advances(db_session: AsyncSession):
    await _publish(
        db_session,
        code="wf_delay",
        steps=[
            {"step_key": "hold", "step_type": "delay", "configuration": {"seconds": 3600}},
            {"step_key": "after", "step_type": "task", "configuration": {"title": "After wait"}},
        ],
        transitions=[{"from_step_key": "hold", "to_step_key": "after"}],
    )
    started = await orchestration_engine.dispatch_event(
        db_session, event_id=uuid.uuid4(), event_type=EVENT
    )
    instance = await db_session.get(OrchWorkflowInstance, started[0])
    assert instance.status == OrchInstanceStatus.WAITING

    timer = (await db_session.execute(select(OrchTimer))).scalar_one()
    assert timer.status == "pending"

    fired = await process_due_timers(
        db_session, now=datetime.now(timezone.utc) + timedelta(hours=2)
    )
    assert fired == 1
    await db_session.refresh(instance)
    assert instance.status == OrchInstanceStatus.WAITING

    after = (
        await db_session.execute(select(OrchTask).where(OrchTask.title == "After wait"))
    ).scalar_one()
    assert after is not None
    await db_session.refresh(timer)
    assert timer.status == "fired"


async def test_event_wait_blocks_then_inbound_event_advances(db_session: AsyncSession):
    await _publish(
        db_session,
        code="wf_await",
        steps=[
            {
                "step_key": "wait_sig",
                "step_type": "event_wait",
                "configuration": {"event_type": "signature.completed"},
            },
            {"step_key": "wrap", "step_type": "task", "configuration": {"title": "Wrap"}},
        ],
        transitions=[{"from_step_key": "wait_sig", "to_step_key": "wrap"}],
    )
    started = await orchestration_engine.dispatch_event(
        db_session, event_id=uuid.uuid4(), event_type=EVENT
    )
    instance = await db_session.get(OrchWorkflowInstance, started[0])
    assert instance.status == OrchInstanceStatus.WAITING

    wait = (await db_session.execute(select(OrchEventWait))).scalar_one()
    assert wait.active is True

    await handle_inbound_event(
        db_session,
        wait_id=wait.id,
        event={"event_id": str(uuid.uuid4()), "signed": True},
    )
    await db_session.refresh(instance)
    assert instance.status == OrchInstanceStatus.WAITING
    task = (await db_session.execute(select(OrchTask))).scalar_one()
    assert task.title == "Wrap"
    await db_session.refresh(wait)
    assert wait.active is False


async def test_missing_event_wait_raises(db_session: AsyncSession):
    with pytest.raises(orchestration_engine.NoMatchingEventWaitError):
        await handle_inbound_event(
            db_session, wait_id=uuid.uuid4(), event={"event_id": str(uuid.uuid4())}
        )


async def test_suspend_resume_cancel(db_session: AsyncSession):
    await _publish(
        db_session,
        code="wf_state",
        steps=[
            {"step_key": "task1", "step_type": "task", "configuration": {"title": "One"}},
        ],
        transitions=[],
    )
    started = await orchestration_engine.dispatch_event(
        db_session, event_id=uuid.uuid4(), event_type=EVENT
    )
    instance = await db_session.get(OrchWorkflowInstance, started[0])
    assert instance.status == OrchInstanceStatus.WAITING

    await orchestration_engine.suspend_instance(db_session, instance.id)
    await db_session.refresh(instance)
    assert instance.status == OrchInstanceStatus.SUSPENDED

    await orchestration_engine.resume_instance(db_session, instance.id)
    await db_session.refresh(instance)
    assert instance.status == OrchInstanceStatus.WAITING

    await orchestration_engine.cancel_instance(db_session, instance.id)
    await db_session.refresh(instance)
    assert instance.status == OrchInstanceStatus.CANCELLED

    with pytest.raises(orchestration_engine.OrchestrationError):
        await orchestration_engine.cancel_instance(db_session, instance.id)


async def test_action_failure_retries_then_incident_and_resolve(
    db_session: AsyncSession,
):
    await _publish(
        db_session,
        code="wf_retry",
        steps=[
            {
                "step_key": "fragile",
                "step_type": "action",
                "configuration": {
                    "action": "CREATE_NOTIFICATION",
                    "organization_id": str(uuid.uuid4()),
                    "max_attempts": 2,
                    "backoff_seconds": 5,
                },
            }
        ],
        transitions=[],
    )
    started = await orchestration_engine.dispatch_event(
        db_session, event_id=uuid.uuid4(), event_type=EVENT
    )
    instance = await db_session.get(OrchWorkflowInstance, started[0])
    # No to_email in config: attempt 1 fails, back-off timer scheduled.
    assert instance.status == OrchInstanceStatus.WAITING
    assert await _count(db_session, OrchActionAttempt) >= 1

    fired = await process_due_timers(
        db_session, now=datetime.now(timezone.utc) + timedelta(minutes=5)
    )
    assert fired == 1

    await db_session.refresh(instance)
    assert instance.status == OrchInstanceStatus.HUMAN_INTERVENTION_REQUIRED
    incident = (await db_session.execute(select(OrchIncident))).scalar_one()
    assert incident.status == "open"
    assert "requires to_email" in incident.description

    await orchestration_engine.resolve_incident(
        db_session, incident.id, resolution="fixed config", actor_id=None
    )
    await db_session.refresh(instance)
    # Missing to_email still fails on retry -> re-raises it.
    assert instance.status == OrchInstanceStatus.HUMAN_INTERVENTION_REQUIRED
    await db_session.refresh(incident)
    assert incident.status == "resolved"
    assert incident.resolved_at is not None


async def test_parallel_runs_all_branches(db_session: AsyncSession):
    await _publish(
        db_session,
        code="wf_parallel",
        steps=[
            {"step_key": "split", "step_type": "parallel", "configuration": {"branches": ["div_a", "div_b"]}},
            {"step_key": "div_a", "step_type": "task", "configuration": {"title": "Branch A"}},
            {"step_key": "div_b", "step_type": "task", "configuration": {"title": "Branch B"}},
        ],
        transitions=[],
    )
    started = await orchestration_engine.dispatch_event(
        db_session, event_id=uuid.uuid4(), event_type=EVENT
    )
    instance = await db_session.get(OrchWorkflowInstance, started[0])
    assert instance.status == OrchInstanceStatus.COMPLETED
    titles = (await db_session.execute(select(OrchTask.title))).scalars().all()
    assert "Branch A" in titles
    assert "Branch B" in titles


async def test_create_notification_action_writes_row(
    db_session: AsyncSession, test_org
):
    await _publish(
        db_session,
        code="wf_notify",
        steps=[
            {
                "step_key": "notify",
                "step_type": "action",
                "configuration": {
                    "action": "CREATE_NOTIFICATION",
                    "organization_id": str(test_org.id),
                    "to_email": "ops@example.com",
                    "subject": "Workflow triggered for {agreement_id}",
                },
            }
        ],
        transitions=[],
    )
    await orchestration_engine.dispatch_event(
        db_session,
        event_id=uuid.uuid4(),
        event_type=EVENT,
        payload={"agreement_id": str(uuid.uuid4())},
    )
    notification = (await db_session.execute(select(Notification))).scalar_one()
    assert notification.organization_id == test_org.id
    assert notification.to_email == "ops@example.com"


async def test_instance_event_log_recorded(db_session: AsyncSession):
    await _publish(
        db_session,
        code="wf_log",
        steps=[
            {"step_key": "task1", "step_type": "task", "configuration": {"title": "Check"}},
        ],
        transitions=[],
    )
    started = await orchestration_engine.dispatch_event(
        db_session, event_id=uuid.uuid4(), event_type=EVENT
    )
    events = await orchestration_service.get_instance_events(db_session, started[0])
    types = [e.event_type for e in events]
    assert "instance.started" in types
    assert "instance.step_waiting" in types


async def test_scope_organization_filters_by_org(db_session: AsyncSession, test_org):
    definition = await create_workflow_definition(
        db_session,
        code="wf_org_scoped",
        name="wf_org_scoped",
        description=None,
        scope="organization",
        trigger={"event_type": EVENT},
        configuration={},
        steps=[
            {
                "step_key": "notify",
                "step_type": "action",
                "configuration": {"action": "CREATE_TASK", "title": "Org task"},
            }
        ],
        transitions=[],
        organization_id=test_org.id,
    )
    await orchestration_service.publish_workflow_definition(db_session, definition.id)

    other_org = await orchestration_engine.dispatch_event(
        db_session,
        event_id=uuid.uuid4(),
        event_type=EVENT,
        organization_id=uuid.uuid4(),
    )
    assert other_org == []

    in_org = await orchestration_engine.dispatch_event(
        db_session,
        event_id=uuid.uuid4(),
        event_type=EVENT,
        organization_id=test_org.id,
    )
    assert len(in_org) == 1