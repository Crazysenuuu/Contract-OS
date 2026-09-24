"""Monitoring execution service (spec 3.15.22).

Pipeline for one obligation-monitoring definition:

    claim idempotent run → resolve credentials → build connector →
    fetch observations → persist (dedup) → evaluate → record evaluation +
    health + exception/notifications → advance next_run_at.

Failure doctrine (3.15.25, 3.15.62): a connector/source failure raises a
connector error which is recorded as an INCONCLUSIVE evaluation with the
connector's health row degraded. INCONCLUSIVE is never surfaced as a
contractual PASS/FAIL, and a FAIL always carries the underlying
observations / metrics for human review (3.15.24).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.monitoring.connectors import connector_registry
from app.monitoring.credentials import get_active_credentials
from app.monitoring.enums import (
    EvaluationResult,
    IntegrationStatus,
    MonitoringStatus,
    PauseReason,
)
from app.monitoring.evaluators import EvaluationContext, get_evaluator
from app.monitoring.exceptions import (
    CredentialUnavailable,
    MonitoringError,
    UnsupportedConnector,
    UnsupportedEvaluator,
)
from app.monitoring.models import (
    IntegrationConnection,
    IntegrationHealth,
    MonitoringEvaluation,
    MonitoringException,
    MonitoringRun,
    ObligationMonitoring,
)
from app.monitoring.observations import canon_json, ensure_aware_utc, hash_payload, persist_observations
from app.services.audit_service import record_event
from app.services.event_outbox_service import enqueue_event

_log = logging.getLogger(__name__)

EVENT_EVALUATION_PASSED = "monitoring.evaluation_passed"
EVENT_EVALUATION_FAILED = "monitoring.evaluation_failed"
EVENT_SOURCE_UNAVAILABLE = "monitoring.source_unavailable"
EVENT_RECOVERED = "monitoring.recovered"
EVENT_STALE_VERSION = "monitoring.stale_version"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Schedule arithmetic
# --------------------------------------------------------------------------

class CronExpression:
    """Tiny cron subset for monitoring schedules.

    Supports five-field expressions (minute hour day-of-month month
    day-of-week) with steps (``*/15``) and lists (``0,30``).
    """

    def __init__(self, expression: str) -> None:
        parts = expression.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Cron expression must have 5 fields: '{expression}'")
        self.fields = parts

    def matches(self, dt: datetime) -> bool:
        values = [dt.minute, dt.hour, dt.day, dt.month, dt.weekday() + 1]
        return all(_cron_field_matches(field, value) for field, value in zip(self.fields, values))


def _cron_field_matches(field: str, value: int) -> bool:
    if field == "*":
        return True
    for chunk in field.split(","):
        if "/" in chunk:
            base, step = chunk.split("/", 1)
            base_value = 0 if base == "*" else int(base)
            if value >= base_value and (value - base_value) % int(step) == 0:
                return True
        elif "-" in chunk:
            low, high = (int(p) for p in chunk.split("-", 1))
            if low <= value <= high:
                return True
        elif chunk.isdigit() and int(chunk) == value:
            return True
    return False


def compute_next_run(schedule: dict, now: datetime | None = None) -> datetime:
    """Next run time for a ``schedule_definition``.

    Supported forms:

      ``{"recurrence": "cron", "cron": "*/5 * * * *"}``
      ``{"recurrence": "interval", "minutes": 30 | "hours": 1 | "days": 1}``
      ``{"recurrence": "daily", "at": "09:00"}``
    """
    now = now or now_utc()
    recurrence = schedule.get("recurrence") or "interval"

    if recurrence == "cron":
        expression = schedule.get("cron")
        if not expression:
            raise ValueError("cron schedule requires 'cron' expression")
        cron = CronExpression(expression)
        probe = now
        for _ in range(1440):
            probe = probe + timedelta(minutes=1)
            if cron.matches(probe):
                return probe
        raise ValueError("cron expression never matches")

    if recurrence == "daily":
        at = str(schedule.get("at") or "09:00")
        hour, *rest = at.split(":")
        minute = int(rest[0]) if rest else 0
        candidate = now.replace(hour=int(hour), minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    minutes = int(schedule.get("minutes") or 0)
    hours = int(schedule.get("hours") or 0)
    days = int(schedule.get("days") or 0)
    step = timedelta(minutes=minutes, hours=hours, days=days)
    if step.total_seconds() <= 0:
        step = timedelta(minutes=60)
    return now + step


def run_period_key(monitoring: ObligationMonitoring, run_at: datetime) -> str:
    """Stable period identifier used in the run idempotency key."""
    return f"{monitoring.id}:{run_at.strftime('%Y-%m-%dT%H:%M')}"


# --------------------------------------------------------------------------
# Run execution
# --------------------------------------------------------------------------

async def run_obligation_monitoring(
    db: AsyncSession,
    *,
    monitoring: ObligationMonitoring,
    run_at: datetime | None = None,
    now: datetime | None = None,
) -> MonitoringEvaluation:
    """Execute one monitoring definition end to end, idempotently.

    One ``MonitoringRun`` per (monitoring, scheduled_period); a repeated
    worker invocation returns the existing evaluation. Source failures
    (credential, connector, unsupported evaluator) are captured as
    INCONCLUSIVE evaluations inside the caller's transaction.
    """
    run_at = run_at or now_utc()
    now = now or now_utc()
    run, is_new = await _claim_run(db, monitoring, run_at)
    if not is_new:
        existing = await db.scalar(
            select(MonitoringEvaluation).where(MonitoringEvaluation.run_id == run.id)
        )
        if existing is not None:
            return existing

    definition_hash = hash_definition(monitoring)

    try:
        outcome = await _execute(db, monitoring, now)
    except (CredentialUnavailable, UnsupportedConnector, UnsupportedEvaluator, MonitoringError) as exc:
        evaluation = await _record_source_failure(
            db,
            monitoring=monitoring,
            run_id=run.id,
            definition_hash=definition_hash,
            now=now,
            reason=str(exc),
            exception_type=type(exc).__name__,
        )
        return _finalize_run(db, run, monitoring, evaluation, now, outcome=None)

    evaluation = await _record_evaluation(
        db,
        monitoring=monitoring,
        run_id=run.id,
        definition_hash=definition_hash,
        outcome=outcome,
        observation_ids=outcome.metrics.get("_observation_ids", []),
        now=now,
    )
    return _finalize_run(db, run, monitoring, evaluation, now, outcome=outcome)


def _finalize_run(
    db: AsyncSession,
    run: MonitoringRun,
    monitoring: ObligationMonitoring,
    evaluation: MonitoringEvaluation,
    now: datetime,
    outcome: EvaluationOutcome | None,
) -> MonitoringEvaluation:
    run.status = "COMPLETED"
    run.completed_at = now
    monitoring.last_run_at = now
    if outcome is not None:
        monitoring.last_result = outcome.result.name
        try:
            monitoring.next_run_at = compute_next_run(monitoring.schedule_definition, now)
        except (ValueError, TypeError):
            monitoring.next_run_at = None
    db.add(run)
    db.add(monitoring)
    return evaluation


async def _claim_run(
    db: AsyncSession,
    monitoring: ObligationMonitoring,
    run_at: datetime,
) -> tuple[MonitoringRun, bool]:
    from app.monitoring.observations import canon_json

    period = run_at.strftime("%Y-%m-%dT%H:%M")
    key = run_period_key(monitoring, run_at)
    existing = await db.scalar(select(MonitoringRun).where(MonitoringRun.idempotency_key == key))
    if existing is not None:
        return existing, False
    run = MonitoringRun(
        monitoring_id=monitoring.id,
        organization_id=monitoring.organization_id,
        idempotency_key=key,
        status="RUNNING",
        scheduled_period=period,
        started_at=run_at,
    )
    db.add(run)
    await db.flush()
    return run, True


async def _load_obligation(db: AsyncSession, monitoring: ObligationMonitoring) -> dict:
    from app.models.obligation import Obligation

    obligation = await db.get(Obligation, monitoring.obligation_id)
    if obligation is None:
        return {}
    data = dict(obligation.__dict__)
    data.pop("_sa_instance_state", None)
    return data


def _resolve_query(query: dict, obligation: dict) -> dict:
    """Resolve ``value_source: AGREEMENT_FIELD`` placeholders in the query.

    Placeholders inside ``filters``/``values`` become literal values from the
    obligation record; unknown fields resolve to ``None`` so the connector
    simply matches nothing (never fabricated input).
    """
    resolved = dict(query)

    def walk(node):
        if isinstance(node, dict):
            if node.get("value_source") == "AGREEMENT_FIELD":
                field = node.get("field")
                return obligation.get(field)
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(resolved)


async def _execute(db: AsyncSession, monitoring: ObligationMonitoring, now: datetime):
    """Fetch + persist + evaluate. Source failures raise connector errors."""
    from app.monitoring.evaluators import EvaluationOutcome

    integration = await db.get(IntegrationConnection, monitoring.integration_id)
    if integration is None:
        raise UnsupportedConnector(
            f"Integration {monitoring.integration_id} does not exist"
        )

    credentials = await get_active_credentials(db, integration.id)
    connector = connector_registry.create(
        integration.provider_key,
        credentials=credentials,
        configuration=integration.configuration or {},
    )

    obligation = await _load_obligation(db, monitoring)
    query = _resolve_query(monitoring.query_definition, obligation)

    started = now_utc()
    try:
        observations = await connector.fetch(query)
    finally:
        await connector.close()
    latency_ms = int((now_utc() - started).total_seconds() * 1000)

    was_degraded = integration.status == IntegrationStatus.DEGRADED.value
    await _record_transport_success(db, integration.id, latency_ms=latency_ms, now=now)

    created_ids = await persist_observations(
        db,
        organization_id=monitoring.organization_id,
        integration_id=integration.id,
        monitoring_id=monitoring.id,
        observations=observations,
    )

    if was_degraded:
        # Healthy fetch after a failure streak → explicit recovery signal.
        integration.status = IntegrationStatus.ACTIVE.value
        await _enqueue_monitoring_notification(
            db,
            monitoring=monitoring,
            event_type=EVENT_RECOVERED,
            title_reason="Monitoring source recovered",
            notification_type="monitoring_summary",
            severity="RECOVERED",
        )
        await _audit_event(
            db,
            monitoring,
            action="monitoring.recovered",
            resource_id=monitoring.id,
            metadata_json={"event": "recovery"},
        )

    context = EvaluationContext(obligation=obligation or None, now=now)
    evaluator = get_evaluator(
        (monitoring.evaluation_definition or {}).get("kind") or "threshold"
    )
    outcome: EvaluationOutcome = evaluator.evaluate(
        observations=observations,
        definition=monitoring.evaluation_definition,
        context=context,
    )
    outcome.metrics["_observation_ids"] = created_ids
    return outcome


async def _record_transport_success(
    db: AsyncSession,
    integration_id: uuid.UUID,
    *,
    latency_ms: int,
    now: datetime,
) -> None:
    health = await db.get(IntegrationHealth, integration_id)
    if health is None:
        health = IntegrationHealth(
            integration_id=integration_id,
            last_success_at=now,
            last_latency_ms=latency_ms,
            consecutive_failures=0,
        )
        db.add(health)
    else:
        health.last_success_at = now
        health.last_error = None
        health.last_error_code = None
        health.last_latency_ms = latency_ms


async def _record_source_failure(
    db: AsyncSession,
    *,
    monitoring: ObligationMonitoring,
    run_id: uuid.UUID,
    definition_hash: str,
    now: datetime,
    reason: str,
    exception_type: str,
) -> MonitoringEvaluation:
    integration = await db.get(IntegrationConnection, monitoring.integration_id)

    if integration is not None:
        integration.status = IntegrationStatus.DEGRADED.value
        health = await db.get(IntegrationHealth, integration.id)
        if health is None:
            health = IntegrationHealth(
                integration_id=integration.id,
                last_failure_at=now,
                consecutive_failures=1,
                last_error_code=exception_type,
                last_error=reason[:2000],
            )
            db.add(health)
        else:
            health.last_failure_at = now
            health.consecutive_failures += 1
            health.last_error_code = exception_type
            health.last_error = reason[:2000]

    evaluation = MonitoringEvaluation(
        organization_id=monitoring.organization_id,
        monitoring_id=monitoring.id,
        run_id=run_id,
        obligation_instance_id=None,
        evaluation_definition_hash=definition_hash,
        result=EvaluationResult.INCONCLUSIVE.value,
        status="COMPLETED",
        metrics={
            "error": reason,
            "exception_type": exception_type,
            "observed_count": 0,
        },
        observation_ids=[],
        evaluated_at=now,
        details={"source_failure": True, "reason": reason, "exception_type": exception_type},
    )
    db.add(evaluation)
    await db.flush()

    if integration is not None:
        await _enqueue_monitoring_notification(
            db,
            monitoring=monitoring,
            event_type=EVENT_SOURCE_UNAVAILABLE,
            title_reason=reason,
            notification_type="monitoring_alert",
            severity="INCONCLUSIVE",
        )
    await _audit_event(
        db,
        monitoring,
        action="monitoring.source_unavailable",
        resource_id=monitoring.id,
        metadata_json={"exception_type": exception_type, "reason": reason},
    )
    return evaluation


async def _record_evaluation(
    db: AsyncSession,
    *,
    monitoring: ObligationMonitoring,
    run_id: uuid.UUID,
    definition_hash: str,
    outcome,
    observation_ids: list,
    now: datetime,
) -> MonitoringEvaluation:
    result = outcome.result.name
    evaluation = MonitoringEvaluation(
        organization_id=monitoring.organization_id,
        monitoring_id=monitoring.id,
        run_id=run_id,
        obligation_instance_id=None,
        evaluation_definition_hash=definition_hash,
        result=result,
        status="COMPLETED",
        metrics={k: v for k, v in outcome.metrics.items() if k != "_observation_ids"},
        observation_ids=observation_ids,
        evaluated_at=now,
        details={"reason": outcome.reason},
    )
    db.add(evaluation)
    await db.flush()

    if result == EvaluationResult.FAIL.name:
        await _open_exception(
            db,
            monitoring=monitoring,
            evaluation=evaluation,
            reason=outcome.reason or "Monitoring condition FAILed",
            metrics=outcome.metrics,
        )
        await _enqueue_monitoring_notification(
            db,
            monitoring=monitoring,
            event_type=EVENT_EVALUATION_FAILED,
            title_reason=outcome.reason or "Monitoring condition not met",
            notification_type="monitoring_alert",
            severity="FAIL",
        )
    elif result == EvaluationResult.PASS.name:
        notify = (monitoring.evaluation_definition or {}).get("notify_on_pass", False)
        if notify:
            await _enqueue_monitoring_notification(
                db,
                monitoring=monitoring,
                event_type=EVENT_EVALUATION_PASSED,
                title_reason="Monitoring evaluation PASS",
                notification_type="monitoring_summary",
                severity="PASS",
            )
    else:
        await _mark_inconclusive_health(db, monitoring.integration_id)

    await _audit_event(
        db,
        monitoring,
        action=f"monitoring.evaluation_{result.lower()}",
        resource_id=monitoring.id,
        metadata_json={"result": result, "reason": outcome.reason},
    )
    return evaluation


async def _mark_inconclusive_health(db: AsyncSession, integration_id: uuid.UUID) -> None:
    """A purely evaluative INCONCLUSIVE (e.g. field shape mismatch) still
    flips connector health to DEGRADED so operators notice the source
    is not yielding usable data."""
    integration = await db.get(IntegrationConnection, integration_id)
    if integration is not None and integration.status != IntegrationStatus.DEGRADED.value:
        integration.status = IntegrationStatus.DEGRADED.value
    health = await db.get(IntegrationHealth, integration_id)
    if health is None:
        db.add(
            IntegrationHealth(
                integration_id=integration_id,
                last_failure_at=now_utc(),
                consecutive_failures=1,
                last_error_code="EVALUATION_INCONCLUSIVE",
                last_error="Evaluation could not reach a verdict",
            )
        )


async def _open_exception(
    db: AsyncSession,
    *,
    monitoring: ObligationMonitoring,
    evaluation: MonitoringEvaluation,
    reason: str,
    metrics: dict,
) -> None:
    existing = await db.scalar(
        select(MonitoringException).where(
            MonitoringException.monitoring_id == monitoring.id,
            MonitoringException.status == "OPEN",
        )
    )
    if existing is not None:
        return
    db.add(
        MonitoringException(
            organization_id=monitoring.organization_id,
            monitoring_id=monitoring.id,
            evaluation_id=evaluation.id,
            status="OPEN",
            reason=reason[:4000],
            details={"metrics": {k: v for k, v in metrics.items() if k != "_observation_ids"}},
        )
    )
    await db.flush()


async def _enqueue_monitoring_notification(
    db: AsyncSession,
    *,
    monitoring: ObligationMonitoring,
    event_type: str,
    title_reason: str,
    notification_type: str,
    severity: str,
) -> None:
    """Best-effort operator notification via the domain outbox.

    Recipients: active organization members (any user who can log into the
    workspace). dispatch_event later mints the Notification row / WebSocket /
    push/SMS deliveries from the same event.
    """
    from app.models.organization import Organization
    from app.models.rbac import OrganizationMember
    from app.models.user import User

    member = await db.scalar(
        select(User)
        .join(OrganizationMember, OrganizationMember.user_id == User.id)
        .where(
            OrganizationMember.organization_id == monitoring.organization_id,
            OrganizationMember.status == "active",
        )
        .order_by(OrganizationMember.id.asc())
        .limit(1)
    )

    payload = {
        "obligation_id": str(monitoring.obligation_id),
        "monitoring_id": str(monitoring.id),
        "subject": f"Monitoring alert: obligation {monitoring.obligation_id}",
        "reason": title_reason,
        "severity": severity,
    }
    if member is not None:
        payload["recipient_email"] = member.email
        payload["recipient_user_id"] = str(member.id)

    await enqueue_event(
        db,
        tenant_id=monitoring.organization_id,
        event_type=event_type,
        aggregate_type="obligation_monitoring",
        aggregate_id=monitoring.id,
        payload=payload,
    )


async def _audit_event(
    db: AsyncSession,
    monitoring: ObligationMonitoring,
    *,
    action: str,
    resource_id: uuid.UUID,
    metadata_json: dict,
) -> None:
    try:
        await record_event(
            db,
            tenant_id=monitoring.organization_id,
            actor_id=None,
            actor_type="system",
            action=action,
            resource_type="monitoring_evaluation",
            resource_id=resource_id,
            metadata_json=metadata_json,
        )
    except Exception:  # noqa: BLE001 — audit must never break the monitoring run
        _log.warning("audit record failed for monitoring %s: %s", monitoring.id, action)


def hash_definition(monitoring: ObligationMonitoring) -> str:
    raw = canon_json(
        {
            "query": monitoring.query_definition,
            "evaluation": monitoring.evaluation_definition,
            "schedule": monitoring.schedule_definition,
        }
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Sweep + version-staleness
# --------------------------------------------------------------------------

async def schedule_due_monitorings(
    db: AsyncSession,
    *,
    limit: int = 50,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    """Run every ACTIVE monitoring whose next_run_at is due.

    Uses ``FOR UPDATE SKIP LOCKED`` on Postgres so scheduler workers do not
    stampede; on SQLite the modifier is a no-op, keeping tests deterministic.
    """
    now = now or now_utc()
    rows = await db.execute(
        select(ObligationMonitoring)
        .where(
            ObligationMonitoring.status == "ACTIVE",
            ObligationMonitoring.next_run_at.is_not(None),
            ObligationMonitoring.next_run_at <= now,
        )
        .order_by(ObligationMonitoring.next_run_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    due = list(rows.scalars().all())
    evaluation_ids: list[uuid.UUID] = []
    for monitoring in due:
        evaluation = await run_obligation_monitoring(db, monitoring=monitoring, now=now)
        evaluation_ids.append(evaluation.id)
    await db.flush()
    return evaluation_ids


# --------------------------------------------------------------------------
# Webhook ingestion (spec 3.15.28-3.15.30)
# --------------------------------------------------------------------------

async def ingest_webhook_observations(
    db: AsyncSession,
    *,
    integration_id: uuid.UUID,
    payload: dict,
    provider_event_id: str | None,
) -> int:
    """Apply a verified provider webhook payload to every ACTIVE monitoring
    bound to the integration.

    Each monitoring's ``query_definition`` declares how the payload maps to
    an observation (``external_id_path`` / ``observed_at_path``). Payload
    hashes + the 5-part unique constraint deduplicate replays.
    """
    from app.monitoring.connectors.base import ExternalObservation
    from app.monitoring.evaluators.base import resolve_path
    from app.monitoring.models import MonitoringWebhookEvent, ObligationMonitoring

    integration = await db.get(IntegrationConnection, integration_id)
    if integration is None:
        return 0

    monitorings = (
        await db.execute(
            select(ObligationMonitoring).where(
                ObligationMonitoring.integration_id == integration.id,
                ObligationMonitoring.status == "ACTIVE",
            )
        )
    ).scalars().all()

    event_row = MonitoringWebhookEvent(
        organization_id=integration.organization_id,
        integration_id=integration.id,
        provider_event_id=str(provider_event_id)[:255] if provider_event_id else None,
        signature=None,
        received_at=now_utc(),
        payload_hash=hash_payload(payload),
        payload=payload,
        status="ACCEPTED",
    )
    db.add(event_row)
    await db.flush()

    total = 0
    for monitoring in monitorings:
        query = monitoring.query_definition or {}
        external_id_path = query.get("external_id_path") or "id"
        observed_at_path = query.get("observed_at_path") or "observed_at"
        resource_type = query.get("resource") or "webhook"

        try:
            external_id = resolve_path(payload, external_id_path)
        except Exception:
            external_id = provider_event_id or "webhook"
        try:
            observed_at = ensure_aware_utc(resolve_path(payload, observed_at_path))
        except Exception:
            observed_at = now_utc()

        observations = [
            ExternalObservation(
                external_id=str(external_id)[:500],
                observed_at=observed_at,
                resource_type=str(resource_type)[:255],
                payload=payload,
                source_reference={
                    "provider_key": integration.provider_key,
                    "webhook": True,
                    "webhook_event_id": str(event_row.id),
                },
            )
        ]
        created = await persist_observations(
            db,
            organization_id=monitoring.organization_id,
            integration_id=integration.id,
            monitoring_id=monitoring.id,
            observations=observations,
        )
        total += len(created)
    await db.flush()
    return total


async def detect_stale_monitorings(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    """Pause ACTIVE monitorings whose source agreement was remapped.

    A rule that still points at an old AgreementVersion is automatically
    PAUSED with ``pause_reason = STALE_VERSION`` and its owner notified; a
    human re-activates after remapping (spec 3.15.58).
    """
    from app.models.agreement import AgreementVersion
    from app.models.obligation import Obligation

    rows = (
        await db.execute(
            select(ObligationMonitoring, Obligation)
            .join(Obligation, Obligation.id == ObligationMonitoring.obligation_id)
            .where(ObligationMonitoring.status == MonitoringStatus.ACTIVE.value)
        )
    ).all()

    paused = 0
    for monitoring, obligation in rows:
        latest = await db.scalar(
            select(AgreementVersion.id)
            .where(AgreementVersion.agreement_id == obligation.agreement_id)
            .order_by(AgreementVersion.version_number.desc())
            .limit(1)
        )
        if latest is None or monitoring.source_version_id == latest:
            continue
        monitoring.status = MonitoringStatus.PAUSED.value
        monitoring.pause_reason = PauseReason.STALE_VERSION.value
        await _enqueue_monitoring_notification(
            db,
            monitoring=monitoring,
            event_type=EVENT_STALE_VERSION,
            title_reason=(
                "Monitoring source agreement was revised; rule paused pending remap"
            ),
            notification_type="monitoring_summary",
            severity="STALE_VERSION",
        )
        await _audit_event(
            db,
            monitoring,
            action="monitoring.paused_stale_version",
            resource_id=monitoring.id,
            metadata_json={"latest_version_id": str(latest)},
        )
        paused += 1
    if paused:
        await db.flush()
    return paused