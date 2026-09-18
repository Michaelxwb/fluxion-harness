"""S-04/E-03：密钥明文迁移（真实 Alembic/PostgreSQL/backfill CLI）。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from muad_common import SharedSettings
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

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


def _run_alembic(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": _database_url()}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "migrations/alembic.ini", *args],
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


def test_s04_migration_chain_has_single_head_0005() -> None:
    graph = _migration_graph()
    heads = sorted(set(graph) - {down for down in graph.values() if down})
    assert heads == ["0005"], f"unexpected migration heads: {heads}"


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

    assert revision == "0005"
    assert all(NEW_COLUMNS[table] in tables[table] for table in NEW_COLUMNS)
    leftovers = [f"{table}.{column}" for table, column in OLD_COLUMNS.items() if column in tables[table]]
    assert leftovers == [], f"contract 后旧列残留: {leftovers}"


async def test_e03_backfill_reports_unresolved_refs_and_fails_fast() -> None:
    legacy_ref = "secret://e2e/definitely-missing"
    tenant_id = SharedSettings().default_tenant_id
    suffix = uuid.uuid4().hex[:8]
    model_id, agent_id, bot_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(_database_url())
    try:
        _run_alembic("downgrade", "0004")
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
        env["DATABASE_URL"] = _database_url()
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
        _run_alembic("upgrade", "head", check=False)
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM control.bot_account WHERE id = :id"), {"id": bot_id}
            )
            await connection.execute(
                text("DELETE FROM control.agent_definition WHERE id = :id"), {"id": agent_id}
            )
            await connection.execute(
                text("DELETE FROM control.model_definition WHERE id = :id"), {"id": model_id}
            )
        await engine.dispose()
