"""Celery wiring for the monitoring sweep (spec 3.15.31-3.15.33).

The beat task is a synchronous shim around the async service, matching the
pattern used by every other schedule in ``app/tasks/scheduler.py``.
"""

from app.monitoring.scheduler import detect_stale, run_sweep
from app.worker import celery_app


@celery_app.task(name="app.monitoring.tasks.run_monitoring_sweep")
def run_monitoring_sweep(limit: int = 50):
    """Run due obligation-monitorings and pause stale-version rules."""
    return run_sweep(limit=limit)


@celery_app.task(name="app.monitoring.tasks.detect_stale_monitorings_task")
def detect_stale_monitorings_task():
    """Pause monitorings whose source agreement version was superseded."""
    return detect_stale()