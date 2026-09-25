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
    MonitoringEvidence,
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
EVENT_OBLIGATION_AUTOMATION_TRIGGERED = "obligation.automation_triggered"

# Lifecycle audit tokens (spec 3.15.47) — all flow through the 3.11 hash chain.
AUDIT_INTEGRATION_CREATED = "INTEGRATION_CREATED"
AUDIT_INTEGRATION_UPDATED = "INTEGRATION_UPDATED"
AUDIT_INTEGRATION_CONNECTED = "INTEGRATION_CONNECTED"
AUDIT_INTEGRATION_DISCONNECTED = "INTEGRATION_DISCONNECTED"
AUDIT_INTEGRATION_CREDENTIAL_ROTATED = "INTEGRATION_CREDENTIAL_ROTATED"
AUDIT_INTEGRATION_AUTH_FAILED = "INTEGRATION_AUTH_FAILED"
AUDIT_MONITORING_CREATED = "MONITORING_CREATED"
AUDIT_MONITORING_ACTIVATED = "MONITORING_ACTIVATED"
AUDIT_MONITORING_PAUSED = "MONITORING_PAUSED"
AUDIT_MONITORING_DISABLED = "MONITORING_DISABLED"
AUDIT_RUN_STARTED = "MONITORING_RUN_STARTED"
AUDIT_RUN_COMPLETED = "MONITORING_RUN_COMPLETED"
AUDIT_RUN_FAILED = "MONITORING_RUN_FAILED"
AUDIT_OBSERVATION_RECEIVED = "EXTERNAL_OBSERVATION_RECEIVED"
AUDIT_OBSERVATION_REJECTED = "EXTERNAL_OBSERVATION_REJECTED"
AUDIT_EVALUATION_COMPLETED = "MONITORING_EVALUATION_COMPLETED"
AUDIT_EXCEPTION_OPENED = "MONITORING_EXCEPTION_OPENED"
AUDIT_EXCEPTION_RESOLVED = "MONITORING_EXCEPTION_RESOLVED"
AUDIT_AUTOMATION_TRIGGERED = "OBLIGATION_AUTOMATION_TRIGGERED"

# Conservative automation actions (spec 3.15.36).
_AUTOMATION_ACTIONS = ("NO_ACTION", "MARK_TASK_READY", "ATTACH_EVIDENCE", "COMPLETE_TASK")
_DEFAULT_RISK_THRESHOLD = 3


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
    else:
        await _audit_event(
            db,
            monitoring,
            action=AUDIT_RUN_STARTED,
            resource_id=run.id,
            resource_type="monitoring_run",
            metadata_json={"run_id": str(run.id), "scheduled_period": run.scheduled_period},
        )

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
        _finalize_run(db, run, monitoring, evaluation, now, outcome=None)
        await _audit_event(
            db,
            monitoring,
            action=AUDIT_RUN_FAILED,
            resource_id=run.id,
            resource_type="monitoring_run",
            metadata_json={"run_id": str(run.id), "exception_type": type(exc).__name__},
        )
        return evaluation

    evaluation = await _record_evaluation(
        db,
        monitoring=monitoring,
        run_id=run.id,
        definition_hash=definition_hash,
        outcome=outcome,
        observation_ids=outcome.metrics.get("_observation_ids", []),
        now=now,
    )
    _finalize_run(db, run, monitoring, evaluation, now, outcome=outcome)
    await _audit_event(
        db,
        monitoring,
        action=AUDIT_RUN_COMPLETED,
        resource_id=run.id,
        resource_type="monitoring_run",
        metadata_json={"run_id": str(run.id), "result": evaluation.result},
    )
    return evaluation


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
        observation_ids=[str(x) for x in observation_ids if x is not None],
        evaluated_at=now,
        details={"reason": outcome.reason},
    )
    db.add(evaluation)
    await db.flush()

    evidence_rows = await _persist_evidence_records(
        db,
        monitoring=monitoring,
        run_id=run_id,
        observation_ids=observation_ids,
        now=now,
    )

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
        await _maybe_raise_risk_finding(db, monitoring=monitoring, now=now)
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
        action = _automation_action(monitoring)
        if action != "NO_ACTION":
            await _apply_automation(
                db,
                monitoring=monitoring,
                action=action,
                evidence=evidence_rows,
                now=now,
            )
    else:
        await _mark_inconclusive_health(db, monitoring.integration_id)

    await _audit_event(
        db,
        monitoring,
        action=AUDIT_EVALUATION_COMPLETED,
        resource_id=monitoring.id,
        metadata_json={"result": result, "reason": outcome.reason},
    )
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
    await _audit_event(
        db,
        monitoring,
        action=AUDIT_EXCEPTION_OPENED,
        resource_id=monitoring.id,
        resource_type="monitoring_exception",
        metadata_json={"evaluation_id": str(evaluation.id), "reason": reason[:4000]},
    )


