"""Agreement versioning service.

Every new proposal or change creates a new version.
Versions are immutable — once created, content never changes.
"""

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import (
    Agreement,
    AgreementVersion,
)


def calculate_hash(content: str) -> str:
    """Calculate SHA-256 hash of content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def get_latest_version(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> AgreementVersion | None:
    """Get the latest version of an agreement."""
    result = await db.execute(
        select(AgreementVersion)
        .where(AgreementVersion.agreement_id == agreement_id)
        .order_by(AgreementVersion.version_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_version_by_number(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    version_number: int,
) -> AgreementVersion | None:
    """Get a specific version by number."""
    result = await db.execute(
        select(AgreementVersion).where(
            AgreementVersion.agreement_id == agreement_id,
            AgreementVersion.version_number == version_number,
        )
    )
    return result.scalar_one_or_none()


async def create_version(
    db: AsyncSession,
    agreement: Agreement,
    content: str,
    created_by: uuid.UUID,
    status: str = "draft",
) -> AgreementVersion:
    """Create a new immutable version for an agreement.

    Args:
        db: Database session.
        agreement: The agreement to create a version for.
        content: The full text content of this version.
        created_by: User ID creating the version.
        status: Version status (default: 'draft').

    Returns:
        The newly created AgreementVersion.
    """
    # Get the latest version number
    result = await db.execute(
        select(AgreementVersion.version_number)
        .where(AgreementVersion.agreement_id == agreement.id)
        .order_by(AgreementVersion.version_number.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()

    next_version = 1 if latest is None else latest + 1

    version = AgreementVersion(
        agreement_id=agreement.id,
        version_number=next_version,
        content=content,
        content_hash=calculate_hash(content),
        status=status,
        created_by=created_by,
    )

    db.add(version)
    await db.flush()

    return version


async def create_version_from_base(
    db: AsyncSession,
    agreement: Agreement,
    base_version: AgreementVersion,
    new_content: str,
    created_by: uuid.UUID,
    status: str = "draft",
) -> AgreementVersion:
    """Create a new version based on a base version.

    This ensures version lineage is tracked.
    """
    return await create_version(
        db=db,
        agreement=agreement,
        content=new_content,
        created_by=created_by,
        status=status,
    )


async def list_versions(
    db: AsyncSession,
    agreement_id: uuid.UUID,
) -> list[AgreementVersion]:
    """List all versions of an agreement in order."""
    result = await db.execute(
        select(AgreementVersion)
        .where(AgreementVersion.agreement_id == agreement_id)
        .order_by(AgreementVersion.version_number)
    )
    return list(result.scalars().all())


async def lock_version(
    db: AsyncSession,
    version: AgreementVersion,
) -> AgreementVersion:
    """Lock a version (e.g., after signing).

    Locked versions cannot be modified.
    """
    from datetime import date

    version.status = "locked"
    version.locked_at = date.today()
    await db.flush()
    return version


async def promote_version(
    db: AsyncSession,
    agreement: Agreement,
    version: AgreementVersion,
) -> AgreementVersion:
    """Promote a proposed/negotiated version to the current working version.

    Any previously-current version that has not been locked is superseded so
    version lineage is preserved and the promoted version becomes the one the
    renderer and signer operate on (it already carries the highest version
    number).
    """
    result = await db.execute(
        select(AgreementVersion).where(
            AgreementVersion.agreement_id == agreement.id,
            AgreementVersion.status == "current",
        )
    )
    for prev in result.scalars().all():
        if prev.id != version.id and prev.status != "locked":
            prev.status = "superseded"
    version.status = "current"
    await db.flush()

    # A new current version invalidates any pending approval that was bound
    # to the superseded terms (spec 1.2: approvals must not outlive the
    # version they reviewed).
    from app.services.approval_engine import cancel_approvals_for_agreement

    await cancel_approvals_for_agreement(db, agreement.id)
    return version
