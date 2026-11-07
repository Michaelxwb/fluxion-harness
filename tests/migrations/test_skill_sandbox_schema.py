"""[S-01/S-04][RULE-data-001] 0037 迁移：Skill/Artifact 新列、草稿与 workspace 表、回填与回滚。

真实边界：真实 PostgreSQL 空库 → 0036 旧结构种存量行 → `migrations/db_migrate.py`
升级到 0037 → 反射/约束验证 → 降级回 0036 再升级。全程不 mock 数据库。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = "0036"
TARGET = "0037"
NEW_TABLES = {"skill_draft": "control", "workspace": "task", "workspace_lease": "task"}


def _ini(path: Path, info: dict[str, str]) -> Path:
    target = path / "skill-sandbox.ini"
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


async def _engine(info: dict[str, str]) -> AsyncEngine:
    return create_async_engine(info["database_url"])


async def _seed_legacy_skill(info: dict[str, str]) -> tuple[str, str]:
    skill_id, artifact_id = str(uuid.uuid4()), str(uuid.uuid4())
    engine = await _engine(info)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO control.skill (id, tenant_id, key, name, description) "
                "VALUES (:id, 'legacy-tenant', :key, 'Legacy', 'legacy row')"
            ),
            {"id": skill_id, "key": f"legacy-{uuid.uuid4()}"},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO control.skill_artifact "
                "(id, skill_id, version, checksum, storage_key, package_size, "
                " validation_status, created_by) "
                "VALUES (:id, :skill_id, '1.0.0', :checksum, 'skills/legacy.zip', 1, 'READY', :created_by)"
            ),
            {
                "id": artifact_id,
                "skill_id": skill_id,
                "checksum": "sha256:" + uuid.uuid4().hex,
                "created_by": str(uuid.uuid4()),
            },
        )
    await engine.dispose()
    return skill_id, artifact_id


async def _inspect_table(engine: AsyncEngine, schema: str, table: str) -> dict[str, object]:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync: {
                "columns": sa.inspect(sync).get_columns(table, schema=schema),
                "indexes": sa.inspect(sync).get_indexes(table, schema=schema),
            }
        )


def _columns(structure: dict[str, object]) -> dict[str, dict[str, object]]:
    return {str(column["name"]): column for column in structure["columns"]}  # type: ignore[index]


def _indexes(structure: dict[str, object]) -> dict[str, dict[str, object]]:
    return {str(index["name"]): index for index in structure["indexes"]}  # type: ignore[index]


def _partial_where(index: dict[str, object]) -> str:
    return str((index.get("dialect_options") or {}).get("postgresql_where") or "")


async def _legacy_values(
    engine: AsyncEngine, skill_id: str, artifact_id: str
) -> tuple[object, ...]:
    async with engine.connect() as connection:
        skill = (
            await connection.execute(
                sa.text("SELECT source, author, owner_user_id FROM control.skill WHERE id = :id"),
                {"id": skill_id},
            )
        ).one()
        artifact = (
            await connection.execute(
                sa.text(
                    "SELECT review_status, review_note, reviewed_by, reviewed_at, "
                    "submitted_by_user_id, submitted_agent_id, submitted_channel, source "
                    "FROM control.skill_artifact WHERE id = :id"
                ),
                {"id": artifact_id},
            )
        ).one()
    return skill, artifact


async def test_upgrade_backfills_legacy_rows_and_creates_indexes(
    datastore_at_revision: Callable[[str], dict[str, str]], tmp_path: Path
) -> None:
    """[S-04][RULE-data-001] 存量 skill/artifact 回填 ADMIN/APPROVED，关键索引齐全。"""
    info = datastore_at_revision(PREVIOUS)
    skill_id, artifact_id = await _seed_legacy_skill(info)
    await _migrate(info, tmp_path, "upgrade", TARGET)
    engine = await _engine(info)
    try:
        skill, artifact = await _legacy_values(engine, skill_id, artifact_id)
        assert skill.source == "ADMIN"
        assert skill.author is None
        assert skill.owner_user_id is None
        assert artifact.review_status == "APPROVED"
        assert artifact.source == "ADMIN"
        assert artifact.review_note is None and artifact.reviewed_by is None
        assert artifact.reviewed_at is None
        assert artifact.submitted_by_user_id is None
        assert artifact.submitted_agent_id is None and artifact.submitted_channel is None

        skill_structure = await _inspect_table(engine, "control", "skill")
        skill_indexes = _indexes(skill_structure)
        assert "ix_skill_tenant_source" in skill_indexes
        assert "ix_skill_owner" in skill_indexes

        artifact_structure = await _inspect_table(engine, "control", "skill_artifact")
        artifact_indexes = _indexes(artifact_structure)
        assert "ix_skill_artifact_review" in artifact_indexes
        assert "ix_skill_artifact_submitter" in artifact_indexes
        pending = artifact_indexes["uq_skill_artifact_one_pending"]
        assert pending["unique"] is True
        assert "PENDING" in _partial_where(pending)
        assert "is_deleted" in _partial_where(pending)
    finally:
        await engine.dispose()


async def test_new_tables_have_standard_columns_and_timestamptz(
    datastore_at_revision: Callable[[str], dict[str, str]], tmp_path: Path
) -> None:
    """[S-01][RULE-data-001] 草稿/workspace 表标准列、timestamptz 与关键索引。"""
    info = datastore_at_revision(PREVIOUS)
    await _migrate(info, tmp_path, "upgrade", TARGET)
    engine = await _engine(info)
    try:
        for table, schema in NEW_TABLES.items():
            structure = await _inspect_table(engine, schema, table)
            columns = _columns(structure)
            for standard in ("id", "is_deleted", "create_time", "update_time"):
                assert standard in columns, f"{schema}.{table}.{standard} 缺失"
            assert str(columns["create_time"]["type"]) == "TIMESTAMP"
            assert columns["create_time"]["type"].timezone is True
            assert columns["update_time"]["type"].timezone is True

        draft_indexes = _indexes(await _inspect_table(engine, "control", "skill_draft"))
        owner_key = draft_indexes["uq_skill_draft_tenant_owner_key"]
        assert owner_key["unique"] is True
        assert "is_deleted" in _partial_where(owner_key)

        workspace = _columns(await _inspect_table(engine, "task", "workspace"))
        assert workspace["last_access_time"]["type"].timezone is True
        assert workspace["state"]["default"] is not None

        lease = _columns(await _inspect_table(engine, "task", "workspace_lease"))
        assert lease["lease_until"]["type"].timezone is True
        assert {"workspace_id", "run_id", "task_id", "execution_id"} <= set(lease)
    finally:
        await engine.dispose()


async def _insert_artifact(
    engine: AsyncEngine, skill_id: str, *, version: str, checksum: str, review_status: str
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO control.skill_artifact "
                "(skill_id, version, checksum, storage_key, package_size, validation_status, "
                " created_by, review_status, source) "
                "VALUES (:skill_id, :version, :checksum, :storage_key, 1, 'READY', :created_by, "
                " :review_status, 'USER')"
            ),
            {
                "skill_id": skill_id,
                "version": version,
                "checksum": checksum,
                "storage_key": f"skills/{uuid.uuid4()}.zip",
                "created_by": str(uuid.uuid4()),
                "review_status": review_status,
            },
        )


async def test_skill_artifact_allows_one_pending_per_skill(
    datastore_at_revision: Callable[[str], dict[str, str]], tmp_path: Path
) -> None:
    """[S-04][RULE-data-001] 单 Skill 最多一个活跃 PENDING；软删后允许新 PENDING。"""
    info = datastore_at_revision(PREVIOUS)
    skill_id = str(uuid.uuid4())
    engine = await _engine(info)
    try:
        await _migrate(info, tmp_path, "upgrade", TARGET)
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO control.skill (id, tenant_id, key, name, description, source) "
                    "VALUES (:id, 'pending-tenant', :key, 'Pending', 'd', 'USER')"
                ),
                {"id": skill_id, "key": f"pending-{uuid.uuid4()}"},
            )
        await _insert_artifact(
            engine,
            skill_id,
            version="1.0.0",
            checksum="sha256:" + "a" * 64,
            review_status="PENDING",
        )
        with pytest.raises(IntegrityError):
            await _insert_artifact(
                engine, skill_id, version="1.0.1", checksum="sha256:" + "b" * 64, review_status="PENDING"
            )
        await _insert_artifact(
            engine,
            skill_id,
            version="1.0.2",
            checksum="sha256:" + "c" * 64,
            review_status="APPROVED",
        )
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "UPDATE control.skill_artifact SET is_deleted = true "
                    "WHERE skill_id = :skill_id AND review_status = 'PENDING'"
                ),
                {"skill_id": skill_id},
            )
        await _insert_artifact(
            engine,
            skill_id,
            version="1.0.3",
            checksum="sha256:" + "d" * 64,
            review_status="PENDING",
        )
    finally:
        await engine.dispose()


async def test_skill_draft_owner_key_is_unique_per_tenant(
    datastore_at_revision: Callable[[str], dict[str, str]], tmp_path: Path
) -> None:
    """[S-01][RULE-data-001] (tenant, owner, key) 活跃唯一；软删后可重建。"""
    info = datastore_at_revision(PREVIOUS)
    await _migrate(info, tmp_path, "upgrade", TARGET)
    engine = await _engine(info)
    values = {
        "tenant_id": "draft-tenant",
        "owner_user_id": str(uuid.uuid4()),
        "key": "my-skill",
    }
    insert = sa.text(
        "INSERT INTO control.skill_draft (tenant_id, owner_user_id, key) "
        "VALUES (:tenant_id, :owner_user_id, :key)"
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(insert, values)
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(insert, values)
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "UPDATE control.skill_draft SET is_deleted = true "
                    "WHERE tenant_id = :tenant_id AND owner_user_id = :owner_user_id AND key = :key"
                ),
                values,
            )
            await connection.execute(insert, values)
    finally:
        await engine.dispose()


async def test_workspace_and_lease_partial_uniques(
    datastore_at_revision: Callable[[str], dict[str, str]], tmp_path: Path
) -> None:
    """[B-01][RULE-data-001] workspace (tenant,user,agent) 与 lease (workspace,execution) 活跃唯一。"""
    info = datastore_at_revision(PREVIOUS)
    await _migrate(info, tmp_path, "upgrade", TARGET)
    engine = await _engine(info)
    tenant, user_id, agent_id = "ws-tenant", str(uuid.uuid4()), str(uuid.uuid4())
    workspace_insert = sa.text(
        "INSERT INTO task.workspace (tenant_id, user_id, agent_id) "
        "VALUES (:tenant_id, :user_id, :agent_id) RETURNING id"
    )
    try:
        async with engine.begin() as connection:
            row = (
                await connection.execute(
                    workspace_insert,
                    {"tenant_id": tenant, "user_id": user_id, "agent_id": agent_id},
                )
            ).scalar_one()
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    workspace_insert, {"tenant_id": tenant, "user_id": user_id, "agent_id": agent_id}
                )
        lease_insert = sa.text(
            "INSERT INTO task.workspace_lease "
            "(workspace_id, run_id, task_id, execution_id, lease_until) "
            "VALUES (:workspace_id, :run_id, :task_id, :execution_id, now() + interval '1 hour')"
        )
        lease = {
            "workspace_id": row,
            "run_id": str(uuid.uuid4()),
            "task_id": str(uuid.uuid4()),
            "execution_id": str(uuid.uuid4()),
        }
        async with engine.begin() as connection:
            await connection.execute(lease_insert, lease)
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(lease_insert, lease)
    finally:
        await engine.dispose()


async def test_migration_can_roll_back_and_reapply(
    datastore_at_revision: Callable[[str], dict[str, str]], tmp_path: Path
) -> None:
    """[RULE-data-001] 0037 可重复 upgrade/downgrade，回滚后结构完全移除。"""
    info = datastore_at_revision(PREVIOUS)
    await _migrate(info, tmp_path, "upgrade", TARGET)
    await _migrate(info, tmp_path, "downgrade", PREVIOUS)
    engine = await _engine(info)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(
                lambda sync: sa.inspect(sync).get_table_names(schema="control")
            )
            task_tables = await connection.run_sync(
                lambda sync: sa.inspect(sync).get_table_names(schema="task")
            )
            skill_columns = await connection.run_sync(
                lambda sync: {c["name"] for c in sa.inspect(sync).get_columns("skill", schema="control")}
            )
        assert "skill_draft" not in tables
        assert "workspace" not in task_tables and "workspace_lease" not in task_tables
        assert not {"source", "author", "owner_user_id"} & skill_columns
    finally:
        await engine.dispose()
    await _migrate(info, tmp_path, "upgrade", TARGET)
    engine = await _engine(info)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(
                lambda sync: sa.inspect(sync).get_table_names(schema="control")
            )
        assert "skill_draft" in tables
    finally:
        await engine.dispose()
