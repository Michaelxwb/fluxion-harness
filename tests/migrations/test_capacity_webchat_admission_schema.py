"""B-211: real migration/ORM parity for admission policy, counters, and Run queue fields.

0023 建 `runtime.run_admission_policy` / `run_admission_counter`，并给 `run_record` 加排队投影
列：policy 每租户完整唯一（无 tombstone 分叉，版本只能原地单调前进）；USER/TENANT 计数完整
唯一且非负；排队 deadline partial index 覆盖全部非终态 Run；释放标记可随终态同事务写入。
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
TABLES = ("run_admission_policy", "run_admission_counter")
QUEUE_COLUMNS = ("queue_stage", "queue_reason", "queued_at", "queue_deadline_at", "admission_released_at")
NON_TERMINAL = ("CREATED", "RUNNING", "WAITING_TOOL", "WAITING_INPUT")
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
    ini = tmp_path / "admission.ini"
    ini.write_text("[alembic]\nscript_location = migrations\nprepend_sys_path = .\n"
                   f"sqlalchemy.url = {info['database_url']}\n")
    return await asyncio.to_thread(
        subprocess.run, [sys.executable, "migrations/db_migrate.py", "-c", str(ini), direction, target],
        capture_output=True, text=True, timeout=120,
    )


def _policy(tenant: str = "b211", **changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "tenant_id": tenant,
        "source_settings_revision": 5,
        "max_open_runs_per_tenant": 2000,
        "max_open_runs_per_user": 2,
    }
    values.update(changes)
    return values


async def _insert(connection: AsyncConnection, table: sa.Table, values: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await connection.execute(table.insert().values(**values))


async def _insert_run(connection: AsyncConnection, **fields: object) -> UUID:
    conversation, run_id = uuid4(), uuid4()
    await connection.execute(sa.text(
        "INSERT INTO runtime.conversation (id,tenant_id,user_id,agent_id) VALUES (:id,'b211',:user,:agent)"
    ), {"id": conversation, "user": uuid4(), "agent": uuid4()})
    values: dict[str, object] = {
        "id": run_id,
        "tenant_id": "b211",
        "conversation_id": conversation,
        "user_id": uuid4(),
        "agent_id": uuid4(),
        "status": "CREATED",
        "input_text": "queue",
        "trace_id": "b211-trace",
    }
    values.update(fields)
    await connection.execute(_models().RunRecord.__table__.insert().values(**values))
    return run_id


async def test_b211_empty_database_schema_parity(async_tool_database: AsyncEngine) -> None:
    models = _models()
    policy_table, counter_table = models.RunAdmissionPolicy.__table__, models.RunAdmissionCounter.__table__
    async with async_tool_database.begin() as connection:
        before = {name: await connection.run_sync(_structure, name) for name in (*TABLES, "run_record")}
        index = next(item for item in await connection.run_sync(
            lambda sync: sa.inspect(sync).get_indexes("run_record", "runtime")
        ) if item["name"] == "ix_run_record_queue_deadline")
        await connection.execute(sa.text("DROP SCHEMA runtime CASCADE"))
        await connection.execute(sa.text("CREATE SCHEMA runtime"))
        await connection.run_sync(models.Base.metadata.create_all)
        created = {name: await connection.run_sync(_structure, name) for name in TABLES}
    migrated = {name: before[name] for name in TABLES}
    assert migrated == created
    for name in TABLES:
        assert STANDARD <= set(_columns(migrated[name]))
    run_columns = _columns(before["run_record"])
    assert set(QUEUE_COLUMNS) <= set(run_columns)
    assert set(QUEUE_COLUMNS) <= set(models.RunRecord.__table__.columns.keys())
    # 排队时间列与运行裁决一样用带时区时间；状态码/释放标记可空。
    for name, expected in (("queue_stage", "VARCHAR(24)"), ("queue_reason", "VARCHAR(64)")):
        assert run_columns[name][1] == expected and run_columns[name][2] is True
    for name in ("queued_at", "queue_deadline_at", "admission_released_at"):
        assert run_columns[name][4] is True and run_columns[name][2] is True
    assert "ck_run_record_queue_stage" in {check[0] for check in before["run_record"][2]}
    # deadline partial index 只覆盖全部非终态的等待行（排队投影不跨终态）。
    assert tuple(index["column_names"]) == ("queue_deadline_at",) and index["unique"] is False
    predicate = str(index["dialect_options"]["postgresql_where"])
    assert "queue_stage IS NOT NULL" in predicate and "is_deleted = false" in predicate
    assert all(status in predicate for status in NON_TERMINAL)
    assert policy_table.name == "run_admission_policy" and counter_table.name == "run_admission_counter"


async def test_b211_policy_is_single_row_per_tenant_and_version_moves_in_place(
    async_tool_database: AsyncEngine,
) -> None:
    policy = _models().RunAdmissionPolicy.__table__
    async with async_tool_database.begin() as connection:
        await connection.execute(policy.insert().values(**_policy()))
        await _insert(connection, policy, _policy())  # 完整唯一：同租户不得分叉出第二版本行
        applied = await connection.execute(policy.update().where(
            policy.c.tenant_id == "b211", policy.c.source_settings_revision < 8
        ).values(source_settings_revision=8, update_time=sa.func.now()))
        assert applied.rowcount == 1
        stale = await connection.execute(policy.update().where(
            policy.c.tenant_id == "b211", policy.c.source_settings_revision < 3
        ).values(source_settings_revision=3, update_time=sa.func.now()))
        assert stale.rowcount == 0  # 旧实例拿着旧阈值覆盖不生效：版本原地单调前进
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(policy.update().where(policy.c.tenant_id == "b211").values(
                    is_deleted=True, update_time=sa.func.now(),
                ))
        rows = (await connection.execute(policy.select())).mappings().all()
        assert len(rows) == 1 and rows[0]["source_settings_revision"] == 8
        for changes in ({"max_open_runs_per_tenant": 1}, {"max_open_runs_per_tenant": 100001},
                        {"max_open_runs_per_user": 1}, {"max_open_runs_per_user": 101},
                        {"max_open_runs_per_tenant": 99, "max_open_runs_per_user": 100},
                        {"source_settings_revision": -1}):
            await _insert(connection, policy, {**_policy(tenant=uuid4().hex), **changes})
        # 配置契约的边界值必须被接受（2～100000 / 2～100 且 ≤租户上限）。
        await connection.execute(policy.insert().values(**_policy(
            tenant="b211-low", max_open_runs_per_tenant=2, max_open_runs_per_user=2)))
        await connection.execute(policy.insert().values(**_policy(
            tenant="b211-high", max_open_runs_per_tenant=100000, max_open_runs_per_user=100)))


async def test_b211_counters_are_complete_unique_per_scope_and_non_negative(
    async_tool_database: AsyncEngine,
) -> None:
    counter = _models().RunAdmissionCounter.__table__
    user_key = str(uuid4())
    async with async_tool_database.begin() as connection:
        tenant_row = (await connection.execute(counter.insert().values(
            tenant_id="b211", scope_type="TENANT", scope_key="b211",
        ).returning(counter.c.id))).scalar_one()
        await connection.execute(counter.insert().values(
            tenant_id="b211", scope_type="USER", scope_key=user_key, open_run_count=2,
        ))
        fresh = (await connection.execute(counter.select().where(counter.c.id == tenant_row))).mappings().one()
        assert (fresh["open_run_count"], fresh["revision"]) == (0, 1)
        await _insert(connection, counter, {
            "tenant_id": "b211", "scope_type": "TENANT", "scope_key": "b211",
        })  # 完整唯一：同租户同 scope 不得出现第二行计数
        await connection.execute(counter.update().where(counter.c.id == tenant_row).values(
            is_deleted=True, update_time=sa.func.now(),
        ))
        await _insert(connection, counter, {
            "tenant_id": "b211", "scope_type": "TENANT", "scope_key": "b211",
        })  # tombstone 仍在完整唯一键内：删除重录不重置计数身份
        for changes in ({"scope_type": "POD"}, {"open_run_count": -1}, {"revision": 0}):
            await _insert(connection, counter, {
                "tenant_id": "b211", "scope_type": "TENANT", "scope_key": uuid4().hex, **changes,
            })
        # 计数随终态释放回退仍非负；Run 在飞集合由 Run 行本身决定。
        released = await connection.execute(counter.update().where(
            counter.c.scope_key == user_key
        ).values(open_run_count=1, revision=counter.c.revision + 1, update_time=sa.func.now()))
        assert released.rowcount == 1
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(counter.update().where(counter.c.scope_key == user_key).values(
                    open_run_count=-1, update_time=sa.func.now(),
                ))


async def test_b211_queue_fields_only_expose_waiting_stages_and_release_marker(
    async_tool_database: AsyncEngine,
) -> None:
    run = _models().RunRecord.__table__
    async with async_tool_database.begin() as connection:
        run_id = await _insert_run(
            connection,
            queue_stage="WAITING_RUNTIME",
            queue_reason="RUN_CAPACITY_EXCEEDED",
            queued_at=datetime.now(timezone.utc),
            queue_deadline_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        )
        row = (await connection.execute(run.select().where(run.c.id == run_id))).mappings().one()
        assert row["queue_stage"] == "WAITING_RUNTIME" and row["admission_released_at"] is None
        assert row["queue_deadline_at"] > row["queued_at"]
        for stage in ("WAITING_TOOL", "RUNNING", "COMPLETED"):
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(run.update().where(run.c.id == run_id).values(
                        queue_stage=stage, update_time=sa.func.now(),
                    ))  # 队列投影只表达等待阶段，不复制执行/工具状态
        await connection.execute(run.update().where(run.c.id == run_id).values(
            queue_stage="WAITING_MODEL", queue_reason="MODEL_CAPACITY_UNAVAILABLE",
            queue_deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            update_time=sa.func.now(),
        ))
        await connection.execute(run.update().where(run.c.id == run_id).values(
            status="COMPLETED", queue_stage=None, admission_released_at=sa.func.now(),
            update_time=sa.func.now(),
        ))  # 终态同事务清排队投影并写释放标记
        released = (await connection.execute(run.select().where(run.c.id == run_id))).mappings().one()
        assert released["status"] == "COMPLETED" and released["queue_stage"] is None
        assert released["admission_released_at"] is not None


@pytest.mark.parametrize("blocker", ["counter", "waiting"])
async def test_b211_downgrade_requires_drain_for_live_admission_state(
    async_tool_datastore: dict[str, str], tmp_path: Path, blocker: str,
) -> None:
    models = _models()
    engine = create_async_engine(async_tool_datastore["database_url"])
    try:
        async with engine.begin() as connection:
            if blocker == "counter":
                await connection.execute(models.RunAdmissionCounter.__table__.insert().values(
                    tenant_id="b211", scope_type="TENANT", scope_key="b211", open_run_count=1,
                ))
            else:
                await _insert_run(
                    connection, queue_stage="WAITING_RUNTIME", queue_reason="RUN_CAPACITY_EXCEEDED",
                    queued_at=datetime.now(timezone.utc),
                    queue_deadline_at=datetime.now(timezone.utc) + timedelta(seconds=60),
                )
            head = (await connection.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one()
    finally:
        await engine.dispose()
    result = await _migrate(async_tool_datastore, tmp_path, direction="downgrade", target="0022")
    assert result.returncode != 0 and "RUN_ADMISSION_DOWNGRADE_REQUIRES_DRAIN" in result.stderr
    engine = create_async_engine(async_tool_datastore["database_url"])
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == head
            tables = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names("runtime")))
            assert set(TABLES) <= tables
            columns = await connection.run_sync(
                lambda sync: {c["name"] for c in sa.inspect(sync).get_columns("run_record", "runtime")})
            assert set(QUEUE_COLUMNS) <= columns
    finally:
        await engine.dispose()


async def test_b211_downgrade_on_pristine_schema_returns_to_0022(
    async_tool_datastore: dict[str, str], tmp_path: Path,
) -> None:
    result = await _migrate(async_tool_datastore, tmp_path, direction="downgrade", target="0022")
    assert result.returncode == 0, result.stderr
    engine = create_async_engine(async_tool_datastore["database_url"])
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == "0022"
            tables = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names("runtime")))
            assert not (set(TABLES) & tables)
            columns = await connection.run_sync(
                lambda sync: {c["name"] for c in sa.inspect(sync).get_columns("run_record", "runtime")})
            assert not (set(QUEUE_COLUMNS) & columns)
    finally:
        await engine.dispose()
    rerun = await _migrate(async_tool_datastore, tmp_path, direction="upgrade", target="head")
    assert rerun.returncode == 0, rerun.stderr
