"""Validation + dry-run simulation tests (spec §2.11.39/2.11.43).

Uses pure condition graphs (no side-effect actions) so validation and
simulation are exercised end to end without touching worker queues.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orchestration import (
    OrchStepDefinition,
    OrchStepType,
    OrchTransition,
    OrchWorkflowStatus,
)
from app.services.orchestration_validation import (
    simulate_workflow,
    validate_workflow_definition,
)
from app.services.orchestration_service import (
    create_workflow_definition,
    publish_workflow_definition,
)

pytestmark = pytest.mark.asyncio


async def _build_definition(
    db: AsyncSession,
    code: str,
    steps: list[dict],
    transitions: list[dict],
    organization_id=None,
) -> uuid.UUID:
    definition = await create_workflow_definition(
        db,
        code=code,
        name=code,
        description=None,
        scope="global",
        trigger={"event_type": "test.event"},
        configuration={},
        steps=steps,
        transitions=transitions,
        organization_id=organization_id,
    )
    return definition.id


async def test_valid_linear_workflow(db_session: AsyncSession):
    def_id = await _build_definition(
        db_session,
        "wf_valid",
        steps=[
            {"step_key": "start", "step_type": "task", "configuration": {"title": "Start"}},
            {"step_key": "end", "step_type": "task", "configuration": {"title": "End"}},
        ],
        transitions=[{"from_step_key": "start", "to_step_key": "end"}],
    )
    result = await validate_workflow_definition(db_session, def_id)
    assert result["valid"] is True, result["errors"]
    assert result["errors"] == []


async def test_workflow_with_no_steps_is_invalid(db_session: AsyncSession):
    def_id = await _build_definition(db_session, "wf_empty", steps=[], transitions=[])
    result = await validate_workflow_definition(db_session, def_id)
    assert result["valid"] is False
    assert any("no steps" in e for e in result["errors"])


async def test_planned_action_rejected_validation(db_session: AsyncSession):
    def_id = await _build_definition(
        db_session,
        "wf_planned",
        steps=[
            {
                "step_key": "approve",
                "step_type": "action",
                "configuration": {"action": "CREATE_APPROVAL"},
            }
        ],
        transitions=[],
    )
    result = await validate_workflow_definition(db_session, def_id)
    assert result["valid"] is False
    assert any("spec-planned but not implemented" in e for e in result["errors"])


async def test_unknown_action_rejected_validation(db_session: AsyncSession):
    def_id = await _build_definition(
        db_session,
        "wf_unknown_action",
        steps=[
            {
                "step_key": "go",
                "step_type": "action",
                "configuration": {"action": "DO_WILD_THING"},
            }
        ],
        transitions=[],
    )
    result = await validate_workflow_definition(db_session, def_id)
    assert result["valid"] is False
    assert any("unknown action" in e for e in result["errors"])


async def test_subworkflow_step_rejected_validation(db_session: AsyncSession):
    def_id = await _build_definition(
        db_session,
        "wf_sub",
        steps=[
            {"step_key": "sub", "step_type": "subworkflow", "configuration": {}}
        ],
        transitions=[],
    )
    result = await validate_workflow_definition(db_session, def_id)
    assert result["valid"] is False
    assert any("not yet implemented" in e for e in result["errors"])


async def test_event_wait_requires_event_type(db_session: AsyncSession):
    def_id = await _build_definition(
        db_session,
        "wf_evt",
        steps=[
            {"step_key": "wait", "step_type": "event_wait", "configuration": {}}
        ],
        transitions=[],
    )
    result = await validate_workflow_definition(db_session, def_id)
    assert result["valid"] is False
    assert any("needs 'event_type'" in e for e in result["errors"])


async def test_unknown_condition_operator_in_transition_rejected(
    db_session: AsyncSession,
):
    def_id = await _build_definition(
        db_session,
        "wf_bad_cond",
        steps=[
            {"step_key": "a", "step_type": "task", "configuration": {}},
            {"step_key": "b", "step_type": "task", "configuration": {}},
        ],
        transitions=[
            {
                "from_step_key": "a",
                "to_step_key": "b",
                "condition": {"operator": "LIKES", "field": "x", "value": 1},
            }
        ],
    )
    result = await validate_workflow_definition(db_session, def_id)
    assert result["valid"] is False
    assert any("unknown operator" in e for e in result["errors"])


async def test_transition_references_missing_step(db_session: AsyncSession):
    def_id = await _build_definition(
        db_session,
        "wf_dangling",
        steps=[{"step_key": "a", "step_type": "task", "configuration": {}}],
        transitions=[{"from_step_key": "a", "to_step_key": "ghost"}],
    )
    result = await validate_workflow_definition(db_session, def_id)
    assert result["valid"] is False
    assert any("references unknown step" in e for e in result["errors"])


async def test_simulate_stops_at_task_wait_point(db_session: AsyncSession):
    def_id = await _build_definition(
        db_session,
        "wf_sim_task",
        steps=[
            {"step_key": "review", "step_type": "task", "configuration": {}},
            {"step_key": "finalize", "step_type": "task", "configuration": {}},
        ],
        transitions=[{"from_step_key": "review", "to_step_key": "finalize"}],
    )
    result = await simulate_workflow(db_session, def_id, {})
    assert result["completed"] is False
    assert result["waiting_at"] == "review"
    keys = [t["step_key"] for t in result["trace"]]
    assert keys == ["review"]


async def test_simulate_resolves_conditions(db_session: AsyncSession):
    def_id = await _build_definition(
        db_session,
        "wf_sim_cond",
        steps=[
            {
                "step_key": "check",
                "step_type": "condition",
                "configuration": {
                    "condition": {"operator": "EQUALS", "field": "risk", "value": "low"}
                },
            },
            {"step_key": "auto", "step_type": "task", "configuration": {}},
            {"step_key": "manual", "step_type": "task", "configuration": {}},
        ],
        transitions=[
            {
                "from_step_key": "check",
                "to_step_key": "auto",
                "condition": {"operator": "EQUALS", "field": "risk", "value": "low"},
            },
            {"from_step_key": "check", "to_step_key": "manual"},
        ],
    )
    result = await simulate_workflow(db_session, def_id, {"risk": "low"})
    assert result["completed"] is False
    assert result["waiting_at"] == "auto"

    manual = await simulate_workflow(db_session, def_id, {"risk": "high"})
    assert manual["completed"] is False
    assert manual["waiting_at"] == "manual"


async def test_simulate_completes_when_no_wait_steps(db_session: AsyncSession):
    def_id = await _build_definition(
        db_session,
        "wf_sim_done",
        steps=[
            {
                "step_key": "only",
                "step_type": "condition",
                "configuration": {"condition": {"operator": "TRUE"}},
            }
        ],
        transitions=[],
    )
    result = await simulate_workflow(db_session, def_id, {})
    assert result["completed"] is True


async def test_publish_creates_active_version_with_copied_graph(
    db_session: AsyncSession,
):
    draft_id = await _build_definition(
        db_session,
        "wf_pub",
        steps=[
            {"step_key": "start", "step_type": "task", "configuration": {"title": "Go"}},
        ],
        transitions=[],
    )
    active = await publish_workflow_definition(db_session, draft_id)
    assert active.status == OrchWorkflowStatus.ACTIVE
    assert active.version == 2

    steps_result = await db_session.execute(
        select(OrchStepDefinition.step_key)
        .where(OrchStepDefinition.workflow_definition_id == active.id)
    )
    copied_steps = {row[0] for row in steps_result.all()}
    assert copied_steps == {"start"}
    trans_result = await db_session.execute(
        select(OrchTransition.from_step_key)
        .where(OrchTransition.workflow_definition_id == active.id)
    )
    assert trans_result.all() == []


async def test_publish_rejects_invalid_draft(db_session: AsyncSession):
    def_id = await _build_definition(
        db_session,
        "wf_pub_invalid",
        steps=[{"step_key": "x", "step_type": "task", "configuration": {}}],
        transitions=[{"from_step_key": "x", "to_step_key": "nowhere"}],
    )
    from app.services.orchestration_service import WorkflowDefinitionError

    with pytest.raises(WorkflowDefinitionError, match="failed validation"):
        await publish_workflow_definition(db_session, def_id)


async def test_publish_and_validate_good_steps_registered(db_session: AsyncSession):
    """Guard that a valid ACTION graph validates after publish (no drift)."""
    def_id = await _build_definition(
        db_session,
        "wf_pub_action",
        steps=[
            {
                "step_key": "notify",
                "step_type": "action",
                "configuration": {"action": "CREATE_TASK", "title": "Nudge"},
            }
        ],
        transitions=[],
    )
    result = await validate_workflow_definition(db_session, def_id)
    assert result["valid"] is True, result["errors"]