def _automation_action(monitoring: ObligationMonitoring) -> str:
    automation = (monitoring.automation or {}).get("on_pass")
    action = (automation or "NO_ACTION").upper()
    return action if action in _AUTOMATION_ACTIONS else "NO_ACTION"


async def _persist_evidence_records(
    db: AsyncSession,
    *,
    monitoring: ObligationMonitoring,
    run_id: uuid.UUID,
    observation_ids: list,
    now: datetime,
) -> list[MonitoringEvidence]:
    """Materialise one evidence record per newly stored observation (3.15.37).

    Each record makes the run reconstructable: source, source identifier,
    observed_at, received_at, payload hash, integration ID and monitoring run
    ID (3.15.38). Deduplication is inherent — a persisted observation has a
    stable id and observations were already deduped on insert.
    """
    from app.monitoring.connectors.base import (
        ExternalObservation,
    )  # noqa: F401  (kept import crisp for error surfacing)

    from app.monitoring.models import ExternalObservationRecord

    if not observation_ids:
        return []

    integration = await db.get(IntegrationConnection, monitoring.integration_id)
    source = integration.provider_key if integration is not None else str(monitoring.integration_id)

    records = (
        (await db.execute(
            select(ExternalObservationRecord).where(
                ExternalObservationRecord.id.in_(observation_ids)
            )
        ))
        .scalars()
        .all()
    )

    created: list[MonitoringEvidence] = []
    for row in records:
        evidence = MonitoringEvidence(
            organization_id=monitoring.organization_id,
            monitoring_id=monitoring.id,
            integration_id=monitoring.integration_id,
            monitoring_run_id=run_id,
            obligation_id=monitoring.obligation_id,
            evidence_type="SYSTEM_RECORD",
            source=str(source)[:255],
            source_identifier=row.external_id[:500],
            observed_at=ensure_aware_utc(row.observed_at),
            received_at=now,
            payload_hash=row.payload_hash,
            value={
                "integration_id": str(row.integration_id),
                "external_id": row.external_id,
                "observation_id": str(row.id),
                "payload_hash": row.payload_hash,
            },
            attached_to_obligation=False,
        )
        db.add(evidence)
        created.append(evidence)
    if created:
        await db.flush()
        await _audit_event(
            db,
            monitoring,
            action=AUDIT_OBSERVATION_RECEIVED,
            resource_id=monitoring.id,
            metadata_json={
                "count": len(created),
                "run_id": str(run_id) if run_id else None,
            },
        )
    return created


