"""Isolated PostgreSQL fixtures for async-tool integration and migration tests."""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.acceptance.datastores import create_datastore, drop_datastore

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def migration_head() -> str:
    """迁移链的真实 head（不钉死常量）：链推进时断言不需要跟着改。"""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    return head


@pytest.fixture
def async_tool_datastore() -> Iterator[dict[str, str]]:
    info = create_datastore()
    try:
        yield info
    finally:
        drop_datastore(info["name"], info["admin_database_url"], info["redis_url"])


@pytest.fixture
async def async_tool_database(async_tool_datastore: dict[str, str]) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(async_tool_datastore["database_url"])
    try:
        yield engine
    finally:
        await engine.dispose()
