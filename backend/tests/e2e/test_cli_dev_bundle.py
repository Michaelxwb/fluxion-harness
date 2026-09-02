from __future__ import annotations

import asyncio
import json
from pathlib import Path

from typer.testing import CliRunner

from fluxion.cli.main import app
from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import (
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
    # ADR-A008：模型名取自自举 ModelDefinition（name="echo"），provider 仍 dev.echo。
    assert payload["data"]["output"] == "echo: hello"
    assert payload["data"]["runtime_profile_id"] == "assistant"
    assert payload["data"]["runtime_profile_version"] == "1"
    assert payload["data"]["model_provider_id"] == "dev.echo"
    assert payload["data"]["trace_id"]

    profile = asyncio.run(_load_profile(dsn))
    assert profile is not None
    assert profile.status is ResourceStatus.PUBLISHED
    # ADR-012 / TASK-A104：mechanics-only spec；persona/model 在自举的同名
    # AgentDefinition（_ensure_default_agent），ADR-A008 三层链指向自举的
    # ModelDefinition（model.dev-echo → provider dev.echo）。
    assert profile.spec_json["request_timeout_ms"] == 1_000
    agent = asyncio.run(_load_agent(dsn))
    assert agent is not None
    from fluxion.agents.definitions import AgentDefinition

    assert (
        AgentDefinition.model_validate(agent.spec_json).model_policy.primary_model_ref.id
        == "model.dev-echo"
    )


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


async def _load_agent(dsn: str) -> ResourceDefinition | None:
    store = SQLiteRegistryStore(dsn)
    await store.initialize()
    try:
        return await store.get(
            ResourceKind.AGENT_DEFINITION,
            "assistant",
            tenant_id="tenant-a",
        )
    finally:
        await store.close()
