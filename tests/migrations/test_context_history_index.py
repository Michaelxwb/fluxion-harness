"""Migration 0061: history paging index exists and matches HISTORY_EVENT_TYPES."""

from __future__ import annotations

from pathlib import Path

import pytest
from muad_agent_runtime.application.context_builder import HISTORY_EVENT_TYPES
from muad_agent_runtime.infrastructure.db import get_session_factory
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations/versions/0061_conversation_history_index.py"


def test_migration_revision_chain() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0061"' in source
    assert 'down_revision = "0060"' in source
    assert "ix_canonical_event_history" in source


@pytest.mark.asyncio
async def test_history_index_is_present_and_has_every_history_event_type() -> None:
    async with get_session_factory()() as session:
        definition = await session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'runtime' AND tablename = 'canonical_event' "
                "AND indexname = 'ix_canonical_event_history'"
            )
        )
    assert definition is not None
    assert "is_deleted = false" in definition or "is_deleted=false" in definition
    for event_type in HISTORY_EVENT_TYPES:
        assert event_type in definition, f"history index predicate missing {event_type}"
