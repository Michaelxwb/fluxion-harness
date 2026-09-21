"""task.task_submission 的 schema 一致性与提交幂等约束（B-102 / RULE-api-002）。

真实边界：Alembic 迁移 → 真实 PostgreSQL → SQLAlchemy ORM。
不得用 SQLite 或 mock 顶替 partial unique 行为。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_worker.infrastructure.models.task_submission import TaskSubmission
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from conftest import TenantContext

SCHEMA = "task"
TABLE = "task_submission"
UNIQUE_INDEX = "uq_task_submission_tenant_key_endpoint"
EXPECTED_INDEXES = (
    UNIQUE_INDEX,
    "ix_task_submission_task_create_time",
    "ix_task_submission_schedule_create_time",
)
EXPECTED_COLUMNS = {
    "id",
    "is_deleted",
    "create_time",
    "update_time",
    "tenant_id",
    "idempotency_key",
    "endpoint",
    "request_fingerprint",
    "actor_user_id",
    "task_id",
    "schedule_id",
    "response_json",
}
EXPECTED_FOREIGN_KEYS = {
    ("task_id", "task.task_execution.id"),
    ("schedule_id", "task.task_schedule.id"),
}

INSERT = sa.text(
    """
    INSERT INTO task.task_submission
        (tenant_id, idempotency_key, endpoint, request_fingerprint, actor_user_id, response_json)
    VALUES (:tenant_id, :key, :endpoint, :fingerprint, :actor_user_id, '{}'::jsonb)
    """
)


async def _reflect() -> dict[str, Any]:
    from muad_agent_worker.infrastructure.db import get_session_factory

    async with get_session_factory()() as session:
        connection = await session.connection()

        def probe(sync_connection: sa.Connection) -> dict[str, Any]:
            inspector = inspect(sync_connection)
            return {
                "columns": inspector.get_columns(TABLE, schema=SCHEMA),
                "foreign_keys": inspector.get_foreign_keys(TABLE, schema=SCHEMA),
                "indexes": inspector.get_indexes(TABLE, schema=SCHEMA),
            }

        return await connection.run_sync(probe)


@pytest.fixture
async def submission_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    """复用 conftest 的租户清理，并额外清掉本表的提交记录。"""
    try:
        yield tenant
    finally:
        async with tenant.session_factory() as session:
            await session.execute(
                sa.text(f"DELETE FROM {SCHEMA}.{TABLE} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant.tenant_id},
            )
            await session.commit()


def _params(tenant: TenantContext, *, key: str, endpoint: str, fingerprint: str = "sha256:x") -> dict[str, Any]:
    return {
        "tenant_id": tenant.tenant_id,
        "key": key,
        "endpoint": endpoint,
        "fingerprint": fingerprint,
        "actor_user_id": uuid.uuid4(),
    }


async def _insert(tenant: TenantContext, **kwargs: Any) -> None:
    async with tenant.session_factory() as session:
        await session.execute(INSERT, _params(tenant, **kwargs))
        await session.commit()


async def test_task_submission_schema_parity(submission_tenant: TenantContext) -> None:
    """ORM 与真实数据库的表结构一致（标准列、JSONB、同 schema 物理 FK）。"""
    reflected = await _reflect()
    orm_table = TaskSubmission.__table__

    orm_columns = {column.name for column in orm_table.columns}
    db_columns = {str(item["name"]) for item in reflected["columns"]}
    assert orm_columns == EXPECTED_COLUMNS, f"ORM columns: {sorted(orm_columns)}"
    assert db_columns == EXPECTED_COLUMNS, f"DB columns: {sorted(db_columns)}"

    orm_fks = {(fk.parent.name, fk.target_fullname) for fk in orm_table.foreign_keys}
    db_fks = {
        (
            str(item["constrained_columns"][0]),
            f"{item['referred_schema']}.{item['referred_table']}.{item['referred_columns'][0]}",
        )
        for item in reflected["foreign_keys"]
    }
    assert orm_fks == EXPECTED_FOREIGN_KEYS, f"ORM FKs: {orm_fks}"
    assert db_fks == EXPECTED_FOREIGN_KEYS, f"DB FKs: {db_fks}"

    response_json = {str(item["name"]): item for item in reflected["columns"]}["response_json"]
    assert str(response_json["type"]).upper().startswith("JSONB"), response_json["type"]


async def test_task_submission_partial_unique_index(submission_tenant: TenantContext) -> None:
    """partial unique 落在 (tenant_id, idempotency_key, endpoint) 且以 is_deleted=false 为条件。"""
    reflected = await _reflect()
    indexes = {str(item["name"]): item for item in reflected["indexes"]}

    assert set(EXPECTED_INDEXES) <= set(indexes), f"missing indexes: {set(EXPECTED_INDEXES) - set(indexes)}"

    unique = indexes[UNIQUE_INDEX]
    assert bool(unique["unique"]) is True, "uq_task_submission_tenant_key_endpoint must be unique"
    assert [str(column) for column in unique["column_names"]] == [
        "tenant_id",
        "idempotency_key",
        "endpoint",
    ]
    dialect_options = unique.get("dialect_options") or {}
    assert dialect_options.get("postgresql_where"), "unique index must be partial (is_deleted = false)"


async def test_duplicate_submission_is_rejected(submission_tenant: TenantContext) -> None:
    """同租户 + 同键 + 同端点只允许一条有效记录。"""
    tenant = submission_tenant
    await _insert(tenant, key="k-1", endpoint="create-task")

    with pytest.raises(IntegrityError):
        await _insert(tenant, key="k-1", endpoint="create-task")

    async with tenant.session_factory() as session:
        count = (
            await session.execute(
                sa.text(
                    f"SELECT count(*) FROM {SCHEMA}.{TABLE} "
                    "WHERE tenant_id = :tenant_id AND idempotency_key = 'k-1'"
                ),
                {"tenant_id": tenant.tenant_id},
            )
        ).scalar()
    assert count == 1


async def test_tenant_and_endpoint_are_isolated(submission_tenant: TenantContext) -> None:
    """不同租户、不同端点各自独立，互不冲突。"""
    tenant = submission_tenant
    await _insert(tenant, key="shared", endpoint="create-task")
    await _insert(tenant, key="shared", endpoint="create-schedule")

    other = f"{tenant.tenant_id}-other"
    async with tenant.session_factory() as session:
        await session.execute(
            INSERT,
            {
                "tenant_id": other,
                "key": "shared",
                "endpoint": "create-task",
                "fingerprint": "sha256:x",
                "actor_user_id": uuid.uuid4(),
            },
        )
        await session.commit()
        try:
            total = (
                await session.execute(
                    sa.text(f"SELECT count(*) FROM {SCHEMA}.{TABLE} WHERE idempotency_key = 'shared'")
                )
            ).scalar()
        finally:
            await session.execute(
                sa.text(f"DELETE FROM {SCHEMA}.{TABLE} WHERE tenant_id = :tenant_id"),
                {"tenant_id": other},
            )
            await session.commit()

    assert total == 3


async def test_soft_deleted_row_frees_the_key(submission_tenant: TenantContext) -> None:
    """软删除后 partial unique 不再命中，同键可重新提交。"""
    tenant = submission_tenant
    await _insert(tenant, key="k-2", endpoint="create-task")

    async with tenant.session_factory() as session:
        await session.execute(
            sa.text(
                f"UPDATE {SCHEMA}.{TABLE} SET is_deleted = true "
                "WHERE tenant_id = :tenant_id AND idempotency_key = 'k-2'"
            ),
            {"tenant_id": tenant.tenant_id},
        )
        await session.commit()

    await _insert(tenant, key="k-2", endpoint="create-task")


async def test_rollback_leaves_no_submission(submission_tenant: TenantContext) -> None:
    """事务回滚后不残留首次响应记录。"""
    tenant = submission_tenant
    async with tenant.session_factory() as session:
        await session.execute(INSERT, _params(tenant, key="k-rollback", endpoint="create-task"))
        await session.rollback()

    async with tenant.session_factory() as session:
        count = (
            await session.execute(
                sa.text(
                    f"SELECT count(*) FROM {SCHEMA}.{TABLE} "
                    "WHERE tenant_id = :tenant_id AND idempotency_key = 'k-rollback'"
                ),
                {"tenant_id": tenant.tenant_id},
            )
        ).scalar()
    assert count == 0
