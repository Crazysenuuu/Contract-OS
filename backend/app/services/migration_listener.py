"""
Migration Event Listener.

Automatically tracks migration events using Alembic event hooks.
"""
import logging
from alembic import event
from alembic.runtime.migration import MigrationContext

from app.services.migration_monitor import get_migration_monitor

logger = logging.getLogger(__name__)

# Track current migration context
_current_context = None


@event.listens_for(MigrationContext, "before_migrate")
def before_migrate(context, revision, heads):
    """Called before each migration step."""
    global _current_context
    _current_context = context
    monitor = get_migration_monitor()
    # Event will be recorded when migration actually starts


@event.listens_for(MigrationContext, "after_migrate")
def after_migrate(context, revision, heads):
    """Called after each migration step."""
    monitor = get_migration_monitor()
    # Migration completed successfully
    logger.info(f"Migration step completed: {revision}")


def wrap_upgrade(func):
    """Decorator to wrap alembic upgrade with monitoring."""
    def wrapper(*args, **kwargs):
        monitor = get_migration_monitor()
        event = monitor.start_migration(
            event_type="upgrade",
            migration_id=str(args[0]) if args else "unknown",
            migration_name="upgrade",
            database_name="contractos",
        )
        try:
            result = func(*args, **kwargs)
            monitor.complete_migration(event, success=True)
            return result
        except Exception as e:
            monitor.complete_migration(
                event,
                success=False,
                error_message=str(e),
            )
            raise
    return wrapper


def wrap_downgrade(func):
    """Decorator to wrap alembic downgrade with monitoring."""
    def wrapper(*args, **kwargs):
        monitor = get_migration_monitor()
        event = monitor.start_migration(
            event_type="downgrade",
            migration_id=str(args[0]) if args else "unknown",
            migration_name="downgrade",
            database_name="contractos",
        )
        try:
            result = func(*args, **kwargs)
            monitor.complete_migration(event, success=True)
            monitor.record_rollback(event)
            return result
        except Exception as e:
            monitor.complete_migration(
                event,
                success=False,
                error_message=str(e),
            )
            raise
    return wrapper
