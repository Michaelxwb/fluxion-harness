"""TASK-002: v2 columns and candidate table exist in the migrated schema."""

from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa

from tests.async_tool_datastores import async_tool_database as async_tool_database

pytestmark = pytest.mark.integration


async def test_v2_memory_columns_and_candidate_table(async_tool_database):
    async with async_tool_database.begin() as connection:
        columns = await connection.run_sync(
            lambda c: {item["name"] for item in sa.inspect(c).get_columns("user_memory", "runtime")}
        )
        tables = await connection.run_sync(lambda c: set(sa.inspect(c).get_table_names("runtime")))
    assert {
        "memory_kind",
        "predicate",
        "scope_state",
        "scope_json",
        "scope_hash",
        "content_text",
        "title",
        "summary",
        "tags_json",
        "cardinality",
        "priority",
        "taxonomy_version",
    } <= columns
    assert "memory_conflict_candidate" in tables


async def test_v2_candidate_pending_unique_and_required_target(async_tool_database):
    async with async_tool_database.begin() as connection:
        indexes = await connection.run_sync(
            lambda c: {
                item["name"]: item
                for item in sa.inspect(c).get_indexes("memory_conflict_candidate", "runtime")
            }
        )
        columns = await connection.run_sync(
            lambda c: {
                item["name"]: item
                for item in sa.inspect(c).get_columns("memory_conflict_candidate", "runtime")
            }
        )
    assert indexes["uq_memory_conflict_pending"]["unique"] is True
    assert indexes["uq_memory_conflict_pending"]["column_names"] == [
        "tenant_id",
        "user_id",
        "memory_key",
        "candidate_hash",
    ]
    assert columns["memory_key"]["nullable"] is False
    assert columns["base_version"]["nullable"] is False


async def test_v2_row_carries_no_legacy_category(async_tool_database):
    """v2 identity lives in memory_kind; category stays NULL instead of a fake legacy value."""

    identity, user = uuid4(), uuid4()
    async with async_tool_database.begin() as connection:
        columns = await connection.run_sync(
            lambda c: {
                item["name"]: item for item in sa.inspect(c).get_columns("user_memory", "runtime")
            }
        )
        history = await connection.run_sync(
            lambda c: {
                item["name"] for item in sa.inspect(c).get_columns("user_memory_history", "runtime")
            }
        )
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.user_memory"
                " (id,tenant_id,user_id,memory_key,memory_kind,predicate,content_json,expires_at)"
                " VALUES (:id,'b02',:user,'user:fact:role','FACT','role',CAST(:content AS jsonb),"
                " statement_timestamp()+interval '30 days')"
            ),
            {"id": identity, "user": user, "content": '{"value":"pm"}'},
        )
        stored = (
            await connection.execute(
                sa.text("SELECT category,memory_kind FROM runtime.user_memory WHERE id=:id"),
                {"id": identity},
            )
        ).one()
    assert columns["category"]["nullable"] is True
    assert "legacy_snapshots_json" not in history
    assert stored.category is None
    assert stored.memory_kind == "FACT"
