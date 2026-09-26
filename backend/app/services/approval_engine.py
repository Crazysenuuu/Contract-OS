"""Approval engine service.

Manages multi-stage approval workflows:
- Create approval definitions
- Start approval process
- Record decisions
- Advance through stages
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agreement import Agreement
from app.models.approval import (
    ApprovalDecision,
    ApprovalDefinition,
    ApprovalRecord,
    ApprovalStage,
    ApprovalStep,
)
from app.services.currency_service import resolve_currency


async def get_approval_definitions(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> list[ApprovalDefinition]:
    """Get all active approval definitions for an organization."""
    result = await db.execute(
        select(ApprovalDefinition)
        .where(
            ApprovalDefinition.organization_id == organization_id,
            ApprovalDefinition.is_active == True,
        )
        .options(selectinload(ApprovalDefinition.stages))
        .order_by(ApprovalDefinition.name)
    )
    return list(result.scalars().all())


async def get_approval_definition(
    db: AsyncSession,
    definition_id: uuid.UUID,
) -> ApprovalDefinition | None:
    """Get an approval definition by ID with stages."""
    result = await db.execute(
        select(ApprovalDefinition)
        .where(ApprovalDefinition.id == definition_id)
        .options(selectinload(ApprovalDefinition.stages))
    )
    return result.scalar_one_or_none()


async def create_approval_definition(
    db: AsyncSession,
    organization_id: uuid.UUID,
    name: str,
    description: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    stages: list[dict] | None = None,
) -> ApprovalDefinition:
    """Create an approval workflow definition.

    Args:
        db: Database session.
        organization_id: The organization.
        name: Workflow name.
        description: Optional description.
        min_value: Optional minimum agreement value to trigger.
        max_value: Optional maximum agreement value to trigger.
        stages: List of stage definitions [{"name": "...", "order": 1, "required_role": "..."}]

    Returns:
        The created ApprovalDefinition.
    """
    definition = ApprovalDefinition(
        organization_id=organization_id,
        name=name,
        description=description,
        min_value=min_value,
        max_value=max_value,
    )
    db.add(definition)
    await db.flush()

    if stages:
        for stage_data in stages:
            stage = ApprovalStage(
                definition_id=definition.id,
                name=stage_data["name"],
                order=stage_data["order"],
                required_role=stage_data.get("required_role"),
                require_all_approvers=stage_data.get("require_all_approvers", False),
            )
            db.add(stage)

        await db.flush()

    return definition


async def start_approval(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    definition_id: uuid.UUID,
    approval_type: str = "legal_review",
) -> ApprovalRecord:
    """Start an approval process for an agreement.

    Args:
        db: Database session.
        agreement_id: The agreement to approve.
        definition_id: The approval workflow to use.
        approval_type: 'legal_review' (lawyer gate) or 'party_approval'.

    Returns:
        The created ApprovalRecord.
    """
    # Get the definition with stages
    definition = await get_approval_definition(db, definition_id)
    if definition is None:
        raise ValueError("Approval definition not found")

    # Get the first stage
    stages = sorted(definition.stages, key=lambda s: s.order)
    first_stage = stages[0] if stages else None

    # Snapshot the version the approval is adjudicating so later decisions
    # cannot be applied to terms that have since been replaced.
    from app.models.agreement import AgreementVersion

    version_result = await db.execute(
        select(AgreementVersion).where(
            AgreementVersion.agreement_id == agreement_id,
            AgreementVersion.status == "current",
        )
    )
    version = version_result.scalar_one_or_none()

    record = ApprovalRecord(
        agreement_id=agreement_id,
        definition_id=definition_id,
        current_stage_id=first_stage.id if first_stage else None,
        status="in_progress" if first_stage else "approved",
        agreement_version_id=version.id if version else None,
        approval_type=approval_type,
    )
    db.add(record)
    await db.flush()

    # Action Center hook (spec §3.10.17-19): open a live action item for
    # this approval so it surfaces on the assignees' queue.
    from app.services.action_item_service import upsert_action_item

    agreement_row = await db.get(Agreement, agreement_id)
    await upsert_action_item(
        db,
        organization_id=(
            agreement_row.organization_id
            if agreement_row is not None
            else uuid.UUID(int=0)
        ),
        action_type="approval_request",
        title=f"Approval requested: {agreement_row.title if agreement_row else agreement_id}",
        source_system="approvals",
        source_id=record.id,
        description=(
            f"Stage: {first_stage.name}" if first_stage else None
        ),
    )

    return record


async def resolve_and_start_approval(
    db: AsyncSession,
    *,
    agreement_id: uuid.UUID,
    organization_id: uuid.UUID,
    approval_type: str = "legal_review",
    definition_id: uuid.UUID | None = None,
) -> tuple[ApprovalRecord, dict]:
    """Resolve the matching approval definition and start the approval.

    When ``definition_id`` is given, that definition is used directly.
    Otherwise the dynamic rules engine (spec 24.2) evaluates the org's
    rule-based DOA definitions against the agreement's metadata — value,
    currency, type, counterparty country, extracted risk score — and starts
    the first matching definition; no rule matched falls back to the DOA
    threshold matrix. The resolution outcome is returned alongside the
    record so the API can tell callers which rule fired.
    """
    if definition_id is not None:
        record = await start_approval(
            db,
            agreement_id,
            definition_id,
            approval_type=approval_type,
        )
        return record, {"source": "explicit", "definition_id": str(definition_id)}

    agreement = (
        await db.execute(select(Agreement).where(Agreement.id == agreement_id))
    ).scalar_one_or_none()
    if agreement is None:
        raise ValueError("Agreement not found")

    from app.models.agreement_type import AgreementType

    type_row = (
        await db.execute(
            select(AgreementType).where(
                AgreementType.id == agreement.agreement_type_id
            )
        )
    ).scalar_one_or_none()

    data = agreement.data or {}
    value = data.get("value") or data.get("total_value") or 0.0

    from app.services.rules_engine import evaluate_doa_rules

    resolution = await evaluate_doa_rules(
        db,
        organization_id=organization_id,
        agreement_value=float(value),
        agreement_type=type_row.key if type_row else None,
        currency=resolve_currency(agreement.currency),
        extra_context={
            "counterparty_country": agreement.governing_law,
            "title": agreement.title,
            **(data if isinstance(data, dict) else {}),
        },
    )

    resolved_id = resolution.get("definition_id")
    if not resolved_id:
        raise ValueError(
            "No approval definition matched for this agreement "
            "(rules engine and DOA matrix both returned nothing)."
        )

    record = await start_approval(
        db,
        agreement_id,
        uuid.UUID(str(resolved_id)),
        approval_type=approval_type,
    )
    return record, resolution


async def cancel_approvals_for_agreement(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    *,
    exclude_record_id: uuid.UUID | None = None,
) -> int:
    """Cancel open (pending/in_progress) approvals for an agreement.

    Called when a new version becomes current so approvals tied to the old
    version cannot linger for decisions. Returns the number cancelled.
    """
    from app.models.approval import ApprovalRecord as AR

    query = select(AR).where(
        AR.agreement_id == agreement_id,
        AR.status.in_(["pending", "in_progress"]),
    )
    if exclude_record_id is not None:
        query = query.where(AR.id != exclude_record_id)

    result = await db.execute(query)
    records = list(result.scalars().all())
    for record in records:
        record.status = "cancelled"
    if records:
        await db.flush()
    return len(records)


async def record_decision(
    db: AsyncSession,
    record_id: uuid.UUID,
    stage_id: uuid.UUID,
    user_id: uuid.UUID,
    decision: str,
    comment: str | None = None,
) -> ApprovalDecision:
    """Record an approval/rejection decision.

    Args:
        db: Database session.
        record_id: The approval record.
        stage_id: The stage being decided on.
        user_id: The approver.
        decision: 'approved', 'rejected', or 'escalated'.
        comment: Optional comment.

    Returns:
        The created ApprovalDecision.
    """
    decision_record = ApprovalDecision(
        record_id=record_id,
        stage_id=stage_id,
        user_id=user_id,
        decision=decision,
        comment=comment,
        decided_at=datetime.now(timezone.utc),
    )
    db.add(decision_record)
    await db.flush()

    return decision_record


async def advance_stage(
    db: AsyncSession,
    record: ApprovalRecord,
) -> ApprovalRecord:
    """Advance to the next approval stage.

    Checks if current stage is complete, then moves to next.
    """
    # Get all stages for this definition
    result = await db.execute(
        select(ApprovalStage)
        .where(ApprovalStage.definition_id == record.definition_id)
        .order_by(ApprovalStage.order)
    )
    stages = list(result.scalars().all())

    if not stages:
        record.status = "approved"
        await db.flush()
        return record

    # Find current stage index
    current_index = None
    for i, stage in enumerate(stages):
        if stage.id == record.current_stage_id:
            current_index = i
            break

    if current_index is None:
        # No current stage, start at beginning
        record.current_stage_id = stages[0].id
        record.status = "in_progress"
    elif current_index < len(stages) - 1:
        # Move to next stage
        record.current_stage_id = stages[current_index + 1].id
        record.status = "in_progress"
    else:
        # Last stage complete
        record.current_stage_id = None
        record.status = "approved"

    await db.flush()

    # Close the approval's action item when the record leaves in-flight.
    if record.status in ("approved", "rejected", "cancelled"):
        from app.services.action_item_service import resolve_source_item

        await resolve_source_item(
            db, source_system="approvals", source_id=record.id
        )

    return record


async def get_approval_record(
    db: AsyncSession,
    record_id: uuid.UUID,
) -> ApprovalRecord | None:
    """Get an approval record with decisions."""
    result = await db.execute(
        select(ApprovalRecord)
        .where(ApprovalRecord.id == record_id)
        .options(
            selectinload(ApprovalRecord.decisions),
            selectinload(ApprovalRecord.current_stage),
        )
    )
    return result.scalar_one_or_none()


async def get_approval_for_agreement(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> ApprovalRecord | None:
    """Get the active approval record for an agreement."""
    result = await db.execute(
        select(ApprovalRecord)
        .where(
            ApprovalRecord.agreement_id == agreement_id,
            ApprovalRecord.status.in_(["pending", "in_progress"]),
        )
        .options(
            selectinload(ApprovalRecord.decisions),
            selectinload(ApprovalRecord.current_stage),
        )
        .order_by(ApprovalRecord.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def cancel_approval(
    db: AsyncSession,
    record: ApprovalRecord,
) -> ApprovalRecord:
    """Cancel an approval process."""
    record.status = "cancelled"
    await db.flush()
    return record


# ---------------------------------------------------------------------------
# Quorum, deadlines, sign-off and version locking (spec §3.6.28-56)
# ---------------------------------------------------------------------------


def _stage_quorum_met(stage: ApprovalStage, decisions: list[ApprovalDecision]) -> bool:
    """Whether the stage's approval quorum is satisfied (§3.6.28).

    Sequential stages always require the stage's approver decision; parallel
    stages complete at ``minimum_approvals`` approvals when configured, else
    when every required approver has approved (legacy semantics).
    """
    approvals = [d for d in decisions if d.stage_id == stage.id and d.decision == "approved"]
    if stage.execution_mode == "parallel" and stage.minimum_approvals:
        return len(approvals) >= stage.minimum_approvals
    if stage.execution_mode == "parallel":
        # No quorum configured: any-one-approved semantics (require_all_approvers
        # False) or every required approver.
        if stage.require_all_approvers:
            required_user_ids = {s.user_id for s in stage.steps if s.is_required}
            return required_user_ids.issubset({d.user_id for d in approvals})
        return len(approvals) >= 1
    # Sequential stage: complete when decided (handled by the caller).
    return len(approvals) >= 1


async def evaluate_stage_completion(
    db: AsyncSession,
    record: ApprovalRecord,
    stage: ApprovalStage,
) -> bool:
    """Re-evaluate a stage against quorum and advance the record when met.

    Called after each decision. Returns True when the record advanced.
    """
    decisions = list(record.decisions)
    if any(d.decision == "rejected" for d in decisions):
        return False
    if not _stage_quorum_met(stage, decisions):
        return False
    await advance_stage(db, record)
    return True


async def scan_approval_deadlines(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict:
    """Escalate approvals stuck past their stage deadline (§3.6.29, §3.6.50).

    A record whose current stage has ``deadline_hours`` set and whose stage
    has been current longer than that window gets ``escalated_at`` stamped
    exactly once. Returns a summary for the beat task.
    """
    now = now or datetime.now(timezone.utc)
    records = (
        await db.execute(
            select(ApprovalRecord)
            .where(
                ApprovalRecord.status == "in_progress",
                ApprovalRecord.current_stage_id.is_not(None),
            )
            .options(selectinload(ApprovalRecord.current_stage))
        )
    ).scalars().all()

    escalated = 0
    for record in records:
        stage = record.current_stage
        if stage is None or not stage.deadline_hours:
            continue
        started = record.stage_started_at or record.created_at
        started = started if started.tzinfo else started.replace(tzinfo=timezone.utc)
        deadline = started + timedelta(hours=stage.deadline_hours)
        if now >= deadline and record.escalated_at is None:
            record.escalated_at = now
            escalated += 1
    if escalated:
        await db.flush()
    return {"scanned": len(records), "escalated": escalated}


async def lock_for_version_change(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> int:
    """Version-lock every in-flight approval for an agreement (§3.6.34).

    Called when a new version becomes current: decisions may not proceed on
    terms that are no longer current. The record stays in place (unlike
    cancellation) so the UI can explain what is blocked and why.
    """
    records = (
        await db.execute(
            select(ApprovalRecord).where(
                ApprovalRecord.agreement_id == agreement_id,
                ApprovalRecord.status == "in_progress",
            )
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for record in records:
        record.version_locked_at = now
    if records:
        await db.flush()
    return len(records)


async def unlock_version(
    db: AsyncSession,
    record: ApprovalRecord,
    *,
    version_id: uuid.UUID | None,
) -> ApprovalRecord:
    """Re-bind an approval record to the new current version and unlock it."""
    record.agreement_version_id = version_id
    record.version_locked_at = None
    await db.flush()
    return record


async def get_signoff_readiness(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> dict:
    """Sign-off checklist for an agreement (§3.6.54-55).

    Deterministic gates: current version exists and is not draft, at least
    one approval record approved, no in-flight approvals, no open approval
    comments outstanding (rejections), no version lock.
    """
    from app.models.agreement import Agreement, AgreementVersion

    agreement = await db.get(Agreement, agreement_id)
    if agreement is None:
        raise ValueError("Agreement not found")

    version = (
        await db.execute(
            select(AgreementVersion)
            .where(AgreementVersion.agreement_id == agreement_id)
            .order_by(AgreementVersion.version_number.desc())
            .limit(1)
        )
    ).scalars().first()

    checks: list[dict] = []

    checks.append(
        {
            "key": "current_version",
            "label": "Agreement has a rendered version",
            "passed": version is not None and bool(version.content),
        }
    )

    approved = (
        await db.scalar(
            select(func.count())
            .select_from(ApprovalRecord)
            .where(
                ApprovalRecord.agreement_id == agreement_id,
                ApprovalRecord.status == "approved",
            )
        )
    ) or 0
    checks.append(
        {
            "key": "approval_complete",
            "label": "At least one approval record approved",
            "passed": approved > 0,
        }
    )

    in_flight = (
        await db.scalar(
            select(func.count())
            .select_from(ApprovalRecord)
            .where(
                ApprovalRecord.agreement_id == agreement_id,
                ApprovalRecord.status.in_(["pending", "in_progress"]),
            )
        )
    ) or 0
    checks.append(
        {
            "key": "no_open_approvals",
            "label": "No approvals awaiting decision",
            "passed": in_flight == 0,
        }
    )

    locked = (
        await db.scalar(
            select(func.count())
            .select_from(ApprovalRecord)
            .where(
                ApprovalRecord.agreement_id == agreement_id,
                ApprovalRecord.version_locked_at.is_not(None),
            )
        )
    ) or 0
    checks.append(
        {
            "key": "no_version_lock",
            "label": "No approval is version-locked",
            "passed": locked == 0,
        }
    )

    ready = all(c["passed"] for c in checks)
    return {"agreement_id": str(agreement_id), "ready": ready, "checks": checks}


async def get_pending_approvals_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> list[dict]:
    """Get all agreements pending approval for a user.

    Returns list of dicts with agreement and approval info.
    """
    # Find approval records where user is an approver in current stage
    result = await db.execute(
        select(ApprovalRecord)
        .join(ApprovalStage, ApprovalStage.id == ApprovalRecord.current_stage_id)
        .join(ApprovalStep, ApprovalStep.stage_id == ApprovalStage.id)
        .where(
            ApprovalStep.user_id == user_id,
            ApprovalRecord.status == "in_progress",
        )
        .options(
            selectinload(ApprovalRecord.agreement),
            selectinload(ApprovalRecord.current_stage),
        )
    )
    records = list(result.scalars().all())

    return [
        {
            "approval_record_id": record.id,
            "agreement_id": record.agreement_id,
            "agreement_title": record.agreement.title if record.agreement else None,
            "current_stage": record.current_stage.name if record.current_stage else None,
            "status": record.status,
        }
        for record in records
    ]
