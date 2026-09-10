from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceBinding, ResourceKind
from fluxion.runtime.secrets import CredentialResolver, LocalEncryptedSecretStore
from fluxion.services.runtime_app import RuntimeApplicationService
from fluxion.services.runtime_contracts import RunRuntimeRequest
from tests.runtime_helpers import TEST_POSTGRES_DSN, publish_resource

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
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    secrets = LocalEncryptedSecretStore(master_key=os.urandom(32))
    credential_ref = await secrets.put("dev", "live-model", api_key)
    call_log = tmp_path / "live-mcp-call.log"
    # TASK-005：stdio 已删除，live 烟雾改用进程内 streamable-http fixture。
    from mcp.server import MCPServer

    live_server = MCPServer("fluxion-live-fixture")

    @live_server.tool()
    def lookup(query: str) -> dict[str, str]:
        call_log.write_text(query, encoding="utf-8")
        return {"answer": f"live {query} result"}

    from tests.integration.test_mcp_governance import _UvicornMCPServer

    fixture_server = _UvicornMCPServer(
        live_server.streamable_http_app(json_response=True, stateless_http=True)
    )
    await fixture_server.start()
    await store.initialize()
    try:
        await _seed_live_product(
            store,
            base_url=base_url,
            model=model,
            credential_ref=credential_ref,
            mcp_url=fixture_server.url,
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
        await fixture_server.close()
        await store.close()


async def _seed_live_product(
    store: PostgreSQLRegistryStore,
    *,
    base_url: str,
    model: str,
    credential_ref: str,
    mcp_url: str,
) -> None:
    await publish_resource(
        store,
        tenant_id="dev",
        kind=ResourceKind.MODEL_PROVIDER,
        resource_id="live-provider",
        version="1",
        spec={
            "protocol": "openai-compatible",
            "base_url": base_url,
            "credential_ref": "secret://dev/live-provider",
            "default_model": model,
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
            "url": mcp_url,
            "timeout_ms": 10_000,
            "allowed_tools": ["lookup"],
        },
    )
    # TASK-005：租户 allow + enabled 策略行（真发现取 hash）。
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from sqlalchemy.ext.asyncio import create_async_engine

    from fluxion.registry.schema import capability_mcp_tool_policies
    from fluxion.runtime.mcp import mcp_tool_schema_hash
    from tests.runtime_helpers import TEST_POSTGRES_DSN, seed_tenant_policy

    await seed_tenant_policy(store, tenant_id="dev", allowed_tools=[TOOL_ID])
    async with Client(
        streamable_http_client(mcp_url), read_timeout_seconds=10
    ) as mcp_client:
        discovered = await mcp_client.list_tools()
    live_hash = next(
        mcp_tool_schema_hash(tool.input_schema)
        for tool in discovered.tools
        if tool.name == "lookup"
    )
    engine = create_async_engine(TEST_POSTGRES_DSN)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                capability_mcp_tool_policies.insert().values(
                    tenant_id="dev",
                    mcp_id="live_lookup",
                    mcp_version=1,
                    tool_name="lookup",
                    schema_hash=live_hash,
                    operation="read",
                    side_effect="none",
                    risk_level="low",
                    idempotency_json={},
                    approval_policy_json={},
                    enabled=True,
                )
            )
    finally:
        await engine.dispose()
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
        ("bind-live-provider", ResourceKind.MODEL_PROVIDER, "live-provider", credential_ref),
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
