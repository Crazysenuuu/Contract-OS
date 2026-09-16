"""Scheduler coverage tests (spec 1.17.21 / 2.07.37).

Verifies the Celery beat schedule includes the expiration sweep and the
retention worker, and that both tasks are importable and registered.
"""

from app.tasks.scheduler import (
    celery_app,
    process_expiration_sweep,
    run_retention_worker_task,
)


def test_beat_schedule_includes_expiration_sweep():
    schedule = celery_app.conf.beat_schedule
    assert "process-expiration-sweep" in schedule
    entry = schedule["process-expiration-sweep"]
    assert entry["task"] == "app.tasks.scheduler.process_expiration_sweep"
    # Hourly sweep: expiration must not wait a day past the expiry date.
    assert entry["schedule"].minute == {0}


def test_beat_schedule_includes_retention_worker():
    schedule = celery_app.conf.beat_schedule
    assert "run-retention-worker" in schedule
    assert (
        schedule["run-retention-worker"]["task"]
        == "app.tasks.scheduler.run_retention_worker_task"
    )


def test_tasks_are_registered_with_celery():
    registered = set(celery_app.tasks.keys())
    assert "app.tasks.scheduler.process_expiration_sweep" in registered
    assert "app.tasks.scheduler.run_retention_worker_task" in registered
