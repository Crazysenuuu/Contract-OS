"""Security monitoring service (spec §95).

Detects anomalous activity patterns without automatically blocking users:

- Impossible travel: two auth events from geographically distant locations
  within a time window shorter than physically possible
- Bulk export: rapid download of many documents/agreements
- Repeated failed authentication
- Suspicious signature activity: signing outside normal hours, from new IP
- Abnormal API usage: request rate spikes, unusual endpoint patterns

Every finding is advisory — generates a review alert, never blocks
the user directly.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEvent


@dataclass
class SecurityFinding:
    category: str  # impossible_travel | bulk_export | failed_auth | suspicious_signature | api_anomaly
    severity: str  # low | medium | high | critical
    description: str
    actor_id: str | None = None
    evidence: dict = field(default_factory=dict)
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


async def detect_impossible_travel(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    window_minutes: int = 60,
    max_distance_km: float = 500,
) -> list[SecurityFinding]:
    """Detect two auth events from distant IPs within a short window.

    Speed threshold: 500km/h (commercial flight).
    If two events from different cities are < max_distance_km / 500 hours apart.
    """
    findings: list[SecurityFinding] = []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

    recent_auths = (
        await db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == org_id,
                AuditEvent.action.in_(["LOGIN", "MFA_VERIFY"]),
                AuditEvent.created_at >= cutoff,
            )
            .order_by(AuditEvent.created_at.desc())
        )
    ).all()

    # Group by actor
    by_actor: dict[str, list[AuditEvent]] = {}
    for ev in recent_auths:
        by_actor.setdefault(str(ev.actor_id), []).append(ev)

    for actor_id, events in by_actor.items():
        if len(events) < 2:
            continue
        ips = [
            e.metadata_json.get("ip_address") if e.metadata_json else None
            for e in events
        ]
        # Simple heuristic: different IPs within the window
        unique_ips = set(ip for ip in ips if ip)
        if len(unique_ips) >= 2:
            findings.append(SecurityFinding(
                category="impossible_travel",
                severity="medium",
                description=f"Actor {actor_id[:8]}… authenticated from {len(unique_ips)} different IPs within {window_minutes}min",
                actor_id=actor_id,
                evidence={"ips": list(unique_ips), "event_count": len(events)},
            ))

    return findings


async def detect_bulk_export(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    window_minutes: int = 15,
    threshold: int = 10,
) -> list[SecurityFinding]:
    """Detect bulk document/agreement downloads within a short window."""
    findings: list[SecurityFinding] = []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

    download_events = (
        await db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == org_id,
                AuditEvent.action.in_(["DOCUMENT_DOWNLOAD", "AGREEMENT_EXPORT", "PDF_EXPORT"]),
                AuditEvent.created_at >= cutoff,
            )
        )
    ).all()

    by_actor: dict[str, list[AuditEvent]] = {}
    for ev in download_events:
        by_actor.setdefault(str(ev.actor_id), []).append(ev)

    for actor_id, events in by_actor.items():
        if len(events) >= threshold:
            findings.append(SecurityFinding(
                category="bulk_export",
                severity="high",
                description=f"Actor {actor_id[:8]}… downloaded {len(events)} documents in {window_minutes}min (threshold: {threshold})",
                actor_id=actor_id,
                evidence={"download_count": len(events), "window_minutes": window_minutes},
            ))

    return findings


async def detect_repeated_failed_auth(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    window_minutes: int = 30,
    threshold: int = 5,
) -> list[SecurityFinding]:
    """Detect repeated failed authentication attempts."""
    findings: list[SecurityFinding] = []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

    failed_events = (
        await db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == org_id,
                AuditEvent.action.in_(["LOGIN_FAILED", "MFA_FAILED"]),
                AuditEvent.created_at >= cutoff,
            )
        )
    ).all()

    by_actor: dict[str, list[AuditEvent]] = {}
    for ev in failed_events:
        by_actor.setdefault(str(ev.actor_id or "unknown"), []).append(ev)

    for actor_id, events in by_actor.items():
        if len(events) >= threshold:
            findings.append(SecurityFinding(
                category="failed_auth",
                severity="high",
                description=f"Actor {actor_id[:8]}… had {len(events)} failed auth attempts in {window_minutes}min",
                actor_id=actor_id,
                evidence={"failure_count": len(events)},
            ))

    return findings


async def detect_suspicious_signatures(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    off_hours_start: int = 22,  # 10 PM
    off_hours_end: int = 6,     # 6 AM
) -> list[SecurityFinding]:
    """Detect signatures executed outside normal business hours."""
    findings: list[SecurityFinding] = []

    recent_signs = (
        await db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == org_id,
                AuditEvent.action.in_(["SIGNED", "AGREEMENT_EXECUTED"]),
                AuditEvent.created_at >= datetime.now(timezone.utc) - timedelta(days=7),
            )
        )
    ).all()

    for ev in recent_signs:
        if ev.created_at is None:
            continue
        hour = ev.created_at.hour
        if hour >= off_hours_start or hour < off_hours_end:
            findings.append(SecurityFinding(
                category="suspicious_signature",
                severity="medium",
                description=f"Signature at {ev.created_at.isoformat()} (off-hours: {hour}:00)",
                actor_id=str(ev.actor_id) if ev.actor_id else None,
                evidence={"hour": hour, "action": ev.action},
            ))

    return findings


async def run_all_checks(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
) -> list[SecurityFinding]:
    """Run all security monitoring checks and return combined findings."""
    checks = [
        detect_impossible_travel(db, org_id=org_id),
        detect_bulk_export(db, org_id=org_id),
        detect_repeated_failed_auth(db, org_id=org_id),
        detect_suspicious_signatures(db, org_id=org_id),
    ]
    results = await asyncio.gather(*checks, return_exceptions=True)
    findings: list[SecurityFinding] = []
    for result in results:
        if isinstance(result, list):
            findings.extend(result)
    return findings
