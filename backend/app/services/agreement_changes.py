"""Agreement changes service.

Manages the lifecycle of change proposals:
- Create change with clause-level items
- Accept / reject / counter-propose
- Server validates old_content against immutable base version
"""

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agreement import Agreement, AgreementVersion
from app.models.negotiation import (
    AgreementChange,
    AgreementChangeItem,
    NegotiationRound,
)


async def create_change(
    db: AsyncSession,
    agreement: Agreement,
    base_version_id: uuid.UUID,
    proposed_by: uuid.UUID,
    change_type: str,
    explanation: str | None,
    items: list[dict],
    proposed_version_id: uuid.UUID | None = None,
    proposing_party_id: uuid.UUID | None = None,
) -> AgreementChange:
    """Create a new change proposal with clause-level items.

    Args:
        db: Database session.
        agreement: The agreement being changed.
        base_version_id: The version this change is based on.
        proposed_by: User ID proposing the change.
        change_type: Type of change ('amendment', 'redline', 'counter', 'correction').
        explanation: Overall explanation for the change.
        items: List of clause modifications.
        proposed_version_id: Optional pre-created version with changes applied.
        proposing_party_id: Optional party on whose behalf the change is proposed.

    Returns:
        The newly created AgreementChange.
    """
    change = AgreementChange(
        agreement_id=agreement.id,
        base_version_id=base_version_id,
        proposed_version_id=proposed_version_id,
        proposed_by=proposed_by,
        proposing_party_id=proposing_party_id,
        change_type=change_type,
        explanation=explanation,
        status="proposed",
    )
    db.add(change)
    await db.flush()

    for item in items:
        change_item = AgreementChangeItem(
            change_id=change.id,
            clause_identifier=item["clause_identifier"],
            change_type=item["change_type"],
            old_content=item.get("old_content"),
            new_content=item.get("new_content"),
            reason=item.get("reason"),
            status="proposed",
        )
        db.add(change_item)

    await db.flush()

    return change


async def accept_change(
    db: AsyncSession,
    change: AgreementChange,
) -> AgreementChange:
    """Accept a change proposal.

    Marks all items as accepted and the change as accepted.
    """
    change.status = "accepted"

    for item in change.items:
        item.status = "accepted"

    await db.flush()
    return change


async def reject_change(
    db: AsyncSession,
    change: AgreementChange,
) -> AgreementChange:
    """Reject a change proposal.

    Marks all items as rejected and the change as rejected.
    """
    change.status = "rejected"

    for item in change.items:
        item.status = "rejected"

    await db.flush()
    return change


async def supersede_change(
    db: AsyncSession,
    change: AgreementChange,
) -> AgreementChange:
    """Mark a change as superseded by a newer proposal."""
    change.status = "superseded"
    await db.flush()
    return change


async def confirm_change(
    db: AsyncSession,
    change: AgreementChange,
) -> AgreementChange:
    """Client confirmation of a proposal by the proposing party.

    ``proposed`` -> ``client_confirmed``. The change is still NOT visible to
    the opposing side until it is released.
    """
    if change.status != "proposed":
        raise ValueError(f"Cannot confirm a change with status: {change.status}")
    change.status = "client_confirmed"
    await db.flush()
    return change


async def release_change(
    db: AsyncSession,
    change: AgreementChange,
) -> AgreementChange:
    """Release a client-confirmed proposal to the opposing side.

    ``client_confirmed`` -> ``released``. After this point the opposing
    party may view, accept, reject, or counter the proposal.
    """
    if change.status != "client_confirmed":
        raise ValueError(f"Cannot release a change with status: {change.status}")
    change.status = "released"
    await db.flush()
    return change


