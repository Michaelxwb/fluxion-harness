"""Real pre-0020 databases and Alembic upgrades; no shared dev database."""

import asyncio
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.acceptance.datastores import create_datastore, drop_datastore


@pytest.fixture
def legacy_memory_datastore() -> Iterator[dict[str, str]]:
    info = create_datastore(revision="0019")
    try:
        yield info
    finally:
        drop_datastore(info["name"], info["admin_database_url"], info["redis_url"])


@pytest.fixture
async def legacy_memory_database(legacy_memory_datastore: dict[str, str]) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(legacy_memory_datastore["database_url"])
    try:
        yield engine
    finally:
        await engine.dispose()


async def upgrade_memory(info: dict[str, str], directory: Path) -> subprocess.CompletedProcess[str]:
    ini = directory / "memory-migration.ini"
    ini.write_text(
        "[alembic]\nscript_location = migrations\nprepend_sys_path = .\n"
        + "sqlalchemy.url = "
        + info["database_url"]
        + "\n"
    )
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "migrations/db_migrate.py", "-c", str(ini), "upgrade", "0020"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return subprocess.CompletedProcess(
        result.args, result.returncode, result.stdout, result.stderr + result.stdout
    )
