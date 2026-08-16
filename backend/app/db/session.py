from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_timeout=settings.database_pool_timeout_seconds,
)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


async def get_optional_session() -> AsyncIterator[AsyncSession | None]:
    """Keep query endpoints useful while local Postgres is intentionally offline."""
    try:
        async with SessionFactory() as session:
            await session.execute(text("select 1"))
            yield session
    except Exception:
        # Driver-level connection failures are not consistently wrapped by SQLAlchemy.
        yield None
