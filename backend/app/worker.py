import os
from celery import Celery

# Set default settings module
os.environ.setdefault("CELERY_CONFIG_MODULE", "app.core.config")

# Initialize Celery
redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
broker_url = os.environ.get("CELERY_BROKER_URL", os.environ.get("REDIS_URL", redis_url))

celery_app = Celery(
    "contractos_worker",
    broker=broker_url,
    backend=os.environ.get("CELERY_RESULT_BACKEND", broker_url),
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Colombo",
    enable_utc=True,
    task_always_eager=os.environ.get("CELERY_TASK_ALWAYS_EAGER", "false").lower() == "true",
    task_eager_propagates=os.environ.get("CELERY_TASK_EAGER_PROPAGATES", "true").lower() == "true",
)

# Import task modules eagerly so both `celery worker` and `celery beat`
# register every task and the beat_schedule. Relying on Celery's `include`
# alone leaves `celery beat -A app.worker.celery_app` with an empty schedule
# because beat never imports the task modules itself.
import app.tasks.email_tasks  # noqa: E402,F401
import app.tasks.scheduler  # noqa: E402,F401