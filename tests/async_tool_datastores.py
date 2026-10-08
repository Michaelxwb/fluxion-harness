"""Isolated PostgreSQL fixtures for async-tool integration and migration tests."""

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.acceptance.datastores import create_datastore, drop_datastore


@pytest.fixture(scope="session")
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
