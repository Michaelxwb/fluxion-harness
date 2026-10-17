"""B-210: real migration/ORM parity and constraints for the runtime capacity pool.

0022 建 `runtime.model_capacity` / `model_dispatch_state` / `model_call_lease`：
实际 Key 的计量身份（key_resource_id）稳定且完整唯一；一次 call_attempt 恰好一个
run/job/probe 归属；UNKNOWN 保留占用、不会被软删除隐藏。
"""

import asyncio
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration
TABLES = ("model_capacity", "model_dispatch_state", "model_call_lease")
STANDARD = {"id", "is_deleted", "create_time", "update_time", "tenant_id"}


def _models() -> Any:
    return import_module("muad_agent_runtime.infrastructure.models.runtime")


def _structure(connection: sa.Connection, name: str) -> tuple[object, ...]:
    inspector = sa.inspect(connection)
    columns = sorted(
        (c["name"], str(c["type"]), c["nullable"], c["default"], getattr(c["type"], "timezone", None))
        for c in inspector.get_columns(name, "runtime")
    )
    indexes = sorted(
        (i["name"], tuple(i["column_names"]), i["unique"],
         str(i.get("dialect_options", {}).get("postgresql_where", "")))
        for i in inspector.get_indexes(name, "runtime")
    )
    checks = sorted((c["name"], c["sqltext"]) for c in inspector.get_check_constraints(name, "runtime"))
    foreign = sorted(
        (tuple(f["constrained_columns"]), f["referred_schema"], f["referred_table"],
         tuple(f["referred_columns"]))
        for f in inspector.get_foreign_keys(name, "runtime")
    )
    unique = sorted((u["name"], tuple(u["column_names"])) for u in inspector.get_unique_constraints(name, "runtime"))
    primary = inspector.get_pk_constraint(name, "runtime")["constrained_columns"]
    return columns, indexes, checks, foreign, unique, primary


def _columns(structure: tuple[object, ...]) -> dict[str, tuple[object, ...]]:
    return {str(column[0]): column for column in structure[0]}  # type: ignore[index]


async def _migrate(
    info: dict[str, str], tmp_path: Path, *, direction: str, target: str,
) -> subprocess.CompletedProcess[str]:
    ini = tmp_path / "capacity.ini"
    ini.write_text("[alembic]\nscript_location = migrations\nprepend_sys_path = .\n"
                   f"sqlalchemy.url = {info['database_url']}\n")
    return await asyncio.to_thread(
        subprocess.run, [sys.executable, "migrations/db_migrate.py", "-c", str(ini), direction, target],
        capture_output=True, text=True, timeout=120,
    )


def _lease(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "tenant_id": "b210",
        "call_attempt_id": uuid4(),
        "probe_id": uuid4(),
        "deployment_id": uuid4(),
        "credential_id": uuid4(),
        "key_resource_id": uuid4(),
        "credential_revision": 3,
        "workload": "PROBE",
        "lease_owner": "probe-1",
        "epoch": 1,
        "lease_until": datetime.now(timezone.utc) + timedelta(seconds=60),
        "reserved_tokens": 128,
        "state": "ACTIVE",
    }
    values.update(changes)
    return values


async def _seed_owner_rows(connection: AsyncConnection) -> tuple[UUID, UUID]:
    """真实 FK 目标：一个 Run 与一个 memory consolidation job。"""
    conversation, run_id, job_id = uuid4(), uuid4(), uuid4()
    await connection.execute(sa.text(
        "INSERT INTO runtime.conversation (id,tenant_id,user_id,agent_id) VALUES (:id,'b210',:user,:agent)"
    ), {"id": conversation, "user": uuid4(), "agent": uuid4()})
    await connection.execute(sa.text(
        "INSERT INTO runtime.run_record "
        "(id,tenant_id,conversation_id,user_id,agent_id,status,input_text,trace_id) "
        "VALUES (:id,'b210',:conversation,:user,:agent,'CREATED','capacity','b210-trace')"
    ), {"id": run_id, "conversation": conversation, "user": uuid4(), "agent": uuid4()})
    await connection.execute(sa.text(
        "INSERT INTO runtime.memory_consolidation_job "
        "(id,tenant_id,run_id,snapshot_id,conversation_id,user_id,through_seq,enqueued_scope_revision) "
        "VALUES (:id,'b210',:run,:snapshot,:conversation,:user,1,1)"
    ), {"id": job_id, "run": run_id, "snapshot": uuid4(), "conversation": conversation, "user": uuid4()})
    return run_id, job_id