async def _apply_automation(
    db: AsyncSession,
    *,
    monitoring: ObligationMonitoring,
    action: str,
    evidence: list[MonitoringEvidence],
    now: datetime,
) -> None:
    """Conservative automatic task advancement on PASS (3.15.36).

    A PASS may only advance a task when the workspace explicitly configured
    the rule to do so — never a universal auto-complete of the obligation
    itself (3.15.36, 3.15.43). COMPLETE_TASK completes the obligation's
    workflow task rows, NOT the obligation record.
    """
    from app.models.obligation import Obligation, ObligationEvidence
    from app.models.user_task import UserTask

    obligation = await db.get(Obligation, monitoring.obligation_id)
    applied = False

    if action in ("MARK_TASK_READY", "COMPLETE_TASK") and obligation is not None:
        tasks = (
            (await db.execute(
                select(UserTask).where(
                    UserTask.organization_id == monitoring.organization_id,
                    UserTask.agreement_id == obligation.agreement_id,
                    UserTask.task_type.in_(["obligation", "review", "compliance"]),
                    UserTask.status.in_(["pending", "in_progress"]),
                )
            ))
            .scalars()
            .all()
        )
        for task in tasks:
            if action == "MARK_TASK_READY" and task.status == "pending":
                task.status = "in_progress"
                applied = True
            elif action == "COMPLETE_TASK":
                task.status = "completed"
                task.completed_at = now
                applied = True

    if action == "ATTACH_EVIDENCE":
        integration = await db.get(IntegrationConnection, monitoring.integration_id)
        submitted_by = integration.created_by if integration is not None else None
        attached = 0
        for ev in evidence:
            ev.attached_to_obligation = True
            attached += 1
        if (
            attached
            and submitted_by is not None
            and obligation is not None
        ):
            db.add(
                ObligationEvidence(
                    obligation_id=obligation.id,
                    document_id=None,
                    description=(
                        f"System evidence from monitoring rule {monitoring.id}: "
                        f"{attached} external record(s)"
                    ),
                    submitted_by=submitted_by,
                    status="SUBMITTED",
                    submitted_at=now,
                )
            )
        applied = True
        await db.flush()

    if applied:
        await _enqueue_monitoring_notification(
            db,
            monitoring=monitoring,
            event_type=EVENT_OBLIGATION_AUTOMATION_TRIGGERED,
            title_reason=f"Automation {action} applied after PASS",
            notification_type="monitoring_summary",
            severity="PASS",
        )
        await _audit_event(
            db,
            monitoring,
            action=AUDIT_AUTOMATION_TRIGGERED,
            resource_id=monitoring.id,
            metadata_json={
                "obligation_id": str(monitoring.obligation_id),
                "automation": action,
            },
        )


