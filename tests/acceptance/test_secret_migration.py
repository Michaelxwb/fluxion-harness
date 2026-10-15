"""S-04/E-03：密钥明文迁移（真实 Alembic/PostgreSQL/backfill CLI）。

E-03 的降级场景在**显式 `revision="0004"` 的独立临时库**上执行（`tests.acceptance.datastores`
与应用验收同一实现）：0020 是记忆重设计的单向维护迁移，`downgrade` 按设计拒绝回滚
（design §4.2），head 库上无法再降到 0004；共享库/会话库因此不再被整库改 schema。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from muad_common import SharedSettings
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.datastores import create_datastore, drop_datastore

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "migrations/versions"

NEW_COLUMNS = {
    "model_definition": "api_key",
    "bot_account": "secret",
    "mcp_server": "auth_secret",
    "project_platform": "auth_secret",
    "user_credential_ref": "credential_json",
    "shared_credential_ref": "credential_json",
}
OLD_COLUMNS = {
    "model_definition": "secret_ref",
    "bot_account": "secret_ref",
    "mcp_server": "auth_secret_ref",
    "user_credential_ref": "secret_ref",
    "shared_credential_ref": "secret_ref",
}


def _database_url() -> str:
    url = SharedSettings().database_url
    if not url:
        pytest.skip("DATABASE_URL not configured")
    return url


def _alembic_ini() -> Path:
    """迁移配置**只认 ini**（`migrations/env.py` 不再读环境变量）：
    按本测试实际要打的库现场生成一份，保证 alembic 子进程与断言/DML 用的是同一个库。
    （靠环境变量驱动 alembic 的写法在 ini 与 DATABASE_URL 不一致时会让降级落到另一个库。）
    """
    ini = Path(tempfile.gettempdir()) / f"alembic-secret-migration-{os.getpid()}.ini"
    ini.write_text(
        "[alembic]\nscript_location = migrations\nprepend_sys_path = .\n"
        f"sqlalchemy.url = {_database_url()}\n",
        encoding="utf-8",
    )
    return ini


def _run_alembic(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": _database_url()}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(_alembic_ini()), *args],
        cwd=ROOT,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def _column_names(sync_connection, table: str) -> set[str]:
    return {column["name"] for column in inspect(sync_connection).get_columns(table, schema="control")}


def _migration_graph() -> dict[str, str | None]:
    graph: dict[str, str | None] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision = "([^"]+)"', source, re.M)
        down = re.search(r'^down_revision = (?:None|"([^"]+)")', source, re.M)
        if revision:
            graph[revision.group(1)] = down.group(1) if down else None
    return graph


def test_s04_migration_chain_has_single_head() -> None:
    graph = _migration_graph()
    heads = sorted(set(graph) - {down for down in graph.values() if down})
    assert len(heads) == 1, f"unexpected migration heads: {heads}"
    assert heads[0] >= "0008"


async def test_s04_contract_state_has_plaintext_columns_only() -> None:
    _run_alembic("upgrade", "head")
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            tables = await connection.run_sync(
                lambda sync: {
                    table: _column_names(sync, table)
                    for table in set(NEW_COLUMNS) | set(OLD_COLUMNS)
                }
            )
    finally:
        await engine.dispose()

    heads = sorted(set(_migration_graph()) - {down for down in _migration_graph().values() if down})
    assert revision == heads[0]
    assert all(NEW_COLUMNS[table] in tables[table] for table in NEW_COLUMNS)
    leftovers = [f"{table}.{column}" for table, column in OLD_COLUMNS.items() if column in tables[table]]
    assert leftovers == [], f"contract 后旧列残留: {leftovers}"


async def test_e03_backfill_reports_unresolved_refs_and_fails_fast() -> None:
    """回填 CLI 遇未解析的旧 `secret_ref` 必须 fail-fast（exit 1 + 明确列出）。

    旧 schema（`secret_ref` 列）由**显式 revision 的独立临时库**承载：0020 是记忆重设计的
    单向维护迁移，`downgrade` 按设计拒绝回滚（design §4.2），head 库上再也降不回 0004
    （同 TASK-032 的 `test_platform_settings_table.py` 口径）。降级只打临时库，共享库不受影响。
    """
    legacy_ref = "secret://e2e/definitely-missing"
    tenant_id = SharedSettings().default_tenant_id
    suffix = uuid.uuid4().hex[:8]
    model_id, agent_id, bot_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    info = create_datastore(prefix="muad_sec_", revision="0004")
    engine = create_async_engine(info["database_url"])
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO control.model_definition "
                    "(id, tenant_id, key, name, model_id, base_url, enabled) "
                    "VALUES (:id, :tenant_id, :key, 'Backfill Model', 'gpt-4o-mini', "
                    "'https://api.example.com/v1', true)"
                ),
                {"id": model_id, "tenant_id": tenant_id, "key": f"backfill-model-{suffix}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO control.agent_definition "
                    "(id, tenant_id, key, name, instructions, model_id, "
                    "runtime_config_json, enabled, revision) "
                    "VALUES (:id, :tenant_id, :key, 'Backfill Agent', 'help', :model_id, "
                    "'{}'::jsonb, true, 1)"
                ),
                {
                    "id": agent_id,
                    "tenant_id": tenant_id,
                    "key": f"backfill-agent-{suffix}",
                    "model_id": model_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO control.bot_account "
                    "(id, tenant_id, channel, name, bot_id, secret_ref, agent_id) "
                    "VALUES (:id, :tenant_id, 'WECOM', 'Backfill Bot', :bot_id, :secret_ref, :agent_id)"
                ),
                {
                    "id": bot_id,
                    "tenant_id": tenant_id,
                    "bot_id": f"backfill-{suffix}",
                    "secret_ref": legacy_ref,
                    "agent_id": agent_id,
                },
            )

        env = {key: value for key, value in os.environ.items() if not key.startswith("MUAD_SECRET__")}
        env["DATABASE_URL"] = info["database_url"]
        cli = subprocess.run(
            [sys.executable, "-m", "muad_console_platform.cli", "backfill-secrets"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert cli.returncode == 1, cli.stdout + cli.stderr
        assert "unresolved secret refs" in cli.stdout
        assert str(bot_id) in cli.stdout
    finally:
        await engine.dispose()
        # 临时库整体丢弃（含旧 schema 与插入的旧行），共享库与本会话库都不需要回滚。
        drop_datastore(info["name"], info["admin_database_url"], info["redis_url"])
