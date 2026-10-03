from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings_lazy

settings = get_settings_lazy()


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


# SQLAlchemy's pool sizing arguments only exist on pooled dialects. SQLite
# (tests, local dev, single-file deployments) uses StaticPool/NullPool and
# raises if handed pool_size/max_overflow, so the arguments are dropped there
# rather than special-cased per call site.
_pool_kwargs: dict = {}
if not _is_sqlite(settings.database_url):
    _pool_kwargs = {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout_seconds,
        # Sized deliberately below the server's max_connections: every API
        # replica and Celery worker opens its own pool, and exhausting
        # Postgres connections makes the whole instance unavailable rather
        # than degrading the one process that asked for too much.
        "pool_recycle": 1800,
    }

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False,
    **_pool_kwargs,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
