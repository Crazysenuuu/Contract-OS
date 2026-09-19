"""Approval engine service.

Manages multi-stage approval workflows:
- Create approval definitions
- Start approval process
- Record decisions
- Advance through stages
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
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
