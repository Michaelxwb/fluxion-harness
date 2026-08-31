from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from tests.runtime_helpers import publish_resource

from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import ResourceBinding, ResourceKind
from fluxion.runtime.secrets import CredentialResolver, LocalEncryptedSecretStore
from fluxion.services.runtime_app import RuntimeApplicationService
from fluxion.services.runtime_contracts import RunRuntimeRequest

TOOL_ID = "mcp__live_lookup__lookup"


@pytest.mark.asyncio
async def test_S_P13_07_live_openai_compatible_model_calls_real_mcp(
    tmp_path: Path,
) -> None:
    if os.environ.get("FLUXION_LIVE_MODEL_SMOKE") != "1":
        pytest.skip("set FLUXION_LIVE_MODEL_SMOKE=1 to run external model smoke")
    base_url = _required_env("FLUXION_LIVE_MODEL_BASE_URL")
    api_key = _required_env("FLUXION_LIVE_MODEL_API_KEY")
    model = _required_env("FLUXION_LIVE_MODEL_NAME")
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    secrets = LocalEncryptedSecretStore(master_key=os.urandom(32))
    credential_ref = await secrets.put("dev", "live-model", api_key)
    call_log = tmp_path / "live-mcp-call.log"
    fixture = Path(__file__).parents[1] / "fixtures" / "mcp_product_server.py"
    await store.initialize()
    try:
        await _seed_live_product(
            store,
            base_url=base_url,
            model=model,
            credential_ref=credential_ref,
            fixture=fixture,
            call_log=call_log,
        )
        runtime = RuntimeApplicationService(
            store,
            credential_resolver=CredentialResolver(secrets),
        )
        result = await runtime.run(
            RunRuntimeRequest(
                tenant_id="dev",
                user_id="live-user",
                runtime_profile_id="live-assistant",
                session_id="live-smoke",
                input_message="调用 live_lookup 查询 fluxion，然后基于工具结果回答。",
            )
        )
        trace = await runtime.trace_store.get(result.trace_id)
        assert trace is not None
        assert call_log.read_text(encoding="utf-8") == "fluxion"
        assert result.output.strip()
        assert any(tool.get("tool_id") == TOOL_ID for tool in trace.tools)
        print(
            "LIVE_SMOKE_EVIDENCE="
            + json.dumps(
                {
                    "model_provider": result.model_provider_id,
                    "runtime_profile_version": result.runtime_profile_version,
                    "tool_ids": [tool.get("tool_id") for tool in trace.tools],
                    "output_length": len(result.output),
                    "trace_id": result.trace_id,
                },
                ensure_ascii=False,
            )
        )
    finally:
        await store.close()


async def _seed_live_product(
    store: SQLiteRegistryStore,
    *,
    base_url: str,
    model: str,
    credential_ref: str,
    fixture: Path,
    call_log: Path,
) -> None:
    await publish_resource(
        store,
        tenant_id="dev",
        kind=ResourceKind.PLUGIN,
        resource_id="live-provider",
        version="1",
        spec={
            "name": "live-provider",
            "plugin_type": "model_provider",
            "protocol": "openai_compatible",
            "base_url": base_url,
            "model": model,
            "request_timeout_ms": 60_000,
            "max_retries": 1,
        },
    )
    await publish_resource(
        store,
        tenant_id="dev",
        kind=ResourceKind.MCP,
        resource_id="live_lookup",
        version="1",
        spec={
            "name": "live_lookup",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(fixture)],
            "env": {"MCP_TEST_CALL_LOG": str(call_log)},
            "timeout_ms": 10_000,
            "allowed_tools": ["lookup"],
        },
    )
    await publish_resource(
        store,
        tenant_id="dev",
        kind=ResourceKind.SKILL,
        resource_id="force-live-lookup",
        version="1",
        spec={
            "name": "force-live-lookup",
            "instructions": "必须先调用 mcp__live_lookup__lookup，query 必须是 fluxion。",
            "required_capabilities": [TOOL_ID],
        },
    )
    await publish_resource(
        store,
        tenant_id="dev",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="live-assistant",
        version="1",
        spec={
            "prompt": "You are a tool-using agent.",
            "model_policy": {
                "provider": "live-provider",
                "model": model,
                "timeout_ms": 60_000,
                "deadline_ms": 120_000,
                "max_rounds": 4,
            },
            "plugin_bindings": ["live-provider@1"],
            "allowed_skills": ["force-live-lookup@1"],
            "allowed_mcps": ["live_lookup@1"],
            "allowed_tools": [TOOL_ID],
        },
    )
    for binding_id, resource_type, resource_id, ref in (
        ("bind-live-provider", ResourceKind.PLUGIN, "live-provider", credential_ref),
        ("bind-live-skill", ResourceKind.SKILL, "force-live-lookup", None),
        ("bind-live-mcp", ResourceKind.MCP, "live_lookup", None),
    ):
        await store.put_binding(
            ResourceBinding(
                binding_id=binding_id,
                tenant_id="dev",
                subject_type="user",
                subject_id="live-user",
                resource_type=resource_type,
                resource_id=resource_id,
                resource_version_selector="1",
                credential_ref=ref,
            )
        )


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        pytest.fail(f"{name} is required when FLUXION_LIVE_MODEL_SMOKE=1")
    return value
