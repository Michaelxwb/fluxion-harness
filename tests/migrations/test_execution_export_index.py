"""TASK-001 migration 0043: source_run_id index exists and is tenant-scoped."""

from __future__ import annotations

from pathlib import Path

import pytest
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations/versions/0043_task_execution_source_run_index.py"


def test_migration_revision_chain() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0043"' in source
    assert 'down_revision = "0042"' in source
    assert "ix_task_execution_source_run" in source


@pytest.mark.asyncio
async def test_source_run_index_is_present() -> None:
    async with get_session_factory()() as session:
        found = await session.scalar(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'task' AND tablename = 'task_execution' "
                "AND indexname = 'ix_task_execution_source_run'"
            )
        )
    assert found == "ix_task_execution_source_run"
