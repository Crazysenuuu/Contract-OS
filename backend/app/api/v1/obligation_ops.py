"""Obligation lifecycle API endpoints (spec 2.08.40).

Top-level /obligations surface for detail, confirmation, assignment,
start/complete/waive/cancel, deadlines, evidence and event history.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.obligation import (
    Obligation,
    ObligationAssignee,
    ObligationDeadline,
    ObligationEvent,
    ObligationEvidence,
)
from app.models.rbac import OrganizationMember
from app.models.user import User
from app.models.agreement_access import AgreementParty
from app.services import obligation_lifecycle

router = APIRouter(
    prefix="/obligations",
    tags=["Obligations"],
)


# --- Schemas ---

class DeadlineCreate(BaseModel):
    deadline_type: str
    due_at: datetime | None = None
    calculation_rule: dict | None = None
    source_date: datetime | None = None
    source_event: str | None = None
    timezone: str | None = None


class AssigneeCreate(BaseModel):
    agreement_party_id: uuid.UUID | None = None
    member_id: uuid.UUID | None = None
    responsibility_type: str = Field(default="PERFORMER")
    primary_assignee: bool = False


class EvidenceCreate(BaseModel):
    document_id: uuid.UUID | None = None
    description: str | None = None


class EvidenceReview(BaseModel):
    approve: bool = True
    comment: str | None = None


class SourceRef(BaseModel):
    document_id: uuid.UUID | None
    version_id: uuid.UUID | None
    clause_id: uuid.UUID | None
    text: str | None


class AssigneeResponse(BaseModel):
    id: uuid.UUID
    agreement_party_id: uuid.UUID | None
    member_id: uuid.UUID | None
    responsibility_type: str
    primary_assignee: bool

    model_config = {"from_attributes": True}


class DeadlineResponse(BaseModel):
    id: uuid.UUID
    deadline_type: str
    due_at: datetime | None
    calculation_rule: dict | None
    source_date: datetime | None
    source_event: str | None
    timezone: str | None
    status: str

    model_config = {"from_attributes": True}


class EvidenceResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID | None
    description: str | None
    submitted_by: uuid.UUID
    status: str
    submitted_at: datetime
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    review_comment: str | None

    model_config = {"from_attributes": True}


class EventResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    actor_id: uuid.UUID | None
    previous_status: str | None
    new_status: str | None
    metadata: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ObligationDetail(BaseModel):
    id: uuid.UUID
    agreement_id: uuid.UUID
    title: str | None
    description: str
    owner_party: str | None
    obligation_type: str
    status: str
    criticality: str | None
    evidence_status: str | None
    due_date: object | None = None
    source: SourceRef
    assignees: list[AssigneeResponse]
    deadlines: list[DeadlineResponse]
    evidence: list[EvidenceResponse]
    reminders: list[dict]
    events: list[EventResponse]
    created_at: datetime


async def _get_authorized_obligation(
    obligation_id: uuid.UUID,
    current_user: User,
    org_id: uuid.UUID,
    db: AsyncSession,
) -> Obligation:
    result = await db.execute(
        select(Obligation)
        .options(
            selectinload(Obligation.assignees),
            selectinload(Obligation.deadlines),
            selectinload(Obligation.evidence_items),
            selectinload(Obligation.reminders),
            selectinload(Obligation.events),
        )
        .where(Obligation.id == obligation_id)
    )
    obligation = result.scalar_one_or_none()
    if obligation is None:
        raise HTTPException(status_code=404, detail="Obligation not found")
    await verify_agreement_access(
        agreement_id=obligation.agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )
    return obligation


def _detail(obligation: Obligation) -> ObligationDetail:
    source = SourceRef(
        document_id=obligation.source_document_id,
        version_id=obligation.source_version_id,
        clause_id=obligation.source_clause_id,
        text=obligation.source_text,
    )
    return ObligationDetail(
        id=obligation.id,
        agreement_id=obligation.agreement_id,
        title=obligation.title,
        description=obligation.description,
        owner_party=obligation.owner_party,
        obligation_type=obligation.obligation_type,
        status=obligation.status,
        criticality=obligation.criticality,
        evidence_status=obligation.evidence_status,
        due_date=obligation.due_date,
        source=source,
        assignees=[
            AssigneeResponse.model_validate(a) for a in obligation.assignees
        ],
        deadlines=[
            DeadlineResponse.model_validate(d) for d in obligation.deadlines
        ],
        evidence=[
            EvidenceResponse.model_validate(e) for e in obligation.evidence_items
        ],
        reminders=[
            {"reminder_date": r.reminder_date, "reminder_type": r.reminder_type,
             "status": r.status}
            for r in obligation.reminders
        ],
        events=[
            EventResponse(
                id=e.id,
                event_type=e.event_type,
                actor_id=e.actor_id,
                previous_status=e.previous_status,
                new_status=e.new_status,
                metadata=e.metadata_json,
                created_at=e.created_at,
            )
            for e in obligation.events
        ],
        created_at=obligation.created_at,
    )


async def _transition(
    obligation: Obligation,
    action,
    error_code: int = 409,
):
    try:
        await action
    except ValueError as exc:
        raise HTTPException(status_code=error_code, detail=str(exc))


# --- Endpoints ---

@router.get("/{obligation_id}", response_model=ObligationDetail)
async def get_obligation_detail(
    obligation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    obligation = await _get_authorized_obligation(
        obligation_id, current_user, org_id, db
    )
    return _detail(obligation)


@router.post("/{obligation_id}/confirm", response_model=ObligationDetail)
async def confirm_obligation(
    obligation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    obligation = await _get_authorized_obligation(
        obligation_id, current_user, org_id, db
    )
    await _transition(
        obligation,
        obligation_lifecycle.confirm_obligation(db, obligation, current_user.id),
    )
    await db.commit()
    await db.refresh(obligation)
    return _detail(obligation)


@router.post("/{obligation_id}/assignees", response_model=ObligationDetail, status_code=201)
async def assign_obligation(
    obligation_id: uuid.UUID,
    data: AssigneeCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    obligation = await _get_authorized_obligation(
        obligation_id, current_user, org_id, db
    )
    if data.agreement_party_id is not None:
        party = (
            await db.execute(
                select(AgreementParty).where(
                    AgreementParty.id == data.agreement_party_id,
                    AgreementParty.agreement_id == obligation.agreement_id,
                )
            )
        ).scalar_one_or_none()
        if party is None:
            raise HTTPException(
                status_code=400, detail="Party does not belong to agreement."
            )
    if data.member_id is not None:
        member = (
            await db.execute(
                select(OrganizationMember).where(
                    OrganizationMember.user_id == data.member_id,
                    OrganizationMember.organization_id == org_id,
                    OrganizationMember.status == "active",
                )
            )
        ).scalar_one_or_none()
        if member is None:
            raise HTTPException(
                status_code=400, detail="Member does not belong to organization."
            )
    await _transition(
        obligation,
        obligation_lifecycle.assign_obligation(
            db,
            obligation,
            current_user.id,
            agreement_party_id=data.agreement_party_id,
            member_id=data.member_id,
            responsibility_type=data.responsibility_type,
            primary_assignee=data.primary_assignee,
        ),
    )
    await db.commit()
    await db.refresh(obligation)
    return _detail(obligation)


@router.delete("/{obligation_id}/assignees/{assignee_id}", status_code=204)
async def remove_assignee(
    obligation_id: uuid.UUID,
    assignee_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    await _get_authorized_obligation(obligation_id, current_user, org_id, db)
    assignee = (
        await db.execute(
            select(ObligationAssignee).where(
                ObligationAssignee.id == assignee_id,
                ObligationAssignee.obligation_id == obligation_id,
            )
        )
    ).scalar_one_or_none()
    if assignee is None:
        raise HTTPException(status_code=404, detail="Assignee not found")
    await db.delete(assignee)
    await db.commit()


@router.post("/{obligation_id}/start", response_model=ObligationDetail)
async def start_obligation(
    obligation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    obligation = await _get_authorized_obligation(
        obligation_id, current_user, org_id, db
    )
    await _transition(
        obligation,
        obligation_lifecycle.start_obligation(db, obligation, current_user.id),
    )
    await db.commit()
    await db.refresh(obligation)
    return _detail(obligation)


@router.post("/{obligation_id}/complete", response_model=ObligationDetail)
async def complete_obligation(
    obligation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    obligation = await _get_authorized_obligation(
        obligation_id, current_user, org_id, db
    )
    await _transition(
        obligation,
        obligation_lifecycle.complete_obligation(db, obligation, current_user.id),
        error_code=422 if obligation.evidence_status == "REQUIRED" else 409,
    )
    await db.commit()
    await db.refresh(obligation)
    return _detail(obligation)


@router.post("/{obligation_id}/waive", response_model=ObligationDetail)
async def waive_obligation(
    obligation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    obligation = await _get_authorized_obligation(
        obligation_id, current_user, org_id, db
    )
    await _transition(
        obligation,
        obligation_lifecycle.waive_obligation(db, obligation, current_user.id),
    )
    await db.commit()
    await db.refresh(obligation)
    return _detail(obligation)


@router.post("/{obligation_id}/cancel", response_model=ObligationDetail)
async def cancel_obligation(
    obligation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    obligation = await _get_authorized_obligation(
        obligation_id, current_user, org_id, db
    )
    await _transition(
        obligation,
        obligation_lifecycle.cancel_obligation(db, obligation, current_user.id),
    )
    await db.commit()
    await db.refresh(obligation)
    return _detail(obligation)


@router.post("/{obligation_id}/deadlines", response_model=ObligationDetail, status_code=201)
async def create_deadline(
    obligation_id: uuid.UUID,
    data: DeadlineCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    obligation = await _get_authorized_obligation(
        obligation_id, current_user, org_id, db
    )
    await _transition(
        obligation,
        obligation_lifecycle.create_deadline(
            db,
            obligation,
            data.deadline_type,
            due_at=data.due_at,
            calculation_rule=data.calculation_rule,
            source_date=data.source_date,
            source_event=data.source_event,
            timezone_name=data.timezone,
        ),
    )
    await db.commit()
    await db.refresh(obligation)
    return _detail(obligation)


@router.get("/{obligation_id}/deadlines", response_model=list[DeadlineResponse])
async def list_deadlines(
    obligation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    obligation = await _get_authorized_obligation(
        obligation_id, current_user, org_id, db
    )
    return obligation.deadlines


@router.post("/{obligation_id}/evidence", response_model=ObligationDetail, status_code=201)
async def submit_obligation_evidence(
    obligation_id: uuid.UUID,
    data: EvidenceCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    obligation = await _get_authorized_obligation(
        obligation_id, current_user, org_id, db
    )
    await _transition(
        obligation,
        obligation_lifecycle.submit_evidence(
            db,
            obligation,
            current_user.id,
            data.document_id,
            data.description,
        ),
    )
    await db.commit()
    await db.refresh(obligation)
    return _detail(obligation)


@router.get("/{obligation_id}/evidence", response_model=list[EvidenceResponse])
async def list_obligation_evidence(
    obligation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    obligation = await _get_authorized_obligation(
        obligation_id, current_user, org_id, db
    )
    return obligation.evidence_items


@router.post("/{obligation_id}/evidence/{evidence_id}/review", response_model=ObligationDetail)
async def review_obligation_evidence(
    obligation_id: uuid.UUID,
    evidence_id: uuid.UUID,
    data: EvidenceReview,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    obligation = await _get_authorized_obligation(
        obligation_id, current_user, org_id, db
    )
    evidence = (
        await db.execute(
            select(ObligationEvidence).where(
                ObligationEvidence.id == evidence_id,
                ObligationEvidence.obligation_id == obligation_id,
            )
        )
    ).scalar_one_or_none()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    document_ok = True
    if evidence.document_id is not None:
        from app.models.document import Document

        document = await db.get(Document, evidence.document_id)
        if document is None or document.agreement_id != obligation.agreement_id:
            document_ok = False
        else:
            from app.services.document_repository import verify_document_integrity

            integrity = await verify_document_integrity(document)
            if not integrity["match"]:
                document_ok = False
        if not document_ok:
            data.approve = False
            data.comment = data.comment or "Evidence document integrity check failed."
    await _transition(
        obligation,
        obligation_lifecycle.review_evidence(
            db,
            evidence,
            obligation,
            current_user.id,
            approve=data.approve,
            comment=data.comment,
            document_ok=document_ok,
        ),
    )
    await db.commit()
    await db.refresh(obligation)
    return _detail(obligation)


@router.get("/{obligation_id}/events", response_model=list[EventResponse])
async def list_obligation_events(
    obligation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    obligation = await _get_authorized_obligation(
        obligation_id, current_user, org_id, db
    )
    return obligation.events