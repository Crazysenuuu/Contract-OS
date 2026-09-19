"""Clause lifecycle governance (spec 24.8).

Implements the clause library governance rules:

- Clause lifecycle: a clause version can be 'active', 'deprecated', or
  'archived'. Only users with the ``clause.publish`` permission (or a
  platform admin) may publish/deprecate/archive a version.
- New-version publication: publishing a newer version of a clause (same
  title/parent) deprecates older active versions.
- In-flight draft resolution: any draft agreement that references a
  deprecated clause version must be flagged, and the contract owner must
  explicitly choose to upgrade to the new version or keep the legacy one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agreement_states import EDITABLE_STATES
from app.models.agreement import Agreement
from app.models.document_intelligence import ClauseLibrary
from app.models.rbac import OrganizationMember, RolePermission, Permission, Role


class ClauseGovernanceError(Exception):
    """Raised when a governance rule is violated."""


async def _user_can_publish(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
) -> bool:
    """Check clause.publish permission via role permissions (or admin)."""
    from app.models.user import User

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is not None and user.is_admin:
        return True

    # Resolve permission id for clause.publish
    perm_result = await db.execute(
        select(Permission.id).where(Permission.key == "clause.publish")
    )
    perm_id = perm_result.scalar_one_or_none()
    if perm_id is None:
        return False

    # Find the user's roles in this org
    roles_result = await db.execute(
        select(OrganizationMember.role_id).where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == user_id,
            OrganizationMember.status == "active",
        )
    )
    role_ids = [r[0] for r in roles_result.all()]
    if not role_ids:
        return False

    rp_result = await db.execute(
        select(RolePermission.permission_id).where(
            RolePermission.role_id.in_(role_ids),
            RolePermission.permission_id == perm_id,
        )
    )
    return rp_result.first() is not None


async def publish_clause_version(
    db: AsyncSession,
    *,
    clause_id: uuid.UUID,
    actor_id: uuid.UUID,
    org_id: uuid.UUID,
    new_text: str | None = None,
    new_title: str | None = None,
) -> ClauseLibrary:
    """Publish a new version of a library clause.

    Creates a new ClauseLibrary row linked via parent_id to the original and
    deprecates the previously active version(s) with the same parent/title
    group. Requires the clause.publish permission.
    """
    if not await _user_can_publish(db, user_id=actor_id, org_id=org_id):
        raise ClauseGovernanceError(
            "Only a Legal Administrator (clause.publish) may publish clause versions"
        )

    result = await db.execute(
        select(ClauseLibrary).where(ClauseLibrary.id == clause_id)
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise ClauseGovernanceError("Clause not found")

    # Identify the version group: the root parent id, or self if root.
    group_parent = source.parent_id or source.id

    # New version number = max in group + 1
    group_result = await db.execute(
        select(ClauseLibrary.version).where(
            ClauseLibrary.parent_id == group_parent
        )
    )
    versions = [v[0] for v in group_result.all()]
    versions.append(source.version if source.parent_id is None else 0)
    next_version = max(versions) + 1

    new_entry = ClauseLibrary(
        organization_id=source.organization_id,
        title=new_title or source.title,
        text=new_text or source.text,
        description=source.description,
        category=source.category,
        subcategory=source.subcategory,
        tags=source.tags,
        jurisdictions=source.jurisdictions,
        agreement_types=source.agreement_types,
        risk_level=source.risk_level,
        risk_score=source.risk_score,
        favorable_for=source.favorable_for,
        version=next_version,
        is_system=source.is_system,
        is_approved=True,
        approved_by=actor_id,
        approved_at=datetime.now(timezone.utc),
        parent_id=group_parent,
        lifecycle_status="active",
        published_by=actor_id,
        published_at=datetime.now(timezone.utc),
        created_by=actor_id,
    )
    db.add(new_entry)
    await db.flush()

    # Deprecate other active versions in the group (including the source
    # when it is the root of the group).
    await db.execute(
        update(ClauseLibrary)
        .where(
            ClauseLibrary.id != new_entry.id,
            (
                (ClauseLibrary.parent_id == group_parent)
                | (ClauseLibrary.id == group_parent)
            ),
            ClauseLibrary.lifecycle_status == "active",
        )
        .values(
            lifecycle_status="deprecated",
            deprecation_reason=f"Superseded by version {next_version}",
        )
    )
    await db.flush()
    return new_entry


async def set_clause_status(
    db: AsyncSession,
    *,
    clause_id: uuid.UUID,
    status: str,
    actor_id: uuid.UUID,
    org_id: uuid.UUID,
    reason: str | None = None,
) -> ClauseLibrary:
    """Set lifecycle status: active / deprecated / archived."""
    if status not in {"active", "deprecated", "archived"}:
        raise ClauseGovernanceError(f"Invalid lifecycle status: {status}")

    if not await _user_can_publish(db, user_id=actor_id, org_id=org_id):
        raise ClauseGovernanceError(
            "Only a Legal Administrator (clause.publish) may change clause lifecycle status"
        )

    result = await db.execute(
        select(ClauseLibrary).where(ClauseLibrary.id == clause_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise ClauseGovernanceError("Clause not found")

    entry.lifecycle_status = status
    entry.deprecation_reason = reason if reason is not None else entry.deprecation_reason
    entry.published_by = actor_id
    entry.published_at = datetime.now(timezone.utc)
    await db.flush()
    return entry


async def find_inflight_drafts(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    clause_id: uuid.UUID,
) -> list[dict]:
    """Find un-signed drafts that reference a clause version (in-flight).

    Drafts are matched by clause title/keywords in their data. Each result
    includes the agreement id, title, status, and which clause version it
    references, so the owner can choose upgrade vs legacy (24.8).
    """
    result = await db.execute(
        select(ClauseLibrary).where(ClauseLibrary.id == clause_id)
    )
    clause = result.scalar_one_or_none()
    if clause is None:
        return []

    drafts_result = await db.execute(
        select(Agreement).where(
            Agreement.organization_id == org_id,
            Agreement.status.in_(sorted(EDITABLE_STATES)),
        )
    )
    drafts: list[dict] = []
    for agreement in drafts_result.scalars().all():
        data = agreement.data or {}
        referenced = data.get("clause_version_ids") or data.get("clauses") or []
        references = False
        referenced_id = None
        if isinstance(referenced, list):
            for item in referenced:
                if isinstance(item, dict) and item.get("id") == str(clause_id):
                    references = True
                    referenced_id = str(clause_id)
                    break
                if item == str(clause_id):
                    references = True
                    referenced_id = str(clause_id)
                    break
        elif str(clause_id) in str(data):
            references = True
            referenced_id = str(clause_id)

        if references:
            drafts.append({
                "agreement_id": str(agreement.id),
                "title": agreement.title,
                "status": agreement.status,
                "referenced_clause_id": referenced_id,
                "clause_title": clause.title,
                "clause_version": clause.version,
            })
    return drafts


async def resolve_inflight_draft(
    db: AsyncSession,
    *,
    agreement_id: uuid.UUID,
    clause_id: uuid.UUID,
    resolution: str,  # 'upgrade' | 'keep_legacy'
    actor_id: uuid.UUID,
) -> dict:
    """Resolve an in-flight draft: upgrade to the new clause version or keep
    the legacy version explicitly.

    'upgrade' records the new clause version id in the agreement data;
    'keep_legacy' records an explicit override so the deprecation is
    transparently logged and no longer flagged.
    """
    if resolution not in {"upgrade", "keep_legacy"}:
        raise ClauseGovernanceError(f"Invalid resolution: {resolution}")

    result = await db.execute(
        select(Agreement).where(Agreement.id == agreement_id)
    )
    agreement = result.scalar_one_or_none()
    if agreement is None:
        raise ClauseGovernanceError("Agreement not found")

    data = dict(agreement.data or {})
    resolutions = data.setdefault("clause_resolutions", [])
    resolutions = [r for r in resolutions if r.get("clause_id") != str(clause_id)]
    resolutions.append({
        "clause_id": str(clause_id),
        "resolution": resolution,
        "resolved_by": str(actor_id),
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    })
    data["clause_resolutions"] = resolutions
    agreement.data = data
    await db.flush()
    return {
        "agreement_id": str(agreement.id),
        "clause_id": str(clause_id),
        "resolution": resolution,
    }