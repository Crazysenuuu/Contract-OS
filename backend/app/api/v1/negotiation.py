"""Negotiation API endpoints.

Manage change proposals, redlines, and negotiation rounds.
"""

import re
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.agreement_access import verify_agreement_access
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement, AgreementVersion
from app.models.negotiation import (
    AgreementChange,
    AgreementChangeItem,
    NegotiationComment,
    NegotiationRound,
)
from app.models.user import User
from app.services.agreement_changes import (
    accept_change,
    confirm_change,
    counter_propose,
    create_change,
    create_negotiation_round,
    get_change,
    get_current_round,
    list_changes_for_agreement,
    reject_change,
    release_change,
    supersede_change,
)
from app.services.agreement_versioning import (
    create_version,
    get_latest_version,
    list_versions,
    promote_version,
)
from app.services.diff_engine import diff_versions, generate_redline_view

router = APIRouter(
    prefix="/agreements",
    tags=["negotiation"],
)


async def _resolve_user_party(
    db: AsyncSession,
    agreement_id: UUID,
    user_id: UUID,
    org_id: UUID,
) -> UUID | None:
    """Resolve the party a user represents on an agreement.

    Priority: explicit AgreementParticipant binding (participant party),
    then the organization's own party when the user is the agreement
    creator. Returns None when no party can be determined.
    """
    from app.models.agreement_access import AgreementParticipant
    from app.models.legal_entity import LegalEntity

    participant = (
        await db.execute(
            select(AgreementParticipant).where(
                AgreementParticipant.agreement_id == agreement_id,
                AgreementParticipant.user_id == user_id,
                AgreementParticipant.status == "active",
            )
        )
    ).scalars().first()
    if participant is not None:
        return participant.agreement_party_id

    agreement = (
        await db.execute(select(Agreement).where(Agreement.id == agreement_id))
    ).scalars().first()
    if agreement is not None and agreement.created_by == user_id:
        from app.models.agreement_access import AgreementParty

        org_party = (
            await db.execute(
                select(AgreementParty.id)
                .join(LegalEntity, LegalEntity.id == AgreementParty.legal_entity_id)
                .where(
                    AgreementParty.agreement_id == agreement_id,
                    LegalEntity.organization_id == org_id,
                )
            )
        ).scalars().first()
        return org_party
    return None


async def _ensure_opposing_party(
    db: AsyncSession,
    change,
    agreement_id: UUID,
    user_id: UUID,
    org_id: UUID,
) -> None:
    """Block a party from resolving its own change proposal.

    An accepting/rejecting/countering actor must be on the OPPOSING side of
    the proposal, not the side that made it (spec 1.5/1.12 party isolation).
    When no proposing party is recorded (legacy agreements without parties)
    the check is skipped so existing flows keep working.
    """
    if change.proposing_party_id is None:
        return
    actor_party_id = await _resolve_user_party(db, agreement_id, user_id, org_id)
    if actor_party_id is not None and actor_party_id == change.proposing_party_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A party cannot accept, reject, or counter its own proposal",
        )
    # The opposing side may only act on proposals released to it.
    if change.status not in ("released", "accepted", "rejected", "superseded"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Proposal has not yet been released to your side",
        )


async def _ensure_same_party_actor(
    db: AsyncSession,
    change,
    agreement_id: UUID,
    user_id: UUID,
    org_id: UUID,
    action: str,
) -> None:
    """Require the actor to represent the side that made the proposal.

    Used by the internal confirm/release actions: only the proposing party's
    own users may confirm their client or release the proposal.
    """
    if change.proposing_party_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No proposing party recorded for this proposal",
        )
    actor_party_id = await _resolve_user_party(db, agreement_id, user_id, org_id)
    if actor_party_id is None or actor_party_id != change.proposing_party_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only the proposing party may {action} this proposal",
        )


# --- Schemas ---


def _extract_previous_content(base_content: str, clause_identifier: str) -> str:
    """Return the previously-proposed content for a clause, if any.

    Negotiated changes are appended as bracketed markers on the version
    text; a fresh proposal over a clause that was already modified can
    retrieve the prior wording so the diff shows old -> new accurately.
    """
    pattern = (
        rf"\[(?:MODIFIED|REPLACED|ADDED): {re.escape(clause_identifier)}\]"
        rf"\s*(.*?)(?=\n\n\[|\Z)"
    )
    match = re.search(pattern, base_content, flags=re.DOTALL)
    if match is None:
        return ""
    return match.group(1).strip()


