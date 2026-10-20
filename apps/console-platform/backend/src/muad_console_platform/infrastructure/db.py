from collections.abc import AsyncIterator
from functools import lru_cache

from muad_common import SharedSettings
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = SharedSettings()
    return create_async_engine(
        settings.require_database_url(),
        pool_pre_ping=True,
        # 池参数按 typed 设置显式接线：池耗尽时按 `db_pool_timeout_sec` 快速失败（默认 5s），
        # 不再落到 SQLAlchemy 的 30s 默认把请求挂死（2026-10-20 50 用户容量复现：
        # `ADMISSION_TIMEOUT`/控制探测恰好 30s 与 `pool_timeout` 默认值一致）。
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_sec,
    )


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
