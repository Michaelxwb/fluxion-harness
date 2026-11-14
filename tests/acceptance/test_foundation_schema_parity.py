from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.datastores import create_datastore, drop_datastore

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "migrations/versions"
PARITY_FILE = "tests/console_platform/test_schema_parity.py"
DRIFT_COLUMN = "e2e_drift_probe"

ORM_SUITES_PARITY = (
    "tests/console_platform/test_schema_parity.py",
    "tests/console_internal/test_resolve_schema_parity.py",
    "tests/console_channel/test_channel_schema_parity.py",
    "tests/agent_runtime/test_runtime_schema_parity.py",
    "tests/agent_worker/test_task_schema_parity.py",
)


def _migration_graph() -> dict[str, str | None]:
    graph: dict[str, str | None] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision = "([^"]+)"', source, re.M)
        down = re.search(r'^down_revision = (?:None|"([^"]+)")', source, re.M)
        assert revision, f"missing revision in {path.name}"
        graph[revision.group(1)] = down.group(1) if down else None
    return graph


def _run_parity_probe(database_url: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        # 探针必须看到被注入漂移的那一份库：关掉每测试克隆，否则它读到的是干净模板。
        "MUAD_ACCEPTANCE_SHARED_DB": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", PARITY_FILE, "-k", "agent_definition"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


async def _execute(database_url: str, statement: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


async def test_e05_migration_chain_has_single_head() -> None:
    graph = _migration_graph()
    heads = sorted(set(graph) - {down for down in graph.values() if down})
    assert len(heads) == 1, f"unexpected migration heads: {heads}"
    assert heads[0] >= "0008"
    chain: list[str] = []
    current: str | None = heads[0]
    while current:
        chain.append(current)
        current = graph[current]
    assert chain[-1] == "0001" and len(chain) == len(graph), f"unexpected migration chain: {chain}"


async def test_e05_every_orm_suite_has_parity_tests() -> None:
    missing = [path for path in ORM_SUITES_PARITY if not (ROOT / path).is_file()]
    assert missing == [], f"missing schema parity suites: {missing}"


async def test_e05_parity_suite_detects_injected_column_drift() -> None:
    info = create_datastore()
    database_url = info["database_url"]
    try:
        await _execute(
            database_url,
            f"ALTER TABLE control.agent_definition ADD COLUMN IF NOT EXISTS {DRIFT_COLUMN} text",
        )
        drifted = _run_parity_probe(database_url)
        assert drifted.returncode != 0, (
            "parity suite did not detect the injected column drift\n"
            f"stdout={drifted.stdout[-800:]}\nstderr={drifted.stderr[-400:]}"
        )
        await _execute(
            database_url,
            f"ALTER TABLE control.agent_definition DROP COLUMN IF EXISTS {DRIFT_COLUMN}",
        )
        restored = _run_parity_probe(database_url)
        assert restored.returncode == 0, f"parity suite still failing after repair: {restored.stdout[-800:]}"
    finally:
        drop_datastore(info["name"], info["admin_database_url"], info["redis_url"])
