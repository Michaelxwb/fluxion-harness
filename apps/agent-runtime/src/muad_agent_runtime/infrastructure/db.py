from collections.abc import AsyncIterator, Callable
from functools import lru_cache

from muad_common import process_settings
from muad_common.db_counters import install_db_counters
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .db_timing import TimedAsyncAdaptedQueuePool, install_db_timing

SessionFactory = async_sessionmaker[AsyncSession]
# 部分 application 服务接收的是"工厂函数"而非已构造的 sessionmaker（调用约定为 factory()()）
SessionFactoryProvider = Callable[[], SessionFactory]


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = process_settings()
    engine = create_async_engine(
        settings.require_database_url(),
        pool_pre_ping=True,
        # 池参数按 typed 设置显式接线：池耗尽时按 `db_pool_timeout_sec` 快速失败（默认 5s），
        # 不再落到 SQLAlchemy 的 30s 默认把请求挂死（2026-10-20 50 用户容量复现：
        # `ADMISSION_TIMEOUT`/控制探测恰好 30s 与 `pool_timeout` 默认值一致）。
        poolclass=TimedAsyncAdaptedQueuePool,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_sec,
    )
    install_db_timing(engine)
    install_db_counters(engine, service="agent-runtime")
    return engine


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    session = get_session_factory()()
    try:
        yield session
        if session.in_transaction():
            await session.commit()
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    if not process_settings().database_url:
        return
    await get_engine().dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()
