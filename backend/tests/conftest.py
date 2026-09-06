from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from fluxion.registry.schema import metadata
from tests.runtime_helpers import TEST_POSTGRES_DSN, pg_store

__all__ = ["pg_store"]


@pytest.fixture(scope="session", autouse=True)
async def _pg_schema_bootstrap() -> None:
    """Session 级 PG 建表（ADR-A007）：serving 模式 initialize() 为 no-op，
    非 reset 路径依赖表已存在；CI 空库下由本夹具一次性建表（checkfirst 幂等）。
    """
    engine = create_async_engine(TEST_POSTGRES_DSN)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all, checkfirst=True)
    finally:
        await engine.dispose()