async def _insert(connection: AsyncConnection, table: sa.Table, values: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await connection.execute(table.insert().values(**values))


async def test_b210_empty_database_schema_parity(async_tool_database: AsyncEngine) -> None:
    models = _models()
    lease_table, capacity_table, dispatch_table = (
        models.ModelCallLease.__table__, models.ModelCapacity.__table__, models.ModelDispatchState.__table__,
    )
    async with async_tool_database.begin() as connection:
        actual = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names("runtime")))
        assert set(TABLES) <= actual
        migrated = {name: await connection.run_sync(_structure, name) for name in TABLES}
        await connection.execute(sa.text("DROP SCHEMA runtime CASCADE"))
        await connection.execute(sa.text("CREATE SCHEMA runtime"))
        await connection.run_sync(models.Base.metadata.create_all)
        created = {name: await connection.run_sync(_structure, name) for name in TABLES}
    assert migrated == created
    for name in TABLES:
        assert STANDARD <= set(_columns(migrated[name]))
    assert set(lease_table.foreign_keys) and {fk.target_fullname for fk in lease_table.foreign_keys} == {
        "runtime.run_record.id", "runtime.memory_consolidation_job.id",
    }
    # 带时区时间列与标准列一致（PG 服务端时间裁决）。
    for name, timed in (("model_capacity", "window_start"), ("model_dispatch_state", "last_picked_at"),
                        ("model_call_lease", "lease_until")):
        assert _columns(migrated[name])[timed][4] is True
        assert _columns(migrated[name])["create_time"][4] is True
    assert _columns(migrated["model_call_lease"])["epoch"][1].startswith("BIGINT")
    assert "UNKNOWN" in next(
        columns[1] for columns in migrated["model_call_lease"][2] if columns[0] == "ck_model_call_lease_state"
    )
    assert capacity_table.name == "model_capacity" and dispatch_table.name == "model_dispatch_state"


async def test_b210_capacity_identity_is_complete_unique_including_tombstone(async_tool_database: AsyncEngine) -> None:
    capacity = _models().ModelCapacity.__table__
    resource_id = uuid4()
    identity = {"tenant_id": "b210", "resource_type": "KEY", "resource_id": resource_id}
    async with async_tool_database.begin() as connection:
        await connection.execute(capacity.insert().values(**identity))
        await _insert(connection, capacity, identity)  # 同一实际 Key 不得出现第二行计量身份
        await connection.execute(capacity.update().where(
            capacity.c.resource_id == resource_id
        ).values(is_deleted=True, update_time=sa.func.now()))
        await _insert(connection, capacity, identity)  # tombstone 仍在完整唯一键内：删除重录不重置旧计量
        for changes in ({"resource_type": "MODEL"}, {"inflight": -1}, {"reserved_tokens": -1},
                        {"reserved_requests": -1}, {"capacity_revision": 0}):
            await _insert(connection, capacity, {**identity, "resource_id": uuid4(), **changes})
        await connection.execute(capacity.insert().values(
            tenant_id="b210", resource_type="QUOTA_GROUP", resource_id=uuid4(),
        ))
        row = (await connection.execute(capacity.select().where(
            capacity.c.resource_id == resource_id
        ))).mappings().one()
        assert (row["inflight"], row["reserved_tokens"], row["reserved_requests"]) == (0, 0, 0)
        assert row["capacity_revision"] == 1 and row["window_start"].tzinfo is not None


