"""[平台设置表与迁移] B-07：真实 PostgreSQL + 真实 alembic 的建表 / 回滚往返。

**隔离红线（本文件最重要的约束）**：`downgrade` 只在 `create_datastore()` 建出的**临时库**
（`muad_pst_<uuid>`，用例结束 FORCE drop）上执行，**绝不**对共享 dev 库 `muad` 回滚 ——
那里有用户的开发数据。临时库的建库 / 迁移 / 丢弃复用 `tests/acceptance/datastores.py`
（与应用验收同一实现，避免两份建库逻辑漂移）。

**列 / 索引的对等比较复用 `test_schema_parity.py` 的 `_compare`**：同一条比较实现既服务于
共享库上的 parity 用例，也服务于这里的临时库往返，两处不会漂移。
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from muad_console_platform.infrastructure.models.control import PlatformSetting
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.datastores import create_datastore, drop_datastore
from tests.console_platform.test_schema_parity import _compare

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "control"
TABLE = "platform_setting"
REVISION = "0019"
PARENT_REVISION = "0016"
# 设计 §3.3 的列清单：四列口径（id/is_deleted/create_time/update_time）+ 四个业务列。
EXPECTED_COLUMNS = frozenset(
    {
        "id",
        "tenant_id",
        "revision",
        "settings_json",
        "actor_user_id",
        "is_deleted",
        "create_time",
        "update_time",
    }
)


@pytest.fixture(scope="module")
def isolated_db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    """独立临时库（由 create_datastore 迁到 head）+ 只含该 DSN 的 alembic ini。

    迁移脚本只认 ini、不读环境变量（`migrations/_config.py`），所以往返必须自带一份 ini
    指向临时库 —— 这同时保证 `downgrade` 打不到共享 dev 库。
    """
    info = create_datastore(prefix="muad_pst_")
    ini = tmp_path_factory.mktemp("platform-setting") / "alembic.pst.ini"
    ini.write_text(
        "[alembic]\n"
        "script_location = migrations\n"
        "prepend_sys_path = .\n"
        f"sqlalchemy.url = {info['database_url']}\n",
        encoding="utf-8",
    )
    try:
        yield {
            "url": info["database_url"],
            "ini": str(ini),
            "name": info["name"],
            "admin": info["admin_database_url"],
            "redis": info["redis_url"],
        }
    finally:
        # FORCE 断连后丢弃临时库，并归还本次占用的 Redis 号位。
        drop_datastore(info["name"], info["admin_database_url"], info["redis_url"])


def _alembic(ini: str, *args: str) -> None:
    completed = subprocess.run(
        [sys.executable, "migrations/db_migrate.py", "-c", ini, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, (
        f"alembic {' '.join(args)} 失败：\n{completed.stdout[-2000:]}\n{completed.stderr[-2000:]}"
    )


async def _run(url: str, statement: str, **params: Any) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text(statement), params)
    finally:
        await engine.dispose()


async def _reflect(url: str) -> dict[str, Any]:
    """→ 反射结果；表不存在时返回 `{}`（由调用方断言）。"""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:

            def probe(sync_connection: sa.Connection) -> dict[str, Any]:
                inspector = inspect(sync_connection)
                if not inspector.has_table(TABLE, schema=SCHEMA):
                    return {}
                return {
                    "columns": inspector.get_columns(TABLE, schema=SCHEMA),
                    "primary_key": inspector.get_pk_constraint(TABLE, schema=SCHEMA),
                    "foreign_keys": inspector.get_foreign_keys(TABLE, schema=SCHEMA),
                    "indexes": inspector.get_indexes(TABLE, schema=SCHEMA),
                }

            return await connection.run_sync(probe)
    finally:
        await engine.dispose()


async def _alembic_version(url: str) -> str:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
    finally:
        await engine.dispose()
    return str(value)


async def test_upgrade_to_head_creates_table_matching_orm_model(isolated_db: dict[str, str]) -> None:
    """`alembic upgrade 0001→0019` 后的列 / 主键 / 外键 / 索引与 ORM model 定义一致。"""
    reflected = await _reflect(isolated_db["url"])
    assert reflected, f"{SCHEMA}.{TABLE} 在迁到 head 后不存在（迁移未建表）"
    assert await _alembic_version(isolated_db["url"]) == REVISION

    db_columns = {str(item["name"]) for item in reflected["columns"]}
    assert db_columns == EXPECTED_COLUMNS
    assert [str(name) for name in reflected["primary_key"]["constrained_columns"]] == ["id"]
    # actor_user_id 是 console_account.id 的**逻辑引用**（设计 §3.3「不建物理 FK」）
    assert reflected["foreign_keys"] == []

    diffs = _compare(PlatformSetting.__table__, reflected)
    assert not diffs, "platform_setting 与 model 定义不一致：\n" + "\n".join(diffs)


async def test_partial_unique_rejects_duplicate_tenant_revision(
    isolated_db: dict[str, str],
) -> None:
    """真实 partial unique：同一 `(tenant_id, revision)` 的第二行被拒；软删行不占约束。"""
    tenant_id = "test-partial-unique"
    insert = (
        "INSERT INTO control.platform_setting (tenant_id, revision, settings_json) "
        "VALUES (:tenant_id, :revision, '{}'::jsonb)"
    )
    await _run(isolated_db["url"], insert, tenant_id=tenant_id, revision=1)

    with pytest.raises(IntegrityError):
        await _run(isolated_db["url"], insert, tenant_id=tenant_id, revision=1)

    # 软删掉第一行后，同一 `(tenant_id, revision)` 不再被约束挡住 ⇒ 谓词确实是 partial。
    await _run(
        isolated_db["url"],
        "UPDATE control.platform_setting SET is_deleted = true, update_time = now() "
        "WHERE tenant_id = :tenant_id AND revision = 1",
        tenant_id=tenant_id,
    )
    await _run(isolated_db["url"], insert, tenant_id=tenant_id, revision=1)


async def test_downgrade_drops_table_and_upgrade_restores(isolated_db: dict[str, str]) -> None:
    """`downgrade 0016` 干净删表，`upgrade 0019` 可再次建出同形的表（真实往返）。"""
    ini = isolated_db["ini"]
    try:
        _alembic(ini, "downgrade", PARENT_REVISION)
        assert await _alembic_version(isolated_db["url"]) == PARENT_REVISION
        assert await _reflect(isolated_db["url"]) == {}

        _alembic(ini, "upgrade", REVISION)
        reflected = await _reflect(isolated_db["url"])
        assert reflected, "回滚后再升级未重新建表"
        assert await _alembic_version(isolated_db["url"]) == REVISION
        assert _compare(PlatformSetting.__table__, reflected) == []
    finally:
        # 无论断言是否失败，都把临时库还原到 head，避免影响模块内其它用例。
        _alembic(ini, "upgrade", REVISION)