async def counter_propose(
    db: AsyncSession,
    original_change: AgreementChange,
    *,
    proposed_by: uuid.UUID,
    proposed_content: str,
    explanation: str | None,
    items: list[dict],
    proposing_party_id: uuid.UUID | None = None,
) -> AgreementChange:
    """Create a counterproposal that supersedes the original change.

    The counter is a new AgreementChange record with
    ``change_type="counter"`` that uses the same base version but carries
    the counter-proposer's own proposed content (already built by the
    caller).  The original change is marked ``superseded``.

    Returns the newly created counter change.
    """
    await supersede_change(db, original_change)

    # Build a new version from the same base containing the counter text.
    from app.models.agreement import AgreementVersion

    next_number = (
        await db.execute(
            select(AgreementVersion.version_number)
            .where(AgreementVersion.agreement_id == original_change.agreement_id)
            .order_by(AgreementVersion.version_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    next_number = 1 if next_number is None else next_number + 1

    new_version = AgreementVersion(
        agreement_id=original_change.agreement_id,
        version_number=next_number,
        content=proposed_content,
        content_hash=hashlib.sha256(proposed_content.encode()).hexdigest(),
        status="proposed",
        created_by=proposed_by,
    )
    db.add(new_version)
    await db.flush()

    counter = AgreementChange(
        agreement_id=original_change.agreement_id,
        base_version_id=original_change.base_version_id,
        proposed_by=proposed_by,
        proposing_party_id=proposing_party_id,
        change_type="counter",
        explanation=explanation,
        status="proposed",
        proposed_version_id=new_version.id,
    )
    db.add(counter)
    await db.flush()

    for item_data in items:
        item = AgreementChangeItem(
            change_id=counter.id,
            clause_identifier=item_data["clause_identifier"],
            change_type=item_data["change_type"],
            old_content=item_data.get("old_content"),
            new_content=item_data.get("new_content"),
            reason=item_data.get("reason"),
            status="proposed",
        )
        db.add(item)
    await db.flush()
    return counter


async def get_change(
    db: AsyncSession,
    change_id: uuid.UUID,
) -> AgreementChange | None:
    """Get a change by ID (items eager-loaded for async safety)."""
    result = await db.execute(
        select(AgreementChange)
        .options(selectinload(AgreementChange.items))
        .where(
            AgreementChange.id == change_id,
        )
    )
    return result.scalar_one_or_none()


async def list_changes_for_agreement(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    status: str | None = None,
    visible_party_id: uuid.UUID | None = None,
) -> list[AgreementChange]:
    """List changes for an agreement, optionally filtered by status.

    Party isolation: when ``visible_party_id`` is given, changes proposed by
    ANOTHER party that have not yet been released are hidden (spec 1.12
    "Opposing side receives only released changes"). Resolved changes and
    changes without a recorded proposing party are always visible.
    """
    query = select(AgreementChange).where(
        AgreementChange.agreement_id == agreement_id,
    )

    if status:
        query = query.where(AgreementChange.status == status)

    if visible_party_id is not None:
        unreleased = select(AgreementChange.id).where(
            AgreementChange.agreement_id == agreement_id,
            AgreementChange.proposing_party_id.is_not(None),
            AgreementChange.proposing_party_id != visible_party_id,
            AgreementChange.status.in_(
                ["proposed", "client_confirmed"]
            ),
        )
        query = query.where(AgreementChange.id.not_in(unreleased))

    query = query.order_by(AgreementChange.created_at.desc())

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_latest_proposed_change(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> AgreementChange | None:
    """Get the most recent proposed change for an agreement."""
    result = await db.execute(
        select(AgreementChange)
        .where(
            AgreementChange.agreement_id == agreement_id,
            AgreementChange.status == "proposed",
        )
        .order_by(AgreementChange.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_negotiation_round(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    initiated_by: uuid.UUID,
) -> NegotiationRound:
    """Create a new negotiation round."""
    # Get current round number
    result = await db.execute(
        select(NegotiationRound.round_number)
        .where(NegotiationRound.agreement_id == agreement_id)
        .order_by(NegotiationRound.round_number.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()

    next_round = 1 if latest is None else latest + 1

    round = NegotiationRound(
        agreement_id=agreement_id,
        round_number=next_round,
        initiated_by=initiated_by,
        status="open",
    )
    db.add(round)
    await db.flush()

    return round


async def get_current_round(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> NegotiationRound | None:
    """Get the current open negotiation round."""
    result = await db.execute(
        select(NegotiationRound)
        .where(
            NegotiationRound.agreement_id == agreement_id,
            NegotiationRound.status.in_(["open", "awaiting_response"]),
        )
        .order_by(NegotiationRound.round_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
