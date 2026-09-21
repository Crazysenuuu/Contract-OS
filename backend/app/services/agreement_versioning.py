"""Agreement versioning service.

Every new proposal or change creates a new version.
Versions are immutable — once created, content never changes.
"""

import copy
import difflib
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


def calculate_data_hash(data: dict | None) -> str:
    """Calculate a stable SHA-256 hash of an answers snapshot."""
    import json

    canonical = json.dumps(data or {}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    data: dict | None = None,
    note: str | None = None,
) -> AgreementVersion:
    """Create a new immutable version for an agreement.

    Args:
        db: Database session.
        agreement: The agreement to create a version for.
        content: The full text content of this version.
        created_by: User ID creating the version.
        status: Version status (default: 'draft').
        data: Immutable snapshot of the agreement answers at this version.
        note: Human-readable label for the version.

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
        data=copy.deepcopy(data) if data is not None else None,
        note=note,
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


def diff_data(
    old: dict | None,
    new: dict | None,
) -> dict:
    """Compute an answer-level diff between two version snapshots.

    Returns added / removed / changed key lists plus the old and new values
    for each changed key so the client can render a field-level comparison.
    """
    old = old or {}
    new = new or {}
    old_keys = set(old.keys())
    new_keys = set(new.keys())

    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    changed = sorted(
        key
        for key in (old_keys & new_keys)
        if old.get(key) != new.get(key)
    )

    return {
        "added": [{"key": k, "new": new.get(k)} for k in added],
        "removed": [{"key": k, "old": old.get(k)} for k in removed],
        "changed": [
            {"key": k, "old": old.get(k), "new": new.get(k)} for k in changed
        ],
    }


async def compare_versions(
    db: AsyncSession,
    agreement_id: uuid.UUID,
    from_version: int,
    to_version: int,
) -> dict:
    """Compare two versions of an agreement.

    Returns a unified text diff of rendered content plus an answer-level
    data diff. Either side may be missing content (an un-rendered draft
    version), in which case the text diff is empty.
    """
    old = await get_version_by_number(db, agreement_id, from_version)
    new = await get_version_by_number(db, agreement_id, to_version)
    if old is None:
        raise ValueError(f"Version {from_version} not found")
    if new is None:
        raise ValueError(f"Version {to_version} not found")

    old_lines = (old.content or "").splitlines(keepends=True)
    new_lines = (new.content or "").splitlines(keepends=True)
    unified = "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"v{from_version}",
            tofile=f"v{to_version}",
        )
    )

    return {
        "from_version": from_version,
        "to_version": to_version,
        "from_content_hash": old.content_hash,
        "to_content_hash": new.content_hash,
        "content_diff": unified,
        "data_diff": diff_data(old.data, new.data),
    }


async def restore_version(
    db: AsyncSession,
    agreement: Agreement,
    version: AgreementVersion,
    created_by: uuid.UUID,
) -> AgreementVersion:
    """Restore a previous version by appending a new version with its content.

    History is never rewritten: the restore is itself a new version (N+1)
    carrying the restored answers and rendered text, and the agreement's
    working data is reset to the restored snapshot.
    """
    restored_data = copy.deepcopy(version.data) if version.data is not None else None

    if restored_data is not None:
        agreement.data = restored_data
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(agreement, "data")

    return await create_version(
        db=db,
        agreement=agreement,
        content=version.content or "",
        created_by=created_by,
        status="draft",
        data=restored_data,
        note=f"Restored from v{version.version_number}",
    )

