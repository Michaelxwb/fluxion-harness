from collections.abc import AsyncIterator
from functools import lru_cache

from muad_common import SharedSettings
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    return create_async_engine(SharedSettings().require_database_url(), pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    session = get_session_factory()()
    try:
        yield session
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    if not SharedSettings().database_url:
        return
    await get_engine().dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()
