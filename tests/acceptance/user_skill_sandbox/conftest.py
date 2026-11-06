"""[user-skill-sandbox] 验收夹具：真实 PostgreSQL 会话（由 acceptance 基座注入空库）。"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from muad_common import SharedSettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture()
def shared_tmp_path() -> Iterator[Path]:
    """Docker Desktop 只共享部分宿主机路径；用 /tmp 让 bind mount 真实可见。"""
    path = Path(tempfile.mkdtemp(prefix="muad-system-deps-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    settings = SharedSettings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as active:
            yield active
    finally:
        await engine.dispose()