def _apply_modifications(
    base_content: str,
    modifications: list[ClauseModification],
) -> str:
    """Apply clause modifications to produce the proposed version text.

    Modifying an already-modified clause replaces the previous marker block
    in place rather than appending a second marker, keeping the redline
    readable across successive proposals.
    """
    proposed = base_content
    for mod in modifications:
        new_block = f"\n\n[{mod.change_type.upper()}: {mod.clause_identifier}] {mod.new_content}"
        marker = (
            rf"\[(?:MODIFIED|REPLACED|ADDED): {re.escape(mod.clause_identifier)}\]"
            rf"\s*(.*?)(?=\n\n\[|\Z)"
        )
        if mod.change_type == "remove":
            proposed = re.sub(
                marker,
                f"\n\n[REMOVED: {mod.clause_identifier}]",
                proposed,
                flags=re.DOTALL,
            )
        elif re.search(marker, proposed, flags=re.DOTALL):
            proposed = re.sub(
                marker,
                new_block.strip(),
                proposed,
                count=1,
                flags=re.DOTALL,
            )
        else:
            proposed += new_block
    return proposed


class ClauseModification(BaseModel):
    clause_identifier: str
    change_type: str  # 'add', 'remove', 'modify', 'replace'
    new_content: str
    reason: str | None = None


class ChangeProposalCreate(BaseModel):
    change_type: str  # 'amendment', 'redline', 'counter', 'correction'
    explanation: str | None = None
    modifications: list[ClauseModification]
    base_version_id: UUID | None = None  # optimistic-concurrency anchor


class ChangeProposalResponse(BaseModel):
    id: UUID
    agreement_id: UUID
    base_version_id: UUID
    proposed_version_id: UUID | None
    proposed_by: UUID
    proposing_party_id: UUID | None
    change_type: str
    explanation: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ChangeItemResponse(BaseModel):
    id: UUID
    change_id: UUID
    clause_identifier: str
    change_type: str
    old_content: str | None
    new_content: str | None
    reason: str | None
    status: str

    model_config = {"from_attributes": True}


class NegotiationRoundResponse(BaseModel):
    id: UUID
    agreement_id: UUID
    round_number: int
    initiated_by: UUID
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class NegotiationCommentCreate(BaseModel):
    content: str
    clause_identifier: str | None = None
    agreement_party_id: UUID


class NegotiationCommentResponse(BaseModel):
    id: UUID
    round_id: UUID
    agreement_party_id: UUID
    author_id: UUID
    clause_identifier: str | None
    content: str
    status: str
    created_at: str

    model_config = {"from_attributes": True}


class DiffResponse(BaseModel):
    base_version: int
    compared_version: int
    summary: dict
    clauses: list[dict]


class RedlineViewResponse(BaseModel):
    base_version: int
    compared_version: int
    redline_html: str


# --- Change Proposal Endpoints ---


