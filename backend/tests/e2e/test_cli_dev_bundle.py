from __future__ import annotations

import asyncio
import json
from pathlib import Path

from typer.testing import CliRunner

from fluxion.cli.main import app
from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import (
    ModelPolicy,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
)


def test_S_R12_cli_bootstraps_runtime_profile_and_runs_without_console(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "fluxion-dev.db"
    dsn = f"sqlite+aiosqlite:///{db_path}"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run",
            "--agent",
            "assistant",
            "--input",
            "hello",
            "--tenant",
            "tenant-a",
            "--user",
            "user-a",
            "--session",
            "session-a",
            "--registry-dsn",
            dsn,
            "--bootstrap",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["code"] == "ok"
    assert payload["message"] == "ok"
    assert payload["request_id"]
    assert payload["data"]["output"] == "dev: hello"
    assert payload["data"]["runtime_profile_id"] == "assistant"
    assert payload["data"]["runtime_profile_version"] == "1"
    assert payload["data"]["model_provider_id"] == "dev.echo"
    assert payload["data"]["trace_id"]

    profile = asyncio.run(_load_profile(dsn))
    assert profile is not None
    assert profile.status is ResourceStatus.PUBLISHED
    # ADR-012：spec 由 RuntimeProfile model_dump 生成，model_policy 含默认值
    # 完整落库（failover/deadline_ms/max_rounds），读取端可与 ModelPolicy 精确比对。
    assert profile.spec_json["model_policy"] == ModelPolicy(
        provider="dev.echo", model="dev", timeout_ms=1000
    ).model_dump(mode="json")


async def _load_profile(dsn: str) -> ResourceDefinition | None:
    store = SQLiteRegistryStore(dsn)
    await store.initialize()
    try:
        return await store.get(
            ResourceKind.RUNTIME_PROFILE,
            "assistant",
            tenant_id="tenant-a",
        )
    finally:
        await store.close()
