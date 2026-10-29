"""DB 语句/事务计数器：service × stage 归因，纯观测、不改 SQL 语义。"""

from __future__ import annotations

import pytest
from muad_common.db_counters import (
    DEFAULT_STAGE,
    current_stage,
    db_stage,
    install_db_counters,
    publish_db_counters,
    reset_db_counters,
    snapshot_db_counters,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration


def test_stage_context_defaults_and_nests() -> None:
    assert current_stage() == DEFAULT_STAGE
    with db_stage("model"):
        assert current_stage() == "model"
        with db_stage("admission"):
            assert current_stage() == "admission"
        assert current_stage() == "model"
    assert current_stage() == DEFAULT_STAGE


def test_unknown_stage_falls_back_to_background() -> None:
    with db_stage("not-a-stage"):
        assert current_stage() == DEFAULT_STAGE


async def test_counter_attributes_statements_and_transactions(
    async_tool_datastore: dict[str, str],
) -> None:
    engine = create_async_engine(async_tool_datastore["database_url"])
    install_db_counters(engine, service="test")
    reset_db_counters()
    try:
        with db_stage("model"):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 2"))
    finally:
        await engine.dispose()

    counts = snapshot_db_counters()
    assert counts[("test", "model", "statement")] >= 1
    assert counts[("test", "model", "transaction")] >= 1
    assert counts[("test", DEFAULT_STAGE, "statement")] >= 1
    assert counts[("test", DEFAULT_STAGE, "transaction")] >= 1


async def test_publish_uses_service_and_stage_labels(
    async_tool_datastore: dict[str, str],
) -> None:
    engine = create_async_engine(async_tool_datastore["database_url"])
    install_db_counters(engine, service="test")
    reset_db_counters()
    try:
        with db_stage("event"):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()

    published: list[tuple[str, float, dict[str, str] | None]] = []
    publish_db_counters(
        lambda name, value, labels: published.append((name, value, labels))
    )
    statements = [
        item for item in published if item[0] == "db_statements_total"
    ]
    transactions = [
        item for item in published if item[0] == "db_transactions_total"
    ]
    assert any(
        labels == {"service": "test", "stage": "event"}
        for _, _, labels in statements
    )
    assert any(
        labels == {"service": "test", "stage": "event"}
        for _, _, labels in transactions
    )
