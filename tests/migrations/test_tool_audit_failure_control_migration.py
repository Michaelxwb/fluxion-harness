"""Migration 0063: legacy audit rows stay unknown, new rows keep explicit false."""

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

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations/versions/0063_tool_audit_failure_control.py"


def _migration(directory: Path, database_url: str) -> Path:
    ini = directory / "tool-audit-control.ini"
    ini.write_text(
        "[alembic]\nscript_location = migrations\nprepend_sys_path = .\n"
        f"sqlalchemy.url = {database_url}\n"
    )
    return ini


async def _migrate(info: dict[str, str], directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    ini = _migration(directory, info["database_url"])
    return await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "migrations/db_migrate.py", "-c", str(ini), *args],
        capture_output=True,
        text=True,
        timeout=300,
    )


async def _columns(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'runtime' AND table_name = 'tool_call_audit'"
            )
        )
        return {str(row[0]) for row in rows}


def test_migration_revision_chain_and_nullable_columns() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0063"' in source
    assert 'down_revision = "0062"' in source
    for column in ("retryable", "retry_disposition", "agent_disposition", "diagnostic_json"):
        assert column in source
    assert "nullable=True" in source


async def test_legacy_rows_stay_null_and_new_false_is_distinct(
    datastore_at_revision, tmp_path: Path
) -> None:
    info = datastore_at_revision("0062")
    engine = create_async_engine(info["database_url"])
    legacy_id = uuid.uuid4()
    new_id = uuid.uuid4()
    tenant = f"audit-migration-{uuid.uuid4()}"
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO runtime.tool_call_audit "
                    "(id, tenant_id, run_id, conversation_id, tool_call_id, tool_name, "
                    " tool_kind, prepared_args_hash, status, start_time) "
                    "VALUES (:id, :tenant, :run, :conversation, 'call-legacy', 'execute', "
                    " 'SKILL', 'sha256:legacy', 'OK', :start)"
                ),
                {
                    "id": legacy_id,
                    "tenant": tenant,
                    "run": uuid.uuid4(),
                    "conversation": uuid.uuid4(),
                    "start": datetime.now(UTC),
                },
            )

        result = await _migrate(info, tmp_path, "upgrade", "0063")
        assert result.returncode == 0, result.stderr
        assert {
            "retryable",
            "retry_disposition",
            "agent_disposition",
            "diagnostic_json",
        } <= await _columns(engine)

        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO runtime.tool_call_audit "
                    "(id, tenant_id, run_id, conversation_id, tool_call_id, tool_name, "
                    " tool_kind, prepared_args_hash, status, start_time, retryable, "
                    " retry_disposition, agent_disposition, diagnostic_json) "
                    "VALUES (:id, :tenant, :run, :conversation, 'call-new', 'execute', "
                    " 'SKILL', 'sha256:new', 'ERROR', :start, false, 'BLOCK', 'STOP', "
                    " '{\"gate_reason\": \"BLOCKED_ATTEMPT\"}'::jsonb)"
                ),
                {
                    "id": new_id,
                    "tenant": tenant,
                    "run": uuid.uuid4(),
                    "conversation": uuid.uuid4(),
                    "start": datetime.now(UTC),
                },
            )
            legacy = (
                await connection.execute(
                    sa.text(
                        "SELECT retryable, retry_disposition, agent_disposition, diagnostic_json "
                        "FROM runtime.tool_call_audit WHERE id = :id"
                    ),
                    {"id": legacy_id},
                )
            ).one()
            new = (
                await connection.execute(
                    sa.text(
                        "SELECT retryable, retry_disposition, agent_disposition, diagnostic_json "
                        "FROM runtime.tool_call_audit WHERE id = :id"
                    ),
                    {"id": new_id},
                )
            ).one()
            revision = await connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            )
        assert tuple(legacy) == (None, None, None, None)
        assert new.retryable is False
        assert new.retry_disposition == "BLOCK"
        assert new.agent_disposition == "STOP"
        assert new.diagnostic_json == {"gate_reason": "BLOCKED_ATTEMPT"}
        assert revision == "0063"
    finally:
        await engine.dispose()