async def test_b210_dispatch_state_policy_identity_is_complete_unique_including_tombstone(
    async_tool_database: AsyncEngine,
) -> None:
    dispatch = _models().ModelDispatchState.__table__
    model_id, deployment_id = uuid4(), uuid4()
    identity = {"tenant_id": "b210", "model_id": model_id, "deployment_id": deployment_id, "policy_hash": "a" * 64}
    async with async_tool_database.begin() as connection:
        await connection.execute(dispatch.insert().values(**identity))
        await _insert(connection, dispatch, identity)  # 完整唯一：同策略版本只允许一行共享计数
        await connection.execute(dispatch.update().where(
            dispatch.c.deployment_id == deployment_id
        ).values(is_deleted=True, update_time=sa.func.now()))
        await _insert(connection, dispatch, identity)
        for changes in ({"dispatch_epoch": -1}, {"dispatch_count": -1}):
            await _insert(connection, dispatch, {**identity, "policy_hash": uuid4().hex, **changes})
        await connection.execute(dispatch.insert().values(
            **{**identity, "policy_hash": "b" * 64}, dispatch_count=2,
        ))  # 另一策略版本单独解释，不合并计数


async def test_b210_lease_attempt_is_complete_unique_and_freezes_key_identity(
    async_tool_database: AsyncEngine,
) -> None:
    models = _models()
    lease, capacity = models.ModelCallLease.__table__, models.ModelCapacity.__table__
    key_resource_id = uuid4()
    first, second = _lease(key_resource_id=key_resource_id), _lease(key_resource_id=key_resource_id)
    async with async_tool_database.begin() as connection:
        await connection.execute(capacity.insert().values(
            tenant_id="b210", resource_type="KEY", resource_id=key_resource_id, inflight=2,
        ))
        # 同一实际 Key 跨成员/部署合并为同一行计量身份：两个 attempt 都指向它。
        await connection.execute(lease.insert().values(**first))
        await connection.execute(lease.insert().values(**second))
        await _insert(connection, lease, dict(first))  # call_attempt 完整唯一：重试不重复占位
        rows = (await connection.execute(lease.select().where(
            lease.c.key_resource_id == key_resource_id
        ))).mappings().all()
        assert len(rows) == 2
        assert {row["key_resource_id"] for row in rows} == {key_resource_id}
        assert {row["credential_revision"] for row in rows} == {3}
        # 逻辑 UUID：轮换只改当前凭据，不能迁移/提前释放旧 attempt 的占用。
        foreign = await connection.run_sync(lambda sync: {
            (tuple(f["constrained_columns"]), f["referred_table"])
            for f in sa.inspect(sync).get_foreign_keys("model_call_lease", "runtime")
        })
        assert foreign == {(("run_id",), "run_record"), (("memory_job_id",), "memory_consolidation_job")}


@pytest.mark.parametrize("owner", ["none", "run_and_job", "run_and_probe", "job_and_probe",
                                   "run", "job", "probe"])
async def test_b210_owner_is_exactly_one_of_run_job_probe(
    async_tool_database: AsyncEngine, owner: str,
) -> None:
    lease = _models().ModelCallLease.__table__
    async with async_tool_database.begin() as connection:
        run_id, job_id = await _seed_owner_rows(connection)
        values = _lease(run_id=None, memory_job_id=None, probe_id=None)
        values.update({
            "none": {},
            "run_and_job": {"run_id": run_id, "memory_job_id": job_id},
            "run_and_probe": {"run_id": run_id, "probe_id": uuid4()},
            "job_and_probe": {"memory_job_id": job_id, "probe_id": uuid4()},
            "run": {"run_id": run_id},
            "job": {"memory_job_id": job_id},
            "probe": {"probe_id": uuid4()},
        }[owner])
        if owner in {"none", "run_and_job", "run_and_probe", "job_and_probe"}:
            await _insert(connection, lease, values)
        else:
            await connection.execute(lease.insert().values(**values))


