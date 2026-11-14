"""TASK-002/TASK-017: v2 columns, candidate table, and legacy job drain."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.async_tool_datastores import async_tool_database as async_tool_database

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = "0048"
TARGET = "0049"


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


def _ini(path: Path, info: dict[str, str]) -> Path:
    target = path / "memory-schema.ini"
    target.write_text(
        "[alembic]\nscript_location = migrations\nprepend_sys_path = .\n"
        f"sqlalchemy.url = {info['database_url']}\n"
    )
    return target


async def _migrate(info: dict[str, str], tmp_path: Path, direction: str, target: str) -> None:
    completed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "migrations/db_migrate.py", "-c", str(_ini(tmp_path, info)), direction, target],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=ROOT,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def _job_values(status: str) -> dict[str, object]:
    values: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": "drain-tenant",
        "run_id": uuid4(),
        "snapshot_id": uuid4(),
        "conversation_id": uuid4(),
        "user_id": uuid4(),
        "through_seq": 1,
        "status": status,
        "enqueued_scope_revision": 0,
    }
    if status == "RUNNING":
        values["lease_owner"] = "legacy-worker"
        values["lease_until"] = datetime.now(UTC) - timedelta(hours=1)
    return values


async def test_upgrade_drains_legacy_in_flight_jobs(
    datastore_at_revision: Callable[[str], dict[str, str]], tmp_path: Path
) -> None:
    """[RULE-memory-006] 0049 排空旧在途提炼任务，终态行不动。"""

    info = datastore_at_revision(PREVIOUS)
    seeded = {status: _job_values(status) for status in ("PENDING", "RUNNING", "DONE")}
    engine = create_async_engine(info["database_url"])
    try:
        async with engine.begin() as connection:
            for values in seeded.values():
                columns = ", ".join(values)
                params = ", ".join(f":{name}" for name in values)
                await connection.execute(
                    sa.text(
                        f"INSERT INTO runtime.memory_consolidation_job ({columns}) VALUES ({params})"
                    ),
                    values,
                )
    finally:
        await engine.dispose()

    await _migrate(info, tmp_path, "upgrade", TARGET)

    engine = create_async_engine(info["database_url"])
    try:
        async with engine.connect() as connection:
            rows = {
                row.id: row
                for row in (
                    await connection.execute(
                        sa.text(
                            "SELECT id, status, lease_owner, lease_until, error_code "
                            "FROM runtime.memory_consolidation_job"
                        )
                    )
                )
            }
    finally:
        await engine.dispose()

    assert len(rows) == 3
    for status in ("PENDING", "RUNNING"):
        row = rows[seeded[status]["id"]]
        assert row.status == "FAILED"
        assert row.lease_owner is None and row.lease_until is None
        assert row.error_code == "LEGACY_DRAINED"
    done = rows[seeded["DONE"]["id"]]
    assert done.status == "DONE" and done.error_code is None
