from __future__ import annotations

import asyncio
import os
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import pytest
import uvicorn
from mcp.server import MCPServer
from tests.product_wire import (
    openai_final_response,
    openai_tool_call_response,
    openai_wire_server,
)
from tests.runtime_helpers import publish_resource
from uvicorn._types import ASGIApplication

from fluxion.plugins.model_provider import (
    ModelProviderRegistry,
    OpenAICompatibleHTTPModelProvider,
)
from fluxion.registry import RegistryStore
from fluxion.resources import ResourceBinding, ResourceKind, SubjectType
from fluxion.runtime.mcp import MCPHTTPClientPool, MCPHTTPPoolKey, RegistryMCPRuntime
from fluxion.runtime.secrets import CredentialResolver, LocalEncryptedSecretStore
from fluxion.services.runtime_app import RuntimeApplicationService
from fluxion.services.runtime_contracts import RunRuntimeRequest

MCP_TOOL_ID = "mcp__weather__lookup"


class UvicornMCPServer:
    def __init__(self, app: object) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._server = uvicorn.Server(
            uvicorn.Config(cast(ASGIApplication, app), log_level="warning", lifespan="on")
        )
        self._task: asyncio.Task[None] | None = None

    @property
    def url(self) -> str:
        port = cast(tuple[str, int], self._socket.getsockname())[1]
        return f"http://127.0.0.1:{port}/mcp"

    async def start(self) -> None:
        self._socket.listen()
        self._task = asyncio.create_task(self._server.serve(sockets=[self._socket]))
        for _attempt in range(100):
            if self._server.started:
                return
            await asyncio.sleep(0.01)
        raise TimeoutError("uvicorn MCP fixture did not start")

    async def close(self) -> None:
        self._server.should_exit = True
        if self._task is not None:
            await asyncio.wait_for(self._task, timeout=3)
        self._socket.close()


@asynccontextmanager
async def mcp_resource_spec(
    transport: str,
    tmp_path: Path,
) -> AsyncIterator[tuple[dict[str, object], list[str], Path | None]]:
    if transport == "stdio":
        call_log = tmp_path / "stdio-call.log"
        pid_file = tmp_path / "stdio.pid"
        fixture = Path(__file__).parents[1] / "fixtures" / "mcp_product_server.py"
        yield (
            {
                "name": "weather",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(fixture)],
                "env": {
                    "MCP_TEST_CALL_LOG": str(call_log),
                    "MCP_TEST_PID_FILE": str(pid_file),
                },
                "timeout_ms": 3_000,
                "allowed_tools": ["lookup"],
            },
            [],
            pid_file,
        )
        assert call_log.read_text(encoding="utf-8") == "fluxion"
        return

    calls: list[str] = []
    server = MCPServer("fluxion-product-http-fixture")

    @server.tool()
    def lookup(query: str) -> dict[str, str]:
        calls.append(query)
        return {"answer": f"MCP {query} result", "transport": "streamable_http"}

    @server.tool()
    async def slow_lookup(query: str) -> dict[str, str]:
        calls.append(f"slow:{query}")
        await asyncio.sleep(2)
        return {"answer": f"slow MCP {query} result"}

    fixture_server = UvicornMCPServer(
        server.streamable_http_app(json_response=True, stateless_http=True)
    )
    await fixture_server.start()
    try:
        yield (
            {
                "name": "weather",
                "transport": "streamable_http",
                "url": fixture_server.url,
                "timeout_ms": 3_000,
                "allowed_tools": ["lookup"],
            },
            calls,
            None,
        )
    finally:
        await fixture_server.close()


async def _seed_mcp_product(
    store: RegistryStore,
    *,
    mcp_spec: dict[str, object],
    bind_user: bool = True,
    max_rounds: int = 8,
    tool_id: str = MCP_TOOL_ID,
    credential_ref: str | None = None,
) -> None:
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.MCP,
        resource_id="weather",
        version="1",
        spec=mcp_spec,
    )
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"request_timeout_ms": 3_000, "max_retries": 1, "max_rounds": max_rounds},
    )
    # TASK-A104：persona/model/能力白名单迁至同名 AgentDefinition（resolver 同名
    # 回退解析）。TOOL capability 只承载 ref 准入，不做版本解析。
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id="assistant",
        version="1",
        spec={
            "name": "assistant",
            "system_prompt": "使用 MCP 查询。",
            "owner": "fixture",
            "model_ref": {"id": "wire", "version": "1"},
            "capabilities": [
                {"capability_ref": "weather", "version_pin": "1", "type": "mcp"},
                {"capability_ref": tool_id, "version_pin": "1", "type": "tool"},
            ],
        },
    )
    if bind_user:
        await store.put_binding(
            ResourceBinding(
                binding_id="binding-user-weather",
                tenant_id="tenant-a",
                subject_type=SubjectType.USER,
                subject_id="user-a",
                resource_type=ResourceKind.MCP,
                resource_id="weather",
                resource_version_selector="1",
                credential_ref=credential_ref,
            )
        )