async def test_b210_unknown_lease_retains_occupancy_and_cannot_be_hidden(async_tool_database: AsyncEngine) -> None:
    models = _models()
    lease, capacity = models.ModelCallLease.__table__, models.ModelCapacity.__table__
    key_resource_id = uuid4()
    async with async_tool_database.begin() as connection:
        await connection.execute(capacity.insert().values(
            tenant_id="b210", resource_type="KEY", resource_id=key_resource_id, inflight=1, reserved_tokens=128,
        ))
        lease_id = (await connection.execute(lease.insert().values(
            **_lease(key_resource_id=key_resource_id, state="ACTIVE")
        ).returning(lease.c.id))).scalar_one()
        # 远端是否停止未知：状态保留在 UNKNOWN，计量行继续占额。
        await connection.execute(lease.update().where(lease.c.id == lease_id).values(
            state="UNKNOWN", update_time=sa.func.now(),
        ))
        await _insert(connection, lease, _lease(tenant_id="b210", state="STALE"))
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(lease.update().where(lease.c.id == lease_id).values(
                    is_deleted=True, update_time=sa.func.now(),
                ))
        row = (await connection.execute(lease.select().where(lease.c.id == lease_id))).mappings().one()
        assert row["state"] == "UNKNOWN" and row["reserved_tokens"] == 128
        assert not row["is_deleted"]
        counter = (await connection.execute(capacity.select().where(
            capacity.c.resource_id == key_resource_id
        ))).mappings().one()
        assert counter["inflight"] == 1 and not counter["is_deleted"]
        # 活跃租约 partial index 覆盖 UNKNOWN：占额集合可被（key_resource_id）隔离查询命中。
        index = next(
            item for item in await connection.run_sync(
                lambda sync: sa.inspect(sync).get_indexes("model_call_lease", "runtime")
            ) if item["name"] == "ix_model_call_lease_active"
        )
        assert index["unique"] is False and tuple(index["column_names"]) == ("tenant_id", "key_resource_id")
        predicate = str(index["dialect_options"]["postgresql_where"])
        assert "ACTIVE" in predicate and "UNKNOWN" in predicate and "is_deleted" in predicate


async def test_b210_downgrade_on_empty_tables_returns_to_0021(
    async_tool_datastore: dict[str, str], tmp_path: Path,
) -> None:
    result = await _migrate(async_tool_datastore, tmp_path, direction="downgrade", target="0021")
    assert result.returncode == 0, result.stderr
    engine = create_async_engine(async_tool_datastore["database_url"])
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == "0021"
            tables = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names("runtime")))
            assert not (set(TABLES) & tables)
    finally:
        await engine.dispose()
    rerun = await _migrate(async_tool_datastore, tmp_path, direction="upgrade", target="head")
    assert rerun.returncode == 0, rerun.stderr


async def test_b210_downgrade_with_lease_rows_requires_backup(
    async_tool_datastore: dict[str, str], tmp_path: Path,
) -> None:
    models = _models()
    engine = create_async_engine(async_tool_datastore["database_url"])
    try:
        async with engine.begin() as connection:
            await connection.execute(models.ModelCallLease.__table__.insert().values(**_lease()))
            head = (await connection.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one()
    finally:
        await engine.dispose()
    result = await _migrate(async_tool_datastore, tmp_path, direction="downgrade", target="0021")
    assert result.returncode != 0 and "MODEL_CAPACITY_DOWNGRADE_REQUIRES_BACKUP" in result.stderr
    engine = create_async_engine(async_tool_datastore["database_url"])
    try:
        async with engine.begin() as connection:
            # 整链一个事务：0022 拒绝即回滚，链头与数据都保持原样（不钉死在具体 revision 号）。
            assert (await connection.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == head
            assert (await connection.execute(sa.text("SELECT count(*) FROM runtime.model_call_lease"))).scalar_one() == 1
    finally:
        await engine.dispose()
