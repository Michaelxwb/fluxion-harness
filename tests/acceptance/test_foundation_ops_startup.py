from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from muad_api import StartupValidationError, validate_startup
from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "migrations/versions"


def _migration_head() -> str:
    graph: dict[str, str | None] = {}
    for path in sorted(MIGRATIONS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision = "([^"]+)"', source, re.M)
        down = re.search(r'^down_revision = (?:None|"([^"]+)")', source, re.M)
        if revision:
            graph[revision.group(1)] = down.group(1) if down else None
    heads = sorted(set(graph) - {down for down in graph.values() if down})
    assert len(heads) == 1, f"unexpected migration heads: {heads}"
    return heads[0]


HEAD_REVISION = _migration_head()


@pytest.fixture()
async def engine() -> AsyncIterator[AsyncEngine]:
    database_url = SharedSettings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    engine: AsyncEngine = create_async_engine(database_url)
    yield engine
    await engine.dispose()


async def _set_revision(engine: AsyncEngine, revision: str) -> bool:
    async with engine.begin() as connection:
        exists = await connection.execute(text("SELECT to_regclass('alembic_version')"))
        if exists.scalar_one_or_none() is None:
            return False
        await connection.execute(text("UPDATE alembic_version SET version_num = :rev"), {"rev": revision})
    return True


async def test_e04_happy_path_passes(engine: AsyncEngine, tmp_path: Path) -> None:
    settings = SharedSettings()
    assert settings.database_url
    await validate_startup(
        settings,
        engine,
        SimpleNamespace(root=tmp_path),
        migrations_dir=MIGRATIONS_DIR,
    )


async def test_e04_missing_config_or_storage_aborts(engine: AsyncEngine, tmp_path: Path) -> None:
    settings = SharedSettings(database_url=None)
    with pytest.raises(StartupValidationError, match="database_url is not configured"):
        await validate_startup(
            settings,
            engine,
            SimpleNamespace(root=tmp_path),
            migrations_dir=MIGRATIONS_DIR,
        )

    with pytest.raises(StartupValidationError, match="artifact storage is not mounted"):
        await validate_startup(
            SharedSettings(),
            engine,
            SimpleNamespace(root=tmp_path / "missing"),
            migrations_dir=MIGRATIONS_DIR,
        )


async def test_e04_schema_not_at_head_aborts(engine: AsyncEngine, tmp_path: Path) -> None:
    if not await _set_revision(engine, "0001"):
        pytest.skip("alembic_version table not present")
    try:
        with pytest.raises(StartupValidationError, match="not at head"):
            await validate_startup(
                SharedSettings(),
                engine,
                SimpleNamespace(root=tmp_path),
                migrations_dir=MIGRATIONS_DIR,
            )
    finally:
        await _set_revision(engine, HEAD_REVISION)