def _model_registry(base_url: str) -> ModelProviderRegistry:
    registry = ModelProviderRegistry()
    registry.register(
        "wire",
        OpenAICompatibleHTTPModelProvider(
            provider_id="wire",
            api_base_url=base_url,
            model="fixture-model",
            timeout_seconds=3,
            max_retries=0,
        ),
    )
    return registry


@pytest.mark.parametrize("transport", ["stdio", "streamable_http"])
@pytest.mark.asyncio
async def test_S_P13_03_official_mcp_transports_complete_agent_loop(
    sqlite_store: RegistryStore,
    tmp_path: Path,
    transport: str,
) -> None:
    async with (
        mcp_resource_spec(transport, tmp_path) as (spec, http_calls, pid_file),
        openai_wire_server(
            [
                openai_tool_call_response(MCP_TOOL_ID),
                openai_final_response(f"{transport} MCP 最终答案"),
            ]
        ) as wire,
    ):
        await _seed_mcp_product(sqlite_store, mcp_spec=spec)
        runtime = RuntimeApplicationService(
            sqlite_store,
            model_providers=_model_registry(wire.base_url),
            mcp_runtime=RegistryMCPRuntime(sqlite_store),
        )

        result = await runtime.run(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id=f"session-{transport}",
                input_message="查询 fluxion",
            )
        )

        assert result.output == f"{transport} MCP 最终答案"
        assert result.tool_results[0]["tool_id"] == MCP_TOOL_ID
        assert len(wire.requests) == 2
        first_tools = cast(list[dict[str, object]], wire.requests[0]["tools"])
        assert cast(dict[str, object], first_tools[0]["function"])["name"] == MCP_TOOL_ID
        second_messages = cast(list[dict[str, object]], wire.requests[1]["messages"])
        assert "MCP fluxion result" in cast(str, second_messages[-1]["content"])
        trace = await runtime.trace_store.get(result.trace_id)
        assert trace is not None
        assert trace.snapshot.mcp_versions == {"weather": "1"}
        assert any(event.name == "mcp.tools_listed" for event in trace.events)
        assert any(event.name == "mcp.tool_called" for event in trace.events)
        if transport == "streamable_http":
            assert http_calls == ["fluxion"]

    if pid_file is not None:
        pid = int(pid_file.read_text(encoding="ascii"))
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


@pytest.mark.asyncio
async def test_S_P13_03_streamable_http_reuses_tenant_scoped_transport_pool(
    sqlite_store: RegistryStore,
    tmp_path: Path,
) -> None:
    pool = MCPHTTPClientPool(ttl_seconds=30, max_clients=4)
    async with (
        mcp_resource_spec("streamable_http", tmp_path) as (spec, http_calls, _pid_file),
        openai_wire_server(
            [openai_tool_call_response(MCP_TOOL_ID), openai_final_response("pooled")]
        ) as wire,
    ):
        await _seed_mcp_product(sqlite_store, mcp_spec=spec)
        mcp_runtime = RegistryMCPRuntime(sqlite_store, http_pool=pool)
        runtime = RuntimeApplicationService(
            sqlite_store,
            model_providers=_model_registry(wire.base_url),
            mcp_runtime=mcp_runtime,
        )

        result = await runtime.run(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id="session-pool",
                input_message="验证连接复用",
            )
        )

        assert result.output == "pooled"
        assert http_calls == ["fluxion"]
        assert pool.client_count == 1
        assert pool.hit_count >= 1
    await pool.close()


@pytest.mark.asyncio
async def test_S_P13_03_mcp_pool_invalidates_changed_version_and_ttl() -> None:
    pool = MCPHTTPClientPool(ttl_seconds=0.01, max_clients=4)
    first_key = MCPHTTPPoolKey("tenant-a", "user-a", "http://mcp", "v1", "1")
    second_key = MCPHTTPPoolKey("tenant-a", "user-a", "http://mcp", "v2", "2")
    first = await pool.get_client(
        first_key,
        headers={},
        timeout_ms=1_000,
        credential_ref="secret://tenant-a/mcp@1",
    )
    second = await pool.get_client(
        second_key,
        headers={},
        timeout_ms=1_000,
        credential_ref="secret://tenant-a/mcp@2",
    )
    assert first.is_closed
    assert pool.client_count == 1

    await asyncio.sleep(0.02)
    refreshed = await pool.get_client(
        second_key,
        headers={},
        timeout_ms=1_000,
        credential_ref="secret://tenant-a/mcp@2",
    )
    assert second.is_closed
    assert refreshed is not second
    await pool.close()