async def _maybe_raise_risk_finding(
    db: AsyncSession,
    *,
    monitoring: ObligationMonitoring,
    now: datetime,
):
    """Escalate repeated FAIL into a monitoring-derived risk (3.15.50-51).

    Once the number of consecutive FAIL evaluations reaches the rule's
    ``risk_threshold`` (default 3), a RiskFinding is minted whose evidence
    metadata traces back to obligation, monitoring and the exact evaluation
    ids — the risk engine can pull the underlying monitoring evidence.
    A pending/accepted finding for the same monitoring suppresses duplicates.
    """
    from app.models.ai_analysis import RiskFinding
    from app.models.obligation import Obligation

    obligation = await db.get(Obligation, monitoring.obligation_id)
    if obligation is None:
        return None

    threshold = int(
        (monitoring.evaluation_definition or {}).get("risk_threshold", _DEFAULT_RISK_THRESHOLD)
        or _DEFAULT_RISK_THRESHOLD
    )
    threshold = max(1, threshold)

    recent = (
        (await db.execute(
            select(MonitoringEvaluation)
            .where(MonitoringEvaluation.monitoring_id == monitoring.id)
            .order_by(MonitoringEvaluation.evaluated_at.desc())
            .limit(threshold)
        ))
        .scalars()
        .all()
    )
    consecutive = 0
    for evaluation in recent:
        if evaluation.result == EvaluationResult.FAIL.value:
            consecutive += 1
        else:
            break
    if consecutive < threshold:
        return None

    existing = (
        (await db.execute(
            select(RiskFinding).where(RiskFinding.agreement_id == obligation.agreement_id)
        ))
        .scalars()
        .all()
    )
    for finding in existing:
        meta = finding.evidence or {}
        if (
            meta.get("source_type") == "OBLIGATION_ANALYSIS"
            and str(meta.get("monitoring_id", "")) == str(monitoring.id)
            and finding.reviewer_status in ("pending", "accepted")
        ):
            return None

    version_id = obligation.source_version_id or monitoring.source_version_id
    if version_id is None:
        return None

    failed_ids = (
        (await db.execute(
            select(MonitoringEvaluation.id)
            .where(
                MonitoringEvaluation.monitoring_id == monitoring.id,
                MonitoringEvaluation.result == EvaluationResult.FAIL.value,
            )
            .order_by(MonitoringEvaluation.evaluated_at.desc())
            .limit(threshold)
        ))
        .scalars()
        .all()
    )

    finding = RiskFinding(
        agreement_id=obligation.agreement_id,
        version_id=version_id,
        category="monitoring",
        severity="HIGH",
        finding="Repeated external monitoring failures",
        explanation=(
            f"The monitoring rule {monitoring.id} produced {len(failed_ids)} consecutive "
            f"FAIL evaluations for obligation {obligation.id}."
        ),
        recommendation=(
            "Review the external source data and the obligation's operational performance."
        ),
        evidence={
            "source_type": "OBLIGATION_ANALYSIS",
            "obligation_id": str(obligation.id),
            "monitoring_id": str(monitoring.id),
            "evaluation_ids": [str(eid) for eid in failed_ids],
        },
        confidence=0.9,
        reviewer_status="pending",
    )
    db.add(finding)
    await db.flush()
    return finding


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

    Recipient resolution (spec 3.15.49), in priority order:
      1. obligation owner — the obligation's primary assignee user
      2. integration owner — the workspace member who created the integration
      3. workspace notification policy — any active organization member
    All resolved recipients are reported in ``recipients`` so the delivery
    layer can fan out; the first keeps the legacy single-recipient fields
    (``recipient_email`` / ``recipient_user_id``) compatible.
    """
    from app.models.obligation import Obligation, ObligationAssignee
    from app.models.rbac import OrganizationMember
    from app.models.user import User

    recipients: list[dict] = []

    async def _with_user(user_id: uuid.UUID, role: str) -> None:
        user = await db.get(User, user_id)
        if user is not None:
            recipients.append({"user_id": str(user.id), "email": user.email, "role": role})

    obligation = await db.get(Obligation, monitoring.obligation_id)
    if obligation is not None:
        assignee = await db.scalar(
            select(ObligationAssignee)
            .where(
                ObligationAssignee.obligation_id == obligation.id,
                ObligationAssignee.member_id.is_not(None),
                ObligationAssignee.primary_assignee.is_(True),
            )
            .order_by(ObligationAssignee.responsibility_type.asc())
            .limit(1)
        )
        if assignee is not None and assignee.member_id is not None:
            await _with_user(assignee.member_id, "obligation_owner")

    if not recipients:
        integration = await db.get(IntegrationConnection, monitoring.integration_id)
        if integration is not None and integration.created_by is not None:
            await _with_user(integration.created_by, "integration_owner")

    if not recipients:
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
        if member is not None:
            recipients.append(
                {"user_id": str(member.id), "email": member.email, "role": "workspace"}
            )

    payload = {
        "obligation_id": str(monitoring.obligation_id),
        "monitoring_id": str(monitoring.id),
        "subject": f"Monitoring alert: obligation {monitoring.obligation_id}",
        "reason": title_reason,
        "severity": severity,
        "recipients": recipients,
    }
    if recipients:
        payload["recipient_email"] = recipients[0]["email"]
        payload["recipient_user_id"] = recipients[0]["user_id"]

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
    resource_type: str = "monitoring_evaluation",
    actor_id: uuid.UUID | None = None,
    actor_type: str = "system",
) -> None:
    try:
        meta = dict(metadata_json)
        await record_event(
            db,
            tenant_id=monitoring.organization_id,
            agreement_id=meta.pop("agreement_id", None),
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_json=meta,
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
    from app.monitoring.models import (
        ExternalObservationRecord,
        MonitoringWebhookEvent,
        ObligationMonitoring,
    )

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
        if created:
            # Evidence records with source/source-id/observed_at/received_at/
            # payload_hash/integration_id; no monitoring_run_id here because a
            # webhook does not mint a run (3.15.38).
            records = (
                (
                    await db.execute(
                        select(ExternalObservationRecord).where(
                            ExternalObservationRecord.id.in_(created)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in records:
                db.add(
                    MonitoringEvidence(
                        organization_id=monitoring.organization_id,
                        monitoring_id=monitoring.id,
                        integration_id=integration.id,
                        monitoring_run_id=None,
                        obligation_id=monitoring.obligation_id,
                        evidence_type="SYSTEM_RECORD",
                        source=integration.provider_key[:255],
                        source_identifier=row.external_id[:500],
                        observed_at=ensure_aware_utc(row.observed_at),
                        received_at=now_utc(),
                        payload_hash=row.payload_hash,
                        value={
                            "integration_id": str(integration.id),
                            "external_id": row.external_id,
                            "observation_id": str(row.id),
                            "payload_hash": row.payload_hash,
                        },
                        attached_to_obligation=False,
                    )
                )
            await _audit_event(
                db,
                monitoring,
                action=AUDIT_OBSERVATION_RECEIVED,
                resource_id=monitoring.id,
                metadata_json={"count": len(created), "webhook_event_id": str(event_row.id)},
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