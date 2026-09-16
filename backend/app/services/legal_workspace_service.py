"""Legal workspace service.

Manages private notes and comments that are only visible to the owning party.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.legal_workspace import (
    LegalPrivateComment,
    LegalPrivateNote,
)


async def create_private_note(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    agreement_party_id: uuid.UUID,
    author_id: uuid.UUID,
    content: str,
    note_type: str = "legal",
) -> LegalPrivateNote:
    """Create a private note visible only to the owning party.

    Args:
        db: Database session.
        agreement_id: The agreement.
        agreement_party_id: The party this note belongs to.
        author_id: User creating the note.
        content: Note content.
        note_type: Type of note ('legal', 'strategy', 'risk', 'internal').

    Returns:
        The created LegalPrivateNote.
    """
    note = LegalPrivateNote(
        agreement_id=agreement_id,
        agreement_party_id=agreement_party_id,
        author_id=author_id,
        content=content,
        note_type=note_type,
    )
    db.add(note)
    await db.flush()

    return note


async def get_private_notes(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    agreement_party_id: uuid.UUID,
) -> list[LegalPrivateNote]:
    """Get all private notes for a party.

    CRITICAL: Only returns notes belonging to the specified party.
    """
    result = await db.execute(
        select(LegalPrivateNote)
        .where(
            LegalPrivateNote.agreement_id == agreement_id,
            LegalPrivateNote.agreement_party_id == agreement_party_id,
        )
        .order_by(LegalPrivateNote.created_at)
    )
    return list(result.scalars().all())


async def create_private_comment(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    agreement_party_id: uuid.UUID,
    author_id: uuid.UUID,
    content: str,
    clause_identifier: str | None = None,
) -> LegalPrivateComment:
    """Create a clause-specific internal comment.

    Args:
        db: Database session.
        agreement_id: The agreement.
        agreement_party_id: The party this comment belongs to.
        author_id: User creating the comment.
        content: Comment content.
        clause_identifier: Optional clause to attach to.

    Returns:
        The created LegalPrivateComment.
    """
    comment = LegalPrivateComment(
        agreement_id=agreement_id,
        agreement_party_id=agreement_party_id,
        author_id=author_id,
        content=content,
        clause_identifier=clause_identifier,
    )
    db.add(comment)
    await db.flush()

    return comment


async def get_private_comments(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    agreement_party_id: uuid.UUID,
    clause_identifier: str | None = None,
) -> list[LegalPrivateComment]:
    """Get private comments for a party, optionally filtered by clause.

    CRITICAL: Only returns comments belonging to the specified party.
    """
    query = select(LegalPrivateComment).where(
        LegalPrivateComment.agreement_id == agreement_id,
        LegalPrivateComment.agreement_party_id == agreement_party_id,
    )

    if clause_identifier:
        query = query.where(
            LegalPrivateComment.clause_identifier == clause_identifier
        )

    query = query.order_by(LegalPrivateComment.created_at)

    result = await db.execute(query)
    return list(result.scalars().all())


async def delete_private_note(
    db: AsyncSession,
    note_id: uuid.UUID,
    agreement_party_id: uuid.UUID,
) -> bool:
    """Delete a private note (only if it belongs to the party).

    Returns:
        True if deleted, False if not found or wrong party.
    """
    result = await db.execute(
        select(LegalPrivateNote).where(
            LegalPrivateNote.id == note_id,
            LegalPrivateNote.agreement_party_id == agreement_party_id,
        )
    )
    note = result.scalar_one_or_none()

    if note is None:
        return False

    await db.delete(note)
    await db.flush()
    return True


async def delete_private_comment(
    db: AsyncSession,
    comment_id: uuid.UUID,
    agreement_party_id: uuid.UUID,
) -> bool:
    """Delete a private comment (only if it belongs to the party).

    Returns:
        True if deleted, False if not found or wrong party.
    """
    result = await db.execute(
        select(LegalPrivateComment).where(
            LegalPrivateComment.id == comment_id,
            LegalPrivateComment.agreement_party_id == agreement_party_id,
        )
    )
    comment = result.scalar_one_or_none()

    if comment is None:
        return False

    await db.delete(comment)
    await db.flush()
    return True