@pytest.mark.asyncio
async def test_E_P13_01_unbound_mcp_tool_call_fails_closed_before_server(
    sqlite_store: RegistryStore,
    tmp_path: Path,
) -> None:
    async with (
        mcp_resource_spec("streamable_http", tmp_path) as (spec, http_calls, _pid_file),
        openai_wire_server([openai_tool_call_response(MCP_TOOL_ID)]) as wire,
    ):
        await _seed_mcp_product(sqlite_store, mcp_spec=spec, bind_user=False)
        runtime = RuntimeApplicationService(
            sqlite_store,
            model_providers=_model_registry(wire.base_url),
            mcp_runtime=RegistryMCPRuntime(sqlite_store),
        )
        request = RunRuntimeRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-unbound",
            input_message="尝试越权查询",
        )

        with pytest.raises(RuntimeError) as error:
            await runtime.run(request)

        assert getattr(error.value, "code", None) == "tool_not_allowed"
        assert http_calls == []
        assert len(wire.requests) == 1
        assert "tools" not in wire.requests[0]
        trace = await runtime.trace_store.get(request.trace_id)
        assert trace is not None
        assert not any(event.name == "mcp.tool_called" for event in trace.events)


@pytest.mark.asyncio
async def test_E_P13_01_agent_loop_budget_stops_after_real_mcp_call(
    sqlite_store: RegistryStore,
    tmp_path: Path,
) -> None:
    async with (
        mcp_resource_spec("stdio", tmp_path) as (spec, _http_calls, pid_file),
        openai_wire_server([openai_tool_call_response(MCP_TOOL_ID)]) as wire,
    ):
        await _seed_mcp_product(sqlite_store, mcp_spec=spec, max_rounds=1)
        runtime = RuntimeApplicationService(
            sqlite_store,
            model_providers=_model_registry(wire.base_url),
            mcp_runtime=RegistryMCPRuntime(sqlite_store),
        )
        request = RunRuntimeRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-loop-budget",
            input_message="只允许一轮",
        )

        with pytest.raises(RuntimeError) as error:
            await runtime.run(request)

        assert getattr(error.value, "code", None) == "agent_loop_limit_exceeded"
        assert len(wire.requests) == 1
        trace = await runtime.trace_store.get(request.trace_id)
        assert trace is not None
        assert any(event.name == "mcp.tool_called" for event in trace.events)
        assert any(event.name == "agent_loop.limit_exceeded" for event in trace.events)

    assert pid_file is not None
    pid = int(pid_file.read_text(encoding="ascii"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_E_P13_01_revoked_credential_is_not_reused_by_mcp_transport(
    sqlite_store: RegistryStore,
    tmp_path: Path,
) -> None:
    secrets = LocalEncryptedSecretStore(master_key=b"m" * 32)
    credential_ref = await secrets.put("tenant-a", "weather", "fixture-token")
    async with (
        mcp_resource_spec("streamable_http", tmp_path) as (spec, http_calls, _pid_file),
        openai_wire_server(
            [
                openai_tool_call_response(MCP_TOOL_ID),
                openai_final_response("credential 首次调用成功"),
            ]
        ) as wire,
    ):
        await _seed_mcp_product(
            sqlite_store,
            mcp_spec=spec,
            credential_ref=credential_ref,
        )
        pool = MCPHTTPClientPool(ttl_seconds=30, max_clients=4)
        runtime = RuntimeApplicationService(
            sqlite_store,
            model_providers=_model_registry(wire.base_url),
            mcp_runtime=RegistryMCPRuntime(
                sqlite_store,
                credential_resolver=CredentialResolver(secrets),
                http_pool=pool,
            ),
        )
        first = await runtime.run(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id="session-credential-first",
                input_message="首次调用",
            )
        )
        await secrets.revoke(credential_ref)

        with pytest.raises(RuntimeError) as error:
            await runtime.run(
                RunRuntimeRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    runtime_profile_id="assistant",
                    session_id="session-credential-revoked",
                    input_message="撤销后调用",
                )
            )

        assert first.output == "credential 首次调用成功"
        assert getattr(error.value, "code", None) == "secret_revoked"
        assert http_calls == ["fluxion"]
        assert len(wire.requests) == 2
        assert pool.client_count == 0


@pytest.mark.asyncio
async def test_E_P13_01_mcp_timeout_closes_real_streamable_http_client(
    sqlite_store: RegistryStore,
    tmp_path: Path,
) -> None:
    slow_tool_id = "mcp__weather__slow_lookup"
    async with (
        mcp_resource_spec("streamable_http", tmp_path) as (spec, http_calls, _pid_file),
        openai_wire_server([openai_tool_call_response(slow_tool_id)]) as wire,
    ):
        spec["allowed_tools"] = ["slow_lookup"]
        spec["timeout_ms"] = 700
        await _seed_mcp_product(sqlite_store, mcp_spec=spec, tool_id=slow_tool_id)
        runtime = RuntimeApplicationService(
            sqlite_store,
            model_providers=_model_registry(wire.base_url),
            mcp_runtime=RegistryMCPRuntime(sqlite_store),
        )
        request = RunRuntimeRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-timeout",
            input_message="触发 MCP timeout",
        )

        with pytest.raises(RuntimeError) as error:
            await runtime.run(request)

        assert getattr(error.value, "code", None) == "mcp_timeout"
        assert http_calls == ["slow:fluxion"]
        trace = await runtime.trace_store.get(request.trace_id)
        assert trace is not None
        assert trace.error is not None
        assert not any(event.name == "mcp.tool_called" for event in trace.events)
