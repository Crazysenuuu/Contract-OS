import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import (
    WorkflowDefinition,
    WorkflowState,
    WorkflowTransition,
)


MUTUAL_NDA_STATES = [
    {"key": "DRAFT", "name": "Draft", "is_initial": True, "is_terminal": False},
    {"key": "INTERNAL_REVIEW", "name": "Internal Review", "is_initial": False, "is_terminal": False},
    {"key": "SENT", "name": "Sent to Counterparty", "is_initial": False, "is_terminal": False},
    {"key": "VIEWED", "name": "Viewed by Counterparty", "is_initial": False, "is_terminal": False},
    {"key": "NEGOTIATION", "name": "In Negotiation", "is_initial": False, "is_terminal": False},
    {"key": "APPROVED", "name": "Approved by Both Parties", "is_initial": False, "is_terminal": False},
    {"key": "SIGNING", "name": "Signing in Progress", "is_initial": False, "is_terminal": False},
    {"key": "EXECUTED", "name": "Executed", "is_initial": False, "is_terminal": True},
    {"key": "TERMINATED", "name": "Terminated", "is_initial": False, "is_terminal": True},
]

MUTUAL_NDA_TRANSITIONS = [
    {
        "from": "DRAFT",
        "to": "INTERNAL_REVIEW",
        "action_key": "submit_for_review",
        "name": "Submit for Internal Review",
        "description": "Submit the draft for internal legal review",
        "requires_permission": "agreement.approve",
    },
    {
        "from": "DRAFT",
        "to": "SENT",
        "action_key": "send_directly",
        "name": "Send Directly",
        "description": "Send to counterparty without internal review",
        "requires_permission": "agreement.manage_participants",
    },
    {
        "from": "INTERNAL_REVIEW",
        "to": "SENT",
        "action_key": "approve_and_send",
        "name": "Approve and Send",
        "description": "Approve after internal review and send to counterparty",
        "requires_permission": "agreement.approve",
    },
    {
        "from": "INTERNAL_REVIEW",
        "to": "DRAFT",
        "action_key": "reject_to_draft",
        "name": "Reject to Draft",
        "description": "Send back to draft for revisions",
        "requires_permission": "agreement.approve",
    },
    {
        "from": "SENT",
        "to": "VIEWED",
        "action_key": "counterparty_views",
        "name": "Counterparty Views",
        "description": "Counterparty opens the agreement link",
        "requires_permission": None,
    },
    {
        "from": "VIEWED",
        "to": "NEGOTIATION",
        "name": "Request Change",
        "action_key": "request_change",
        "description": "Counterparty requests a change",
        "requires_permission": None,
    },
    {
        "from": "VIEWED",
        "to": "APPROVED",
        "name": "Accept Agreement",
        "action_key": "accept",
        "description": "Counterparty accepts the agreement as-is",
        "requires_permission": None,
    },
    {
        "from": "NEGOTIATION",
        "to": "APPROVED",
        "name": "Final Acceptance",
        "action_key": "accept",
        "description": "Both parties accept the final terms",
        "requires_permission": None,
    },
    {
        "from": "NEGOTIATION",
        "to": "NEGOTIATION",
        "name": "Counter-Propose",
        "action_key": "counter_propose",
        "description": "Propose counter-changes during negotiation",
        "requires_permission": None,
    },
    {
        "from": "APPROVED",
        "to": "SIGNING",
        "name": "Start Signing",
        "action_key": "start_signing",
        "description": "Initiate the signature collection process",
        "requires_permission": "agreement.request_signature",
    },
    {
        "from": "SIGNING",
        "to": "EXECUTED",
        "name": "All Signatures Collected",
        "action_key": "complete_signing",
        "description": "All required signatures have been collected",
        "requires_permission": None,
    },
    {
        "from": "EXECUTED",
        "to": "TERMINATED",
        "name": "Terminate Agreement",
        "action_key": "terminate",
        "description": "Terminate the executed agreement",
        "requires_permission": "agreement.approve_termination",
    },
]


async def seed_workflow(
    db: AsyncSession,
    workflow_key: str = "mutual_nda_lk_v1",
) -> WorkflowDefinition:
    existing = await db.execute(
        select(WorkflowDefinition)
        .where(WorkflowDefinition.key == workflow_key)
    )
    if existing.scalar_one_or_none() is not None:
        return (await db.execute(
            select(WorkflowDefinition)
            .where(WorkflowDefinition.key == workflow_key)
        )).scalar_one()

    workflow = WorkflowDefinition(
        name="Mutual NDA Workflow (Sri Lanka)",
        key=workflow_key,
        version=1,
        is_active=True,
    )
    db.add(workflow)
    await db.flush()

    state_objects = {}
    for state_data in MUTUAL_NDA_STATES:
        state = WorkflowState(
            workflow_id=workflow.id,
            **state_data,
        )
        db.add(state)
        state_objects[state_data["key"]] = state

    await db.flush()

    for trans_data in MUTUAL_NDA_TRANSITIONS:
        transition = WorkflowTransition(
            workflow_id=workflow.id,
            from_state_id=state_objects[trans_data["from"]].id,
            to_state_id=state_objects[trans_data["to"]].id,
            action_key=trans_data["action_key"],
            name=trans_data["name"],
            description=trans_data.get("description"),
            requires_confirmation=trans_data.get("requires_confirmation", False),
            requires_permission=trans_data.get("requires_permission"),
        )
        db.add(transition)

    await db.flush()

    return workflow
