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
    """Keep query endpoints useful while local Postgres is intentionally offline.

    Only the connection attempt is guarded. Wrapping the `yield` as well meant that any exception
    the endpoint raised was thrown back into this generator, caught here, and answered with a
    second `yield` — which asyncio reports as `RuntimeError: generator didn't stop after athrow()`,
    turning every deliberate 404 into an opaque 500. Nothing raised from `/query` until multi-turn
    added a rejected conversation id, so the fault sat here unexercised.
    """
    session = SessionFactory()
    try:
        # Driver-level connection failures are not consistently wrapped by SQLAlchemy.
        await session.execute(text("select 1"))
    except Exception:
        await session.close()
        yield None
        return
    try:
        yield session
    finally:
        await session.close()
