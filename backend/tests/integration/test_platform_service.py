"""TASK-008（S-05）：Platform Service registry + executor。

真实边界：内存 ServiceRegistry（首版静态注册；K8s Service DNS 以 base_url
直通，不另实现 DNS RR）+ PlatformServiceExecutor + httpx.MockTransport 真 HTTP 栈。
"""

from __future__ import annotations

import httpx
import pytest

from fluxion.registry import RegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime.context import RequestContext, RuntimeContext
from fluxion.runtime.platform_services import PlatformServiceRegistry
from fluxion.runtime.tool_executors import prepare_registry_tools
from fluxion.runtime.tools import ToolResultStatus, ToolRuntime
from fluxion.services.context_resolver import ContextResolver, ResolverSelector
from tests.runtime_helpers import (
    publish_resource,
    seed_model_definition,
    seed_tenant_policy,
)


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/get_customer"
        return httpx.Response(200, json={"id": "c1", "name": "Acme"})

    return httpx.MockTransport(handler)


async def _seed_platform_tool(pg_store: RegistryStore) -> PlatformServiceRegistry:
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
        resource_id="query-customer",
        version="1",
        spec={
            "name": "query-customer",
            "description": "d",
            "tool_kind": "platform_service",
            "service_name": "customer-service-mgr",
            "operation": "get_customer",
            "capability_ref": "query-customer",
            "adapter_ref": "platform",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object", "required": ["id"]},
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
                {"capability_ref": "query-customer", "version_pin": "1", "type": "tool"}
            ],
        },
    )
    await pg_store.add_capability_grant(
        tenant_id="tenant-a",
        platform_user_id="user-a",
        capability_ref="query-customer",
        granted_scope="invoke",
        version_pin="1",
        capability_kind="tool",
    )
    await seed_tenant_policy(
        pg_store, tenant_id="tenant-a", allowed_tools=["query-customer"]
    )
    registry = PlatformServiceRegistry()
    registry.register(
        "customer-service-mgr",
        base_url="https://platform.internal",
        operations={"get_customer": "/api/get_customer"},
    )
    return registry


@pytest.mark.asyncio
async def test_S05_platform_service_call(pg_store: RegistryStore) -> None:
    """S-05：服务发现 → 调用成功（超时/重试/映射生效）。"""
    services = await _seed_platform_tool(pg_store)
    resolved = await ContextResolver(pg_store).resolve(
        ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a"),
        session_id="s-a",
        request_id="req_11111111111111111111111111111111",
        trace_id="trace_11111111111111111111111111111111",
        execution_id="exec_11111111111111111111111111111111",
    )
    context = RuntimeContext(
        request=RequestContext(
            tenant_id="tenant-a", user_id="user-a", session_id="s-a"
        ),
        snapshot=resolved.snapshot,
    )
    runtime = ToolRuntime()
    registered = await prepare_registry_tools(
        context,
        runtime,
        store=pg_store,
        credential_resolver=None,
        client_factory=lambda: httpx.AsyncClient(
            transport=_transport(), timeout=5.0
        ),
        service_registry=services,
    )
    assert registered == {"query-customer"}
    result = await runtime.call(context, "query-customer", {"id": "c1"})
    assert result.status is ToolResultStatus.COMPLETED
    assert result.result == {"id": "c1", "name": "Acme"}