@router.post(
    "/{agreement_id}/changes",
    response_model=ChangeProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def propose_change(
    agreement_id: UUID,
    data: ChangeProposalCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Propose a change to an agreement.

    Creates a new version with the modifications applied.
    Server validates old_content against the immutable base version.
    """
    # Verify access with propose permission
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        permission="agreement.propose_change",
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    # Get base version (latest)
    base_version = await get_latest_version(db, agreement_id)
    if base_version is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No versions exist for this agreement",
        )

    # Optimistic concurrency: if the client claims a base version, it must
    # still be the current one, or the proposal is based on a stale version.
    if data.base_version_id is not None and data.base_version_id != base_version.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stale base version: the agreement has changed since you started editing",
        )

    if base_version.status == "locked":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot modify a locked version",
        )

    # Create new version content by applying modifications clause-by-clause.
    proposed_content = _apply_modifications(
        base_version.content,
        data.modifications,
    )

    # Create the proposed version
    proposed_version = await create_version(
        db=db,
        agreement=agreement,
        content=proposed_content,
        created_by=current_user.id,
        status="proposed",
    )

    # Create the change record
    change = await create_change(
        db=db,
        agreement=agreement,
        base_version_id=base_version.id,
        proposed_by=current_user.id,
        proposing_party_id=await _resolve_user_party(
            db, agreement_id, current_user.id, org_id
        ),
        change_type=data.change_type,
        explanation=data.explanation,
        proposed_version_id=proposed_version.id,
        items=[
            {
                "clause_identifier": mod.clause_identifier,
                "change_type": mod.change_type,
                "old_content": _extract_previous_content(
                    base_version.content,
                    mod.clause_identifier,
                ),
                "new_content": mod.new_content,
                "reason": mod.reason,
            }
            for mod in data.modifications
        ],
    )

    # Idempotently emit the change-set created event in this transaction.
    from app.services.event_service import EventService

    await EventService.publish(
        db,
        event_type="agreement.change_set.created",
        aggregate_type="agreement",
        aggregate_id=agreement_id,
        organization_id=agreement.organization_id,
        actor_user_id=current_user.id,
        payload={
            "change_id": str(change.id),
            "agreement_id": str(agreement_id),
            "base_version_id": str(base_version.id),
            "change_count": len(data.modifications),
        },
        dedup_key=f"proposal:{change.id}",
    )

    return change


@router.get(
    "/{agreement_id}/changes",
    response_model=list[ChangeProposalResponse],
)
async def list_changes(
    agreement_id: UUID,
    change_status: str | None = None,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List all change proposals for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    user_party_id = await _resolve_user_party(db, agreement_id, current_user.id, org_id)
    return await list_changes_for_agreement(
        db, agreement_id, change_status, visible_party_id=user_party_id
    )


@router.get(
    "/{agreement_id}/changes/{change_id}",
    response_model=ChangeProposalResponse,
)
async def get_change_details(
    agreement_id: UUID,
    change_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get details of a specific change proposal."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    change = await get_change(db, change_id)
    if change is None or change.agreement_id != agreement_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change not found",
        )

    return change


@router.get(
    "/{agreement_id}/changes/{change_id}/items",
    response_model=list[ChangeItemResponse],
)
async def list_change_items(
    agreement_id: UUID,
    change_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List all clause modifications in a change proposal."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    change = await get_change(db, change_id)
    if change is None or change.agreement_id != agreement_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change not found",
        )

    return change.items


# --- Change Actions ---


@router.post(
    "/{agreement_id}/changes/{change_id}/confirm",
    response_model=ChangeProposalResponse,
)
async def confirm_proposal(
    agreement_id: UUID,
    change_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Client confirmation of a proposal by the proposing party (spec 1.12).

    Transitions ``proposed`` -> ``client_confirmed``. The change remains
    invisible to the opposing side until it is released. Only users on the
    proposing party may confirm.
    """
    await verify_agreement_access(
        agreement_id=agreement_id,
        permission="agreement.propose_change",
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    change = await get_change(db, change_id)
    if change is None or change.agreement_id != agreement_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change not found",
        )

    await _ensure_same_party_actor(
        db, change, agreement_id, current_user.id, org_id, action="confirm"
    )

    try:
        return await confirm_change(db, change)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{agreement_id}/changes/{change_id}/release",
    response_model=ChangeProposalResponse,
)
async def release_proposal(
    agreement_id: UUID,
    change_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Release a client-confirmed proposal to the opposing side (spec 1.12).

    Transitions ``client_confirmed`` -> ``released``. After release the
    opposing party may view, accept, reject, or counter the proposal. Only
    users on the proposing party may release.
    """
    await verify_agreement_access(
        agreement_id=agreement_id,
        permission="agreement.propose_change",
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    change = await get_change(db, change_id)
    if change is None or change.agreement_id != agreement_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change not found",
        )

    await _ensure_same_party_actor(
        db, change, agreement_id, current_user.id, org_id, action="release"
    )

    try:
        return await release_change(db, change)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{agreement_id}/changes/{change_id}/accept",
    response_model=ChangeProposalResponse,
)
async def accept_proposal(
    agreement_id: UUID,
    change_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Accept a change proposal."""
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        permission="agreement.approve",
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    change = await get_change(db, change_id)
    if change is None or change.agreement_id != agreement_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change not found",
        )

    if change.status not in ("proposed", "client_confirmed", "released"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot accept a change with status: {change.status}",
        )

    await _ensure_opposing_party(db, change, agreement_id, current_user.id, org_id)

    await accept_change(db, change)

    # Promote the proposed version so the accepted terms actually take
    # effect: it becomes the current working version that gets signed.
    # The base version is left untouched as evidence. The content is NOT
    # re-rendered here because the renderer regenerates the whole document
    # from template variables and would discard the negotiated text.
    if change.proposed_version_id is not None:
        result = await db.execute(
            select(AgreementVersion).where(
                AgreementVersion.id == change.proposed_version_id,
                AgreementVersion.agreement_id == agreement_id,
            )
        )
        proposed_version = result.scalar_one_or_none()
        if proposed_version is not None and proposed_version.status != "locked":
            await promote_version(db, agreement, proposed_version)

    return change


@router.post(
    "/{agreement_id}/changes/{change_id}/reject",
    response_model=ChangeProposalResponse,
)
async def reject_proposal(
    agreement_id: UUID,
    change_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Reject a change proposal."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        permission="agreement.approve",
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    change = await get_change(db, change_id)
    if change is None or change.agreement_id != agreement_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change not found",
        )

    if change.status not in ("proposed", "client_confirmed", "released"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot reject a change with status: {change.status}",
        )

    await _ensure_opposing_party(db, change, agreement_id, current_user.id, org_id)

    return await reject_change(db, change)


@router.post(
    "/{agreement_id}/changes/{change_id}/counter",
    response_model=ChangeProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def counter_proposal(
    agreement_id: UUID,
    change_id: UUID,
    data: ChangeProposalCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Counter a change proposal (spec 2.05.16, POST .../counter).

    Takes the latest version of the agreement, applies the countering
    party's modifications, creates a new proposed version, and supersedes
    the original change so only one open proposal chain remains per side.
    """
    agreement = await verify_agreement_access(
        agreement_id=agreement_id,
        permission="agreement.propose_change",
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    change = await get_change(db, change_id)
    if change is None or change.agreement_id != agreement_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change not found",
        )

    if change.status not in ("proposed", "client_confirmed", "released"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot counter a change with status: {change.status}",
        )

    await _ensure_opposing_party(db, change, agreement_id, current_user.id, org_id)

    # Always base the counter on the latest open proposed content so the
    # whole history (base → proposal → counter) is preserved.
    base_version = (
        await db.execute(
            select(AgreementVersion).where(
                AgreementVersion.id == change.base_version_id,
            )
        )
    ).scalar_one_or_none()
    if base_version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Base version not found",
        )
    base_content = base_version.content
    if change.proposed_version_id is not None:
        pv = (
            await db.execute(
                select(AgreementVersion).where(
                    AgreementVersion.id == change.proposed_version_id,
                )
            )
        ).scalar_one_or_none()
        if pv is not None and pv.status in ("proposed", "negotiating"):
            base_content = pv.content
    proposed_content = _apply_modifications(base_content, data.modifications)

    counter = await counter_propose(
        db,
        change,
        proposed_by=current_user.id,
        proposing_party_id=await _resolve_user_party(
            db, agreement_id, current_user.id, org_id
        ),
        proposed_content=proposed_content,
        explanation=data.explanation,
        items=[
            {
                "clause_identifier": mod.clause_identifier,
                "change_type": mod.change_type,
                "old_content": _extract_previous_content(base_content, mod.clause_identifier),
                "new_content": mod.new_content,
                "reason": mod.reason,
            }
            for mod in data.modifications
        ],
    )

    await db.flush()

    from app.services.event_service import EventService

    await EventService.publish(
        db,
        event_type="agreement.change_set.countered",
        aggregate_type="agreement",
        aggregate_id=agreement_id,
        organization_id=agreement.organization_id,
        actor_user_id=current_user.id,
        payload={
            "change_id": str(counter.id),
            "counter_of": str(change_id),
            "agreement_id": str(agreement_id),
        },
        dedup_key=f"counter:{counter.id}",
    )

    return counter


# --- Diff & Redline Endpoints ---


@router.get(
    "/{agreement_id}/diff",
    response_model=DiffResponse,
)
async def compare_versions(
    agreement_id: UUID,
    base_version: int,
    compared_version: int,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Compare two versions and return clause-level diff."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    versions = await list_versions(db, agreement_id)
    version_map = {v.version_number: v for v in versions}

    if base_version not in version_map:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Base version {base_version} not found",
        )

    if compared_version not in version_map:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Compared version {compared_version} not found",
        )

    diff = diff_versions(
        base_text=version_map[base_version].content,
        compared_text=version_map[compared_version].content,
        base_version=base_version,
        compared_version=compared_version,
    )

    return DiffResponse(
        base_version=diff.base_version,
        compared_version=diff.compared_version,
        summary=diff.summary,
        clauses=[
            {
                "clause_identifier": c.clause_identifier,
                "old_content": c.old_content,
                "new_content": c.new_content,
                "change_type": c.change_type,
            }
            for c in diff.clauses
        ],
    )


@router.get(
    "/{agreement_id}/redline",
    response_model=RedlineViewResponse,
)
async def get_redline_view(
    agreement_id: UUID,
    base_version: int,
    compared_version: int,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Get HTML redline view showing additions and deletions."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    versions = await list_versions(db, agreement_id)
    version_map = {v.version_number: v for v in versions}

    if base_version not in version_map:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Base version {base_version} not found",
        )

    if compared_version not in version_map:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Compared version {compared_version} not found",
        )

    redline_html = generate_redline_view(
        base_text=version_map[base_version].content,
        proposed_text=version_map[compared_version].content,
    )

    return RedlineViewResponse(
        base_version=base_version,
        compared_version=compared_version,
        redline_html=redline_html,
    )


# --- Negotiation Round Endpoints ---


@router.post(
    "/{agreement_id}/negotiation/rounds",
    response_model=NegotiationRoundResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_negotiation_round(
    agreement_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Start a new negotiation round."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    # Check for existing open round
    existing_round = await get_current_round(db, agreement_id)
    if existing_round:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A negotiation round is already open",
        )

    return await create_negotiation_round(db, agreement_id, current_user.id)


@router.get(
    "/{agreement_id}/negotiation/rounds",
    response_model=list[NegotiationRoundResponse],
)
async def list_negotiation_rounds(
    agreement_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List all negotiation rounds for an agreement."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    result = await db.execute(
        select(NegotiationRound)
        .where(NegotiationRound.agreement_id == agreement_id)
        .order_by(NegotiationRound.round_number)
    )
    return list(result.scalars().all())


@router.post(
    "/{agreement_id}/negotiation/rounds/{round_id}/comments",
    response_model=NegotiationCommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_comment(
    agreement_id: UUID,
    round_id: UUID,
    data: NegotiationCommentCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Add a comment to a negotiation round."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        permission="agreement.comment",
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    # Verify round exists
    result = await db.execute(
        select(NegotiationRound).where(
            NegotiationRound.id == round_id,
            NegotiationRound.agreement_id == agreement_id,
        )
    )
    round = result.scalar_one_or_none()
    if round is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Negotiation round not found",
        )

    comment = NegotiationComment(
        round_id=round_id,
        agreement_party_id=data.agreement_party_id,
        author_id=current_user.id,
        clause_identifier=data.clause_identifier,
        content=data.content,
    )
    db.add(comment)
    await db.flush()
    await db.refresh(comment)

    return comment


@router.get(
    "/{agreement_id}/negotiation/rounds/{round_id}/comments",
    response_model=list[NegotiationCommentResponse],
)
async def list_comments(
    agreement_id: UUID,
    round_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List all comments in a negotiation round."""
    await verify_agreement_access(
        agreement_id=agreement_id,
        current_user=current_user,
        org_id=org_id,
        db=db,
    )

    result = await db.execute(
        select(NegotiationComment)
        .where(
            NegotiationComment.round_id == round_id,
        )
        .order_by(NegotiationComment.created_at)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Change-set lifecycle aliases (spec 2.05 — /change-sets URL surface).
# The same handlers are reused; the path segment name is invisible to the
# client, so the URLs match the spec exactly.
# ---------------------------------------------------------------------------

change_sets_router = APIRouter(
    prefix="/agreements",
    tags=["Change Sets"],
)

change_sets_router.add_api_route(
    "/{agreement_id}/change-sets",
    propose_change,
    methods=["POST"],
    response_model=ChangeProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
change_sets_router.add_api_route(
    "/{agreement_id}/change-sets",
    list_changes,
    methods=["GET"],
    response_model=list[ChangeProposalResponse],
)
change_sets_router.add_api_route(
    "/{agreement_id}/change-sets/{change_id}/accept",
    accept_proposal,
    methods=["POST"],
    response_model=ChangeProposalResponse,
)
change_sets_router.add_api_route(
    "/{agreement_id}/change-sets/{change_id}/reject",
    reject_proposal,
    methods=["POST"],
    response_model=ChangeProposalResponse,
)
change_sets_router.add_api_route(
    "/{agreement_id}/change-sets/{change_id}/counter",
    counter_proposal,
    methods=["POST"],
    response_model=ChangeProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
