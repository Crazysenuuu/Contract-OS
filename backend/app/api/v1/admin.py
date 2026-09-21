"""
Admin API endpoints.

Admin-only / platform-owner views: who is logged in, with which company,
for how long, plus system health and platform-level statistics.

Security: every endpoint requires an account with `is_admin=True`.
Regular users never see this data - the router enforces it server-side.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_admin
from app.models.organization import Organization
from app.models.rbac import OrganizationMember
from app.models.user import User, UserSession
from app.services.migration_monitor import get_migration_monitor

router = APIRouter(prefix="/admin", tags=["Admin"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _duration_seconds(start: datetime, end: datetime | None) -> int:
    if start is None:
        return 0
    start = _strip_tz(start)
    end = _strip_tz(end or _utcnow())
    delta = end - start
    return max(0, int(delta.total_seconds()))


def _strip_tz(dt: datetime) -> datetime:
    """SQLite returns naive datetimes even for tz-aware columns; normalize."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _human_duration(seconds: int) -> str:
    seconds = max(0, seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


@router.get("/health")
async def admin_health(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """System health: API, database, migrations, and service status."""
    health = {
        "status": "healthy",
        "timestamp": _utcnow().isoformat(),
        "services": {},
    }

    try:
        await db.execute(text("SELECT 1"))
        health["services"]["database"] = {
            "status": "healthy",
            "message": "PostgreSQL connection OK",
        }
    except Exception as e:
        health["status"] = "degraded"
        health["services"]["database"] = {
            "status": "unhealthy",
            "message": str(e),
        }

    monitor = get_migration_monitor()
    migration_health = monitor.get_health_status()
    health["services"]["migrations"] = {
        "status": migration_health["status"],
        "consecutive_failures": migration_health["consecutive_failures"],
        "recent_failures_24h": migration_health["recent_failures"],
    }
    if migration_health["consecutive_failures"] >= 3:
        health["status"] = "degraded"

    active_sessions = await db.execute(
        select(func.count(UserSession.id)).where(
            UserSession.status == "active"
        )
    )
    health["services"]["sessions"] = {
        "status": "healthy",
        "active_sessions": active_sessions.scalar_one(),
    }

    return health


@router.get("/overview")
async def admin_overview(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Platform summary - counts of users, companies, agreements, sessions."""
    now = _utcnow()

    total_users = (await db.execute(
        select(func.count(User.id))
    )).scalar_one()

    active_users = (await db.execute(
        select(func.count(User.id)).where(User.status == "active")
    )).scalar_one()

    admins = (await db.execute(
        select(func.count(User.id)).where(User.is_admin.is_(True))
    )).scalar_one()

    organizations = (await db.execute(
        select(func.count(Organization.id))
    )).scalar_one()

    active_sessions = (await db.execute(
        select(func.count(UserSession.id)).where(
            UserSession.status == "active"
        )
    )).scalar_one()

    active_licensed_users = (await db.execute(
        select(func.count(func.distinct(UserSession.user_id))).where(
            UserSession.status == "active"
        )
    )).scalar_one()

    sessions_last_24h = (await db.execute(
        select(func.count(UserSession.id)).where(
            UserSession.login_at >= now - timedelta(hours=24)
        )
    )).scalar_one()

    result = await db.execute(text(
        "SELECT COUNT(*) FROM agreements"
    ))
    total_agreements = result.scalar_one()

    result = await db.execute(text(
        "SELECT status, COUNT(*) FROM agreements GROUP BY status"
    ))
    agreements_by_status = {
        str(row[0]): int(row[1]) for row in result.all()
    }

    return {
        "generated_at": now.isoformat(),
        "users": {
            "total": total_users,
            "active": active_users,
            "admins": admins,
        },
        "companies": organizations,
        "agreements": {
            "total": total_agreements,
            "by_status": agreements_by_status,
        },
        "sessions": {
            "active_now": active_sessions,
            "active_users_now": active_licensed_users,
            "last_24h": sessions_last_24h,
        },
    }


def _session_row(session: UserSession, user: User, org: Organization | None) -> dict:
    status_seen = session.last_seen_at or session.login_at
    return {
        "session_id": str(session.id),
        "user_id": str(session.user_id),
        "name": user.name,
        "email": user.email,
        "company": org.name if org else None,
        "status": session.status,
        "login_at": session.login_at.isoformat(),
        "last_seen_at": status_seen.isoformat() if status_seen else None,
        "logout_at": session.logout_at.isoformat() if session.logout_at else None,
        "online_seconds": _duration_seconds(
            session.login_at,
            session.logout_at if session.status == "logged_out" else None,
        ),
        "online_duration": _human_duration(_duration_seconds(
            session.login_at,
            session.logout_at if session.status == "logged_out" else None,
        )),
        "ip_address": session.ip_address,
        "user_agent": session.user_agent,
    }


@router.get("/sessions/active")
async def active_sessions(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Every user currently logged in, with company and online duration."""
    result = await db.execute(
        select(UserSession, User, Organization)
        .join(User, UserSession.user_id == User.id)
        .outerjoin(Organization, UserSession.organization_id == Organization.id)
        .where(UserSession.status == "active")
        .order_by(UserSession.last_seen_at.desc().nulls_last())
    )
    rows = result.all()
    return [
        _session_row(session, user, org)
        for session, user, org in rows
    ]


@router.get("/sessions/history")
async def session_history(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
):
    """Recent login sessions (active + logged out), newest first."""
    result = await db.execute(
        select(UserSession, User, Organization)
        .join(User, UserSession.user_id == User.id)
        .outerjoin(Organization, UserSession.organization_id == Organization.id)
        .order_by(UserSession.login_at.desc())
        .limit(limit)
    )
    rows = result.all()
    return [
        _session_row(session, user, org)
        for session, user, org in rows
    ]


@router.get("/sessions/users/{user_id}")
async def user_sessions(
    user_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=500),
):
    """Full session history for one specific user."""
    from uuid import UUID

    try:
        target_id = UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user id",
        )

    result = await db.execute(
        select(UserSession, User, Organization)
        .join(User, UserSession.user_id == User.id)
        .outerjoin(Organization, UserSession.organization_id == Organization.id)
        .where(UserSession.user_id == target_id)
        .order_by(UserSession.login_at.desc())
        .limit(limit)
    )
    rows = result.all()
    return [
        _session_row(session, user, org)
        for session, user, org in rows
    ]


@router.get("/companies")
async def companies_summary(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Per-company summary: users, active sessions, last activity."""
    groups = await db.execute(
        select(
            Organization,
            func.count(func.distinct(OrganizationMember.user_id)).label("members"),
            func.count(
                func.distinct(UserSession.user_id)
            ).filter(UserSession.status == "active").label("active_members"),
        )
        .outerjoin(
            OrganizationMember,
            OrganizationMember.organization_id == Organization.id,
        )
        .outerjoin(
            UserSession,
            UserSession.organization_id == Organization.id,
        )
        .group_by(Organization.id)
        .order_by(Organization.name)
    )
    rows = groups.all()
    return [
        {
            "organization_id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "country": org.country,
            "users": members,
            "active_users": active_members,
        }
        for org, members, active_members in rows
    ]


def _user_row(user: User, org: Organization | None) -> dict:
    return {
        "user_id": str(user.id),
        "name": user.name,
        "email": user.email,
        "status": user.status,
        "is_admin": bool(user.is_admin),
        "mfa_enabled": bool(user.mfa_enabled),
        "organization": org.name if org else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("/users")
async def admin_users_list(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Every registered user, with their primary organization."""
    result = await db.execute(
        select(User, Organization)
        .outerjoin(
            OrganizationMember,
            OrganizationMember.user_id == User.id,
        )
        .outerjoin(
            Organization,
            Organization.id == OrganizationMember.organization_id,
        )
        .order_by(User.created_at.desc())
    )
    seen: set = set()
    rows: list = []
    for user, org in result.all():
        if user.id in seen:
            continue
        seen.add(user.id)
        rows.append(_user_row(user, org))
    return rows


async def _get_user_or_404(user_id: str, db: AsyncSession) -> User:
    from uuid import UUID

    try:
        target_id = UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user id",
        )

    user = (
        await db.execute(select(User).where(User.id == target_id))
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


@router.post("/users/{user_id}/promote")
async def admin_promote_user(
    user_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Grant system-admin rights to an existing user."""
    target = await _get_user_or_404(user_id, db)
    if target.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are already an admin",
        )

    target.is_admin = True
    target.status = "active"
    target.email_verified_at = target.email_verified_at or _utcnow()
    await db.commit()
    return {"message": f"{target.name} is now a system admin"}


@router.post("/users/{user_id}/demote")
async def admin_demote_user(
    user_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Revoke system-admin rights from a user."""
    target = await _get_user_or_404(user_id, db)
    if target.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot demote yourself",
        )

    target.is_admin = False
    await db.commit()
    return {"message": f"{target.name}'s admin rights have been removed"}


@router.post("/users/{user_id}/activate")
async def admin_activate_user(
    user_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Re-activate a deactivated user account."""
    target = await _get_user_or_404(user_id, db)
    target.status = "active"
    await db.commit()
    return {"message": f"{target.name}'s account is active again"}


@router.post("/users/{user_id}/deactivate")
async def admin_deactivate_user(
    user_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Disable a user account (they can no longer sign in)."""
    target = await _get_user_or_404(user_id, db)
    if target.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account",
        )

    target.status = "deactivated"
    await db.execute(
        UserSession.__table__.update().where(
            (UserSession.user_id == target.id)
            & (UserSession.status == "active")
        ).values(status="logged_out", logout_at=_utcnow())
    )
    await db.commit()
    return {"message": f"{target.name}'s account has been deactivated"}


# --- Backup / disaster recovery (spec 39-40 / 58) ---------------------------

from app.services import backup_service


@router.post("/backups/run")
async def admin_run_backup(admin: User = Depends(get_current_admin)):
    """Take an encrypted pg_dump backup now (admin-only)."""
    try:
        result = backup_service.take_backup(label="manual")
    except backup_service.BackupError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok", "backup": result}


@router.get("/backups")
async def admin_list_backups(admin: User = Depends(get_current_admin)):
    """List retained encrypted backups (admin-only)."""
    return {"status": "ok", "backups": backup_service.list_backups()}


@router.post("/backups/verify-restore")
async def admin_verify_restore(
    body: Optional[dict] = None,
    admin: User = Depends(get_current_admin),
):
    """Restore the newest (or named) backup into a scratch DB and confirm
    it loads — proving the recovery mechanism end to end (spec 39)."""
    name = (body or {}).get("name") if body else None
    try:
        result = backup_service.verify_restore(name)
    except backup_service.BackupError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok", **result}


@router.delete("/backups/{name}")
async def admin_delete_backup(name: str, admin: User = Depends(get_current_admin)):
    """Delete a retained backup (admin-only)."""
    try:
        backup_service.delete_backup(name)
    except backup_service.BackupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok"}


# --- Audit batch sealing + external timestamp anchor (spec 1.20.15-16) ------


@router.post("/audit/batches/seal")
async def admin_seal_audit_batch(
    body: dict,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Seal a range of the tenant's audit chain into a Merkle batch.

    Body: {"from_sequence": int, "to_sequence": int, "tenant_id": "<uuid>",
    optional}. When TSA_URL is configured the root is additionally anchored
    with an RFC 3161 token; otherwise the batch records anchor_status=
    internal_only (honest: no external witnessing claimed).
    """
    import uuid as uuid_mod

    from app.models.audit import AuditBatch, AuditEvent
    from app.services.audit_batching import AuditBatchService

    try:
        from_seq = int(body["from_sequence"])
        to_seq = int(body["to_sequence"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_sequence and to_sequence (ints) are required",
        )
    if from_seq <= 0 or to_seq < from_seq or to_seq - from_seq > 100_000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid sequence range",
        )

    tenant_raw = body.get("tenant_id")
    if tenant_raw:
        try:
            tenant_id = uuid_mod.UUID(str(tenant_raw))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tenant_id must be a UUID",
            )
    else:
        # Default to the admin's own organization scope.
        tenant_id = admin.organization_id

    exists = await db.execute(
        select(AuditEvent.id)
        .where(
            AuditEvent.tenant_id == tenant_id,
            AuditEvent.sequence_number.between(from_seq, to_seq),
        )
        .limit(1)
    )
    if exists.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no audit events in the requested range",
        )

    service = AuditBatchService(db)
    try:
        batch = await service.seal_batch(
            tenant_id=tenant_id,
            from_sequence=from_seq,
            to_sequence=to_seq,
            actor_id=admin.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    await db.commit()
    return {
        "status": "ok",
        "batch": {
            "id": str(batch.id),
            "root_hash": batch.root_hash,
            "leaf_count": batch.leaf_count,
            "first_sequence": batch.first_sequence,
            "last_sequence": batch.last_sequence,
            "anchor_status": batch.anchor_status,
            "anchored_at": (
                batch.anchored_at.isoformat() if batch.anchored_at else None
            ),
        },
    }


@router.post("/audit/batches/{batch_id}/verify")
async def admin_verify_audit_batch(
    batch_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Recompute a batch's Merkle root from live events and verify the
    stored RFC 3161 anchor token attests that root (admin-only)."""
    import uuid as uuid_mod

    from app.models.audit import AuditBatch
    from app.services.audit_batching import AuditBatchService
    from app.services.rfc3161_tsa import verify_anchor_token

    try:
        batch_uuid = uuid_mod.UUID(batch_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid batch id"
        )

    result = await db.execute(
        select(AuditBatch).where(AuditBatch.id == batch_uuid)
    )
    batch = result.scalar_one_or_none()
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="batch not found"
        )

    merkle = await AuditBatchService(db).verify_batch(batch_uuid)
    anchor = None
    if batch.anchor_token:
        anchor = verify_anchor_token(batch.anchor_token, batch.root_hash)

    return {
        "status": "ok",
        "batch_id": batch_id,
        "merkle": merkle,
        "anchor": anchor,
    }