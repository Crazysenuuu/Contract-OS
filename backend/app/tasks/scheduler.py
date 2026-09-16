import asyncio
import threading

from app.worker import celery_app
from celery.schedules import crontab

# Configure Celery Beat schedule
celery_app.conf.beat_schedule = {
    # Check for upcoming obligations every day at 8:00 AM
    'send-daily-obligation-reminders': {
        'task': 'app.tasks.scheduler.send_daily_obligation_reminders',
        'schedule': crontab(hour=8, minute=0),
    },
    # Flush pending outbox events to notifications + real-time pushes
    'process-outbox-events': {
        'task': 'app.tasks.scheduler.process_outbox_events',
        'schedule': crontab(minute='*'),
    },
    # Materialise deadlines for recurring obligations that come due today
    'materialise-recurring-obligations': {
        'task': 'app.tasks.scheduler.materialise_recurring_obligations',
        'schedule': crontab(hour=2, minute=0),
    },
    # Hourly: expire agreements whose renewal window passed with no renewal
    # (spec 1.17.21 expiration scheduler) and flag soon-to-expire ones.
    'process-expiration-sweep': {
        'task': 'app.tasks.scheduler.process_expiration_sweep',
        'schedule': crontab(minute=0),
    },
    # Daily 03:00: retention worker - apply disposition to expired retention
    # records, skipping legal holds (spec 2.07.37).
    'run-retention-worker': {
        'task': 'app.tasks.scheduler.run_retention_worker_task',
        'schedule': crontab(hour=3, minute=0),
    },
    # ── NEW PHASE-2 TASKS ──────────────────────────────────────────────────
    # Every minute: retry pending outbound webhook deliveries.
    'send-pending-webhooks': {
        'task': 'app.tasks.scheduler.send_pending_webhooks',
        'schedule': crontab(minute='*'),
    },
    # Hourly: flag agreements that have breached SLA thresholds.
    'sweep-sla-violations': {
        'task': 'app.tasks.scheduler.sweep_sla_violations',
        'schedule': crontab(minute=0),
    },
    # Daily 07:00: notify when counterparty insurance cover is expiring.
    'sweep-insurance-expiry': {
        'task': 'app.tasks.scheduler.sweep_insurance_expiry',
        'schedule': crontab(hour=7, minute=0),
    },
    # Every 5 minutes: push document content changes into the search index.
    'index-pending-documents': {
        'task': 'app.tasks.scheduler.index_pending_documents',
        'schedule': crontab(minute='*/5'),
    },
    # Daily 04:00: roll up dashboard analytics aggregates.
    'aggregate-analytics': {
        'task': 'app.tasks.scheduler.aggregate_analytics',
        'schedule': crontab(hour=4, minute=0),
    },
    # Daily 08:30: send renewal-window notices (spec 1.17 renewal workflow).
    'send-renewal-notices': {
        'task': 'app.tasks.scheduler.send_renewal_notices',
        'schedule': crontab(hour=8, minute=30),
    },
}


