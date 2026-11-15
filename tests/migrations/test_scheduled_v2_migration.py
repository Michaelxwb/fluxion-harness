"""scheduled-task-v2 的破坏性重建迁移（§4.4）。

真实边界：0049 的旧库 → `db_migrate.py upgrade 0050` → 真实 PostgreSQL 结构。
迁移不导入旧调度历史，但必须保留 `task.workspace` / `workspace_lease`。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration

NEW_TABLES = {
    "task_schedule_revision",
    "task_schedule_draft",
    "schedule_fire",
    "task_execution_checkpoint",
    "task_human_interaction",
    "task_delivery",
    "task_delivery_attempt",
}

LEGACY_SCHEDULE_COLUMNS = {"schedule_type", "cron_expr", "delivery_route_id", "intent_key"}
FIRE_AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


async def _migrate(info: dict[str, str], directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    ini = directory / "scheduled-v2.ini"
    ini.write_text(
        "[alembic]\nscript_location = migrations\nprepend_sys_path = .\n"
        f"sqlalchemy.url = {info['database_url']}\n"
    )
    return await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "migrations/db_migrate.py", "-c", str(ini), *args],
        capture_output=True,
        text=True,
        timeout=300,
    )


async def _columns(engine: AsyncEngine, table: str) -> set[str]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'task' AND table_name = :table"
            ),
            {"table": table},
        )
        return {str(row[0]) for row in rows}


async def test_upgrade_rebuilds_schedule_tables_and_keeps_workspace(
    datastore_at_revision, tmp_path: Path
) -> None:
    info = datastore_at_revision("0049")
    engine = create_async_engine(info["database_url"])
    workspace_id = uuid.uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO task.workspace (id, tenant_id, user_id, agent_id) "
                    "VALUES (:id, 'b15', :user, :agent)"
                ),
                {"id": workspace_id, "user": uuid.uuid4(), "agent": uuid.uuid4()},
            )

        assert "schedule_type" in await _columns(engine, "task_schedule")

        result = await _migrate(info, tmp_path, "upgrade", "0050")
        assert result.returncode == 0, result.stderr

        schedule_columns = await _columns(engine, "task_schedule")
        assert not (LEGACY_SCHEDULE_COLUMNS & schedule_columns)
        assert {"owner_user_id", "current_revision_id", "occurrence_count", "config_version"} <= (
            schedule_columns
        )
        execution_columns = await _columns(engine, "task_execution")
        assert {"wait_kind", "wait_payload_json", "schedule_fire_id", "resume_count"} <= (
            execution_columns
        )
        assert not ({"delivery_status", "delivery_mode", "intent_key"} & execution_columns)

        async with engine.connect() as connection:
            tables = {
                str(row[0])
                for row in await connection.execute(
                    sa.text("SELECT tablename FROM pg_tables WHERE schemaname = 'task'")
                )
            }
            revision = await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            survivors = await connection.scalar(
                sa.text("SELECT count(*) FROM task.workspace WHERE id = :id"), {"id": workspace_id}
            )
        assert NEW_TABLES <= tables
        assert revision == "0050"
        assert survivors == 1
    finally:
        await engine.dispose()


async def test_occurrence_uniqueness_is_enforced_by_the_database(
    datastore_at_revision, tmp_path: Path
) -> None:
    info = datastore_at_revision("0050")
    engine = create_async_engine(info["database_url"])
    tenant = "b15-occurrence"
    schedule_id, revision_id, user, agent = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO task.task_schedule "
                    "(id, tenant_id, owner_user_id, agent_id, name, created_by) "
                    "VALUES (:id, :tenant, :user, :agent, 'weekly', :user)"
                ),
                {"id": schedule_id, "tenant": tenant, "user": user, "agent": agent},
            )
            await connection.execute(
                sa.text(
                    "INSERT INTO task.task_schedule_revision "
                    "(id, tenant_id, schedule_id, revision_no, mode, instruction_raw, normalized_goal, "
                    " recurrence_kind, cron_expr, timezone, created_by) "
                    "VALUES (:id, :tenant, :schedule, 1, 'REMINDER', 'weekly', 'weekly', "
                    " 'CRON', '0 9 * * 1', 'Asia/Shanghai', :user)"
                ),
                {"id": revision_id, "tenant": tenant, "schedule": schedule_id, "user": user},
            )

        insert_fire = sa.text(
            "INSERT INTO task.schedule_fire "
            "(tenant_id, schedule_id, schedule_revision_id, scheduled_at, status) "
            "VALUES (:tenant, :schedule, :revision, :at, 'CREATED')"
        )
        async with engine.begin() as connection:
            await connection.execute(
                insert_fire,
                {
                    "tenant": tenant,
                    "schedule": schedule_id,
                    "revision": revision_id,
                    "at": FIRE_AT,
                },
            )
        with pytest.raises(sa.exc.IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    insert_fire,
                    {
                        "tenant": tenant,
                        "schedule": schedule_id,
                        "revision": revision_id,
                        "at": FIRE_AT,
                    },
                )
    finally:
        await engine.dispose()


async def test_downgrade_is_refused(datastore_at_revision, tmp_path: Path) -> None:
    info = datastore_at_revision("0050")
    result = await _migrate(info, tmp_path, "downgrade", "0049")
    assert result.returncode != 0
    assert "restore from backup" in result.stderr
