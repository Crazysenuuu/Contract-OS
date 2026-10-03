"""Retention engine and legal hold service (spec 1.22 / 2.07).

- Applies retention policies to agreements (anchor = executed/created date).
- Detects active legal holds and freezes retention/disposal.
- Runs the retention worker: flags expired records, archives or marks
  deletion candidates, respecting legal holds.
- Records repository artifacts (executed documents, evidence packages)
  with hashes and object-storage refs.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.retention import (
    LegalHold,
    RepositoryRecord,
    RetentionPolicy,
    RetentionRecord,
)
from app.services import document_storage

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_active_policy(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    agreement_type_key: str | None,
) -> RetentionPolicy | None:
    """Find the retention policy for an agreement type (or the 'all' fallback)."""
    result = await db.execute(
        select(RetentionPolicy).where(
            RetentionPolicy.organization_id == org_id,
            RetentionPolicy.is_active.is_(True),
        )
    )
    policies = result.scalars().all()
    if not policies:
        return None

    for p in policies:
        if p.scope == "agreement_type" and p.agreement_type_key == agreement_type_key:
            return p
    for p in policies:
        if p.scope == "all":
            return p
    return None


async def apply_policy_to_agreement(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    agreement: Agreement,
) -> RetentionRecord | None:
    """Create/refresh the retention record for one agreement."""
    agreement_type_key = None
    if agreement.agreement_type_id:
        from app.models.agreement_type import AgreementType

        type_result = await db.execute(
            select(AgreementType.key).where(AgreementType.id == agreement.agreement_type_id)
        )
        agreement_type_key = type_result.scalar_one_or_none()

    policy = await get_active_policy(db, org_id=org_id, agreement_type_key=agreement_type_key)
    if policy is None:
        return None

    anchor = agreement.effective_date or agreement.created_at
    if isinstance(anchor, datetime) and anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    retention_until = anchor + timedelta(days=30 * policy.retention_months)

    existing_result = await db.execute(
        select(RetentionRecord).where(RetentionRecord.agreement_id == agreement.id)
    )
    record = existing_result.scalar_one_or_none()

    # A held record keeps its status frozen; don't reset it to active.
    frozen = record is not None and record.status == "held"

    if record is None:
        record = RetentionRecord(
            organization_id=org_id,
            agreement_id=agreement.id,
            policy_id=policy.id,
            retention_until=retention_until,
            anchor_date=anchor,
            status="active",
        )
        db.add(record)
    else:
        record.policy_id = policy.id
        record.retention_until = retention_until
        record.anchor_date = anchor
        if not frozen:
            record.status = "active"
    await db.flush()
    return record


async def place_legal_hold(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    agreement_id: uuid.UUID | None,
    reason: str,
    hold_type: str = "manual",
    placed_by: uuid.UUID,
) -> LegalHold:
    """Place a legal hold; freezes the matching retention record."""
    hold = LegalHold(
        organization_id=org_id,
        agreement_id=agreement_id,
        reason=reason,
        hold_type=hold_type,
        status="active",
        started_at=_now(),
        placed_by=placed_by,
    )
    db.add(hold)

    # Freeze retention for the agreement (or all org agreements).
    query = select(RetentionRecord).where(RetentionRecord.organization_id == org_id)
    if agreement_id is not None:
        query = query.where(RetentionRecord.agreement_id == agreement_id)
    records = (await db.execute(query)).scalars().all()
    for rec in records:
        rec.status = "held"
    await db.flush()
    return hold


async def release_legal_hold(
    db: AsyncSession,
    *,
    hold_id: uuid.UUID,
    released_by: uuid.UUID,
) -> LegalHold:
    """Release a legal hold; held retention records return to active."""
    result = await db.execute(select(LegalHold).where(LegalHold.id == hold_id))
    hold = result.scalar_one_or_none()
    if hold is None:
        raise ValueError("Legal hold not found")
    if hold.status == "released":
        return hold

    hold.status = "released"
    hold.released_at = _now()
    hold.released_by = released_by

    query = select(RetentionRecord).where(
        RetentionRecord.organization_id == hold.organization_id
    )
    if hold.agreement_id is not None:
        query = query.where(RetentionRecord.agreement_id == hold.agreement_id)
    records = (await db.execute(query)).scalars().all()
    for rec in records:
        if rec.status == "held":
            rec.status = "active"
    await db.flush()
    return hold


async def list_active_holds(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    agreement_id: uuid.UUID | None = None,
) -> list[LegalHold]:
    query = select(LegalHold).where(
        LegalHold.organization_id == org_id,
        LegalHold.status == "active",
    )
    if agreement_id is not None:
        query = query.where(LegalHold.agreement_id == agreement_id)
    return (await db.execute(query)).scalars().all()


async def _delete_agreement_blobs(
    db: AsyncSession, agreement_id: uuid.UUID
) -> tuple[int, bool]:
    """Delete every repository object stored for an agreement.

    Returns (blobs_removed, all_ok). A storage failure is logged and leaves
    ``all_ok`` False so the caller can keep the retention record retryable
    instead of marking it deleted while the object still exists.
    """
    refs = (
        await db.execute(
            select(RepositoryRecord.content_ref).where(
                RepositoryRecord.agreement_id == agreement_id
            )
        )
    ).scalars().all()

    removed = 0
    all_ok = True
    for content_ref in refs:
        try:
            document_storage.delete_blob(content_ref)
            removed += 1
        except (document_storage.StorageError, OSError):
            # S3 failures surface as StorageError; the local backend can raise
            # a bare OSError from unlink(). Treat both as retryable.
            logger.exception(
                "Retention delete: failed to remove blob %s for agreement %s",
                content_ref,
                agreement_id,
            )
            all_ok = False
    return removed, all_ok


async def run_retention_worker(
    db: AsyncSession,
    *,
    org_id: uuid.UUID | None = None,
    dry_run: bool = True,
) -> dict:
    """Retention worker: find expired records and apply disposition.

    Records under legal hold are skipped. With dry_run=True (default) no
    rows are mutated — only reported.
    """
    now = _now()
    query = select(RetentionRecord).where(
        RetentionRecord.retention_until < now,
    )
    if org_id is not None:
        query = query.where(RetentionRecord.organization_id == org_id)
    records = (await db.execute(query)).scalars().all()

    expired = 0
    held_skipped = 0
    archived = 0
    deletion_candidates = 0
    blobs_deleted = 0

    for rec in records:
        # Anything under an active legal hold is frozen regardless of its
        # current status; report it and never touch it.
        holds = await list_active_holds(db, org_id=rec.organization_id, agreement_id=rec.agreement_id)
        if holds:
            held_skipped += 1
            continue

        # Already disposed records need no further action.
        if rec.status in ("archived", "deleted"):
            continue

        policy = None
        if rec.policy_id:
            policy_result = await db.execute(
                select(RetentionPolicy).where(RetentionPolicy.id == rec.policy_id)
            )
            policy = policy_result.scalar_one_or_none()

        disposition = (policy.disposition if policy else "archive") or "archive"
        expired += 1
        if disposition == "delete":
            deletion_candidates += 1
            if not dry_run:
                removed, all_ok = await _delete_agreement_blobs(db, rec.agreement_id)
                blobs_deleted += removed
                # Only mark deleted once the stored objects are gone, so a
                # storage failure is retried next run instead of leaving a
                # blob in the bucket that no record points at.
                if all_ok:
                    rec.status = "deleted"
        else:
            archived += 1
            if not dry_run:
                rec.status = "archived"

    if not dry_run:
        await db.flush()

    return {
        "dry_run": dry_run,
        "expired_found": expired,
        "held_skipped": held_skipped,
        "archived": archived,
        "deletion_candidates": deletion_candidates,
        "blobs_deleted": blobs_deleted,
    }


async def store_repository_record(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    agreement_id: uuid.UUID,
    version_id: uuid.UUID | None,
    document_type: str,
    content_ref: str,
    content_hash: str,
    mime_type: str = "application/pdf",
    size_bytes: int | None = None,
    classification: str = "internal",
    is_executed: bool = False,
    metadata_: dict | None = None,
) -> RepositoryRecord:
    """Persist a repository artifact with its object-storage ref + hash."""
    record = RepositoryRecord(
        organization_id=org_id,
        agreement_id=agreement_id,
        version_id=version_id,
        document_type=document_type,
        content_ref=content_ref,
        content_hash=content_hash,
        mime_type=mime_type,
        size_bytes=size_bytes,
        classification=classification,
        is_executed=is_executed,
        metadata_=metadata_,
    )
    db.add(record)
    await db.flush()
    return record