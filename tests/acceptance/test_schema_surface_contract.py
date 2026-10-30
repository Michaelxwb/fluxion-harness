"""S-03 / E-02: the langgraph schema is absent at head and its removal is reversible."""

from __future__ import annotations

from pathlib import Path

import pytest
from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "migrations/versions"


async def test_s03_schema_surface_excludes_langgraph() -> None:
    database_url = SharedSettings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is not configured")
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(text("SELECT schema_name FROM information_schema.schemata"))
            names = {str(row[0]) for row in rows}
    finally:
        await engine.dispose()
    assert {"control", "runtime", "task"} <= names
    assert "langgraph" not in names


def _upgrade_body(text: str) -> str:
    return text.partition("def upgrade() -> None:")[2].partition("def downgrade() -> None:")[0]


def test_e02_langgraph_schema_drop_migration_is_symmetric() -> None:
    sources = {
        path: path.read_text(encoding="utf-8") for path in sorted(VERSIONS.glob("*.py"))
    }
    matches = [
        text for text in sources.values() if "DROP SCHEMA IF EXISTS langgraph" in _upgrade_body(text)
    ]
    assert len(matches) == 1, "expected exactly one migration dropping the langgraph schema"
    assert "CREATE SCHEMA IF NOT EXISTS langgraph" in matches[0].partition("def downgrade()")[2]