def _run_async(coro_factory):
    """Run an async coroutine from a sync Celery task.

    Uses ``asyncio.run`` when no loop is running (normal worker execution).
    If a loop is already running (e.g. pytest's eager mode, or a task
    invoked from inside an async context), executes the coroutine in a
    fresh thread with its own loop so the caller is never blocked.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    box: dict = {}

    def _target():
        try:
            box["result"] = asyncio.run(coro_factory())
        except BaseException as exc:  # noqa: BLE001 - re-raised in caller thread
            box["error"] = exc

    thread = threading.Thread(target=_target)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["result"]


@celery_app.task(name="app.tasks.scheduler.send_daily_obligation_reminders")
def send_daily_obligation_reminders():
    """Send email reminders for obligations due within the next 7 days."""
    from app.core.database import AsyncSessionLocal
    from app.services.obligation_reminder_service import (
        dispatch_obligation_reminders,
    )

    async def _run():
        async with AsyncSessionLocal() as db:
            try:
                result = await dispatch_obligation_reminders(db)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    return _run_async(_run)


@celery_app.task(name="app.tasks.scheduler.materialise_recurring_obligations")
def materialise_recurring_obligations(up_to: str | None = None):
    """Materialise recurring obligation deadlines that fall due by ``up_to``
    (ISO date, default today). Idempotent per deadline because each
    recurrence records how far it has been materialised."""
    from datetime import date

    from app.core.database import AsyncSessionLocal
    from app.services.recurrence_service import materialise_pending_instances

    horizon = date.fromisoformat(up_to) if up_to else None

    async def _run():
        async with AsyncSessionLocal() as db:
            try:
                result = await materialise_pending_instances(db, up_to=horizon)
                await db.commit()
                return {"created": result}
            except Exception:
                await db.rollback()
                raise

    return _run_async(_run)


@celery_app.task(name="app.tasks.scheduler.process_outbox_events")
def process_outbox_events(limit: int = 50):
    """
    Publish + deliver pending outbox events (idempotent).

    Creates Notification records and pushes real-time updates over
    WebSocket to connected users. Safe to run on an interval: already
    published events are skipped, failed events retry with backoff.
    """
    from app.core.database import AsyncSessionLocal
    from app.services.event_outbox_service import process_pending_events

    async def _run():
        async with AsyncSessionLocal() as db:
            try:
                result = await process_pending_events(db, limit=limit)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    return _run_async(_run)


@celery_app.task(name="app.tasks.scheduler.process_expiration_sweep")
def process_expiration_sweep():
    """Expiration sweep (spec 1.17.21).

    For every renewable agreement whose current_expiry_date has passed with
    no renewal and no non-renewal notice honoured, expire it through the
    lifecycle engine. Idempotent: already-expired renewals are skipped by
    the service layer.
    """
    from datetime import date

    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.agreement import Agreement
    from app.models.renewal import ContractRenewal
    from app.services import renewal_service
    from app.services.lifecycle_service import apply_transition

    async def _run():
        today = date.today()
        processed = {"renewed": 0, "expired": 0, "errors": 0}
        async with AsyncSessionLocal() as db:
            try:
                result = await db.execute(
                    select(ContractRenewal, Agreement)
                    .join(Agreement, Agreement.id == ContractRenewal.agreement_id)
                    .where(
                        ContractRenewal.status.notin_(("expired", "terminated", "cancelled")),
                        ContractRenewal.current_expiry_date.is_not(None),
                        ContractRenewal.current_expiry_date <= today,
                    )
                )
                rows = result.all()
                for renewal, agreement in rows:
                    try:
                        summary = await renewal_service.process_renewal(
                            db,
                            renewal=renewal,
                            agreement=agreement,
                            org_id=agreement.organization_id,
                            actor_id=None,
                        )
                        action = (
                            "renew" if summary.get("action") == "renewed" else "expire"
                        )
                        try:
                            await apply_transition(
                                db,
                                agreement=agreement,
                                action_key=action,
                                actor_id=None,
                                org_id=agreement.organization_id,
                                actor_type="system",
                                metadata_json={
                                    "source": "expiration_sweep",
                                    "renewal_id": str(renewal.id),
                                },
                            )
                        except Exception:
                            # Transition rules may not define renew/expire for
                            # this type/state; the renewal record itself is
                            # still updated, which is the legally meaningful
                            # part. Status transitions also happen via APIs.
                            pass
                        processed[
                            "renewed" if summary.get("action") == "renewed" else "expired"
                        ] += 1
                    except Exception:
                        processed["errors"] += 1
                await db.commit()
                return processed
            except Exception:
                await db.rollback()
                raise

    return _run_async(_run)


# ── Phase-2 tasks ──────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.scheduler.send_pending_webhooks")
def send_pending_webhooks():
    """Flush pending outbound webhook deliveries with exponential backoff (spec 1.13)."""
    from app.core.database import AsyncSessionLocal

    async def _run():
        async with AsyncSessionLocal() as db:
            try:
                from app.services.webhook_service import flush_pending_webhooks
                result = await flush_pending_webhooks(db)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    return _run_async(_run)


@celery_app.task(name="app.tasks.scheduler.sweep_sla_violations")
def sweep_sla_violations():
    """Detect agreements that have breached SLA thresholds and create violation records."""
    from app.core.database import AsyncSessionLocal

    async def _run():
        async with AsyncSessionLocal() as db:
            try:
                from app.services.sla_service import detect_sla_violations
                result = await detect_sla_violations(db)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    return _run_async(_run)


@celery_app.task(name="app.tasks.scheduler.sweep_insurance_expiry")
def sweep_insurance_expiry():
    """Notify parties when counterparty insurance/bonding documents are near expiry."""
    from app.core.database import AsyncSessionLocal

    async def _run():
        async with AsyncSessionLocal() as db:
            try:
                from app.services.insurance_service import notify_insurance_expiry
                result = await notify_insurance_expiry(db)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    return _run_async(_run)


@celery_app.task(name="app.tasks.scheduler.index_pending_documents")
def index_pending_documents():
    """Push unindexed or updated document content into the full-text search index."""
    from app.core.database import AsyncSessionLocal

    async def _run():
        async with AsyncSessionLocal() as db:
            try:
                from app.services.search_index_service import sync_pending_documents
                result = await sync_pending_documents(db)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    return _run_async(_run)


@celery_app.task(name="app.tasks.scheduler.aggregate_analytics")
def aggregate_analytics():
    """Roll up raw events into the daily analytics aggregates for dashboards."""
    from app.core.database import AsyncSessionLocal

    async def _run():
        async with AsyncSessionLocal() as db:
            try:
                from app.services.analytics_service import run_daily_aggregation
                result = await run_daily_aggregation(db)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    return _run_async(_run)


@celery_app.task(name="app.tasks.scheduler.send_renewal_notices")
def send_renewal_notices():
    """Send renewal-window notification emails for agreements entering their renewal period."""
    from app.core.database import AsyncSessionLocal

    async def _run():
        async with AsyncSessionLocal() as db:
            try:
                from app.services.renewal_service import dispatch_renewal_notices
                result = await dispatch_renewal_notices(db)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    return _run_async(_run)

@celery_app.task(name="app.tasks.scheduler.run_retention_worker_task")
def run_retention_worker_task(dry_run: bool = False):

    """"Retention disposition worker (spec 2.07.37).

    Applies retention policy to records past their retention date. Legal
    holds are always respected. Runs as a daily beat task; also exposed via
    the admin API for on-demand runs.
    """""
    from app.core.database import AsyncSessionLocal
    from app.services.retention_service import run_retention_worker

    async def _run():
        async with AsyncSessionLocal() as db:
            try:
                result = await run_retention_worker(db, org_id=None, dry_run=dry_run)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    return _run_async(_run)