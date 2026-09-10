"""TASK-004（S-03，E2E 级）：Console 测试调用与 Runtime 共用同一 Executor。

真实边界：同一 ToolExecutorRegistry + 同一 HTTPToolExecutor +
httpx.MockTransport 真 HTTP 栈（不 mock 执行器本身）。

- 测试路径：ConnectionTestService.test_tool_call（Console API 侧）。
- 运行路径：prepare_registry_tools 装配进 ToolRuntime 后 call。
- 断言：双路径结果一致（同一 executor 证据）。
"""

from __future__ import annotations

import httpx
import pytest

from fluxion.registry import RegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime.context import RequestContext, RuntimeContext
from fluxion.runtime.tool_executors import prepare_registry_tools
from fluxion.runtime.tools import ToolResultStatus, ToolRuntime
from fluxion.services.connection_test import ConnectionTestService
from fluxion.services.context_resolver import ContextResolver, ResolverSelector
from tests.runtime_helpers import (
    publish_resource,
    seed_model_definition,
    seed_tenant_policy,
)

_WEATHER_BODY = {"temp": 21, "city": "Shanghai"}


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/query"
        return httpx.Response(200, json=_WEATHER_BODY)

    return httpx.MockTransport(handler)


def _client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_transport(), timeout=5.0)


async def _seed_weather_tool(pg_store: RegistryStore) -> None:
    await publish_resource(
        pg_store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"max_rounds": 8, "default": True},
    )
    await seed_model_definition(pg_store, tenant_id="tenant-a", provider_id="dev.echo")
    await publish_resource(
        pg_store,
        tenant_id="tenant-a",
        kind=ResourceKind.TOOL,
        resource_id="query-weather",
        version="1",
        spec={
            "name": "query-weather",
            "description": "d",
            "tool_kind": "http_api",
            "url": "https://weather.example.com/query",
            "method": "POST",
            "capability_ref": "query-weather",
            "adapter_ref": "http",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object", "required": ["temp"]},
            "governance": {"risk_level": "low", "operation": "query"},
        },
    )
    await publish_resource(
        pg_store,
        tenant_id="tenant-a",
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id="assistant",
        version="1",
        spec={
            "name": "assistant",
            "system_prompt": "p",
            "owner": "builder",
            "model_policy": {
                "primary_model_ref": {"id": "model.dev.echo", "version": "1"}
            },
            "capabilities": [
                {"capability_ref": "query-weather", "version_pin": "1", "type": "tool"}
            ],
        },
    )
    await pg_store.add_capability_grant(
        tenant_id="tenant-a",
        platform_user_id="user-a",
        capability_ref="query-weather",
        granted_scope="invoke",
        version_pin="1",
        capability_kind="tool",
    )
    await seed_tenant_policy(
        pg_store, tenant_id="tenant-a", allowed_tools=["query-weather"]
    )


@pytest.mark.asyncio
async def test_S03_test_and_runtime_share_executor(pg_store: RegistryStore) -> None:
    """S-03：测试调用与正式执行走同一 Executor，结果一致。"""
    await _seed_weather_tool(pg_store)

    # 测试路径（Console API 侧）
    tester = ConnectionTestService(pg_store, client_factory=_client_factory)
    probed = await tester.test_tool_call(
        tenant_id="tenant-a", tool_id="query-weather"
    )
    assert probed.reachable is True

    # 运行路径（同一注册表装配进 ToolRuntime）
    resolved = await ContextResolver(pg_store).resolve(
        ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a"),
        session_id="s-a",
        request_id="req_dddddddddddddddddddddddddddddddd",
        trace_id="trace_dddddddddddddddddddddddddddddddd",
        execution_id="exec_dddddddddddddddddddddddddddddddd",
    )
    runtime = ToolRuntime()
    registered = await prepare_registry_tools(
        RuntimeContext(
            request=RequestContext(
                tenant_id="tenant-a", user_id="user-a", session_id="s-a"
            ),
            snapshot=resolved.snapshot,
        ),
        runtime,
        store=pg_store,
        credential_resolver=None,
        client_factory=_client_factory,
    )
    assert registered == {"query-weather"}
    result = await runtime.call(
        RuntimeContext(
            request=RequestContext(
                tenant_id="tenant-a", user_id="user-a", session_id="s-a"
            ),
            snapshot=resolved.snapshot,
        ),
        "query-weather",
        {"city": "Shanghai"},
    )
    assert result.status is ToolResultStatus.COMPLETED
    assert result.result == _WEATHER_BODY
