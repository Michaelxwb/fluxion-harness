from __future__ import annotations

import pytest
from tests.console_helpers import console_stack, tenant_headers

from fluxion.resources import ResourceKind

# ADR-012：schema endpoint 是前端表单的单一真相源。每个 kind 的断言锚定
# 契约里真正会被消费的必填字段（无默认值 → 进 required），避免测试与 schema
# 全量耦合（schema 加字段不应破坏此测试）。
REQUIRED_PROPERTIES: dict[ResourceKind, set[str]] = {
    ResourceKind.RUNTIME_PROFILE: {"request_timeout_ms", "max_retries"},
    # TASK-001：AgentDefinition 必填 = identity(name/system_prompt) + owner + model_ref
    ResourceKind.AGENT_DEFINITION: {"name", "system_prompt", "owner", "model_ref"},
    ResourceKind.MODEL: {"plugin_type", "protocol", "base_url", "model"},
    ResourceKind.TOOL: {"name", "capability_ref", "adapter_ref"},
    ResourceKind.SKILL: {"name"},
    ResourceKind.MCP: {"name", "transport"},
    ResourceKind.SECRET: {"name", "secret_ref"},
    ResourceKind.PLUGIN: {"plugin_type", "protocol", "base_url", "model"},
    ResourceKind.POLICY: {"name"},
    ResourceKind.WORKFLOW: {"name", "engine_ref", "steps"},
    ResourceKind.EVAL_SET: {"name", "runtime_profile_ref", "cases"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ResourceKind))
async def test_RS6_resource_schema_returns_model_json_schema(kind: ResourceKind) -> None:
    async with console_stack() as stack:
        response = await stack.client.get(
            f"/api/v1/resources/{kind.value}/schema",
            headers=tenant_headers(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["request_id"]
    schema = payload["data"]["schema"]
    assert schema["title"]
    properties = set(schema["properties"])
    # 必填字段在 required 中声明且有 property 定义；消费字段集不得漂移
    assert REQUIRED_PROPERTIES[kind] <= properties
    assert REQUIRED_PROPERTIES[kind] <= set(schema.get("required", []))


@pytest.mark.asyncio
async def test_RS6_schema_route_not_swallowed_by_resource_id_route() -> None:
    # 路由顺序回归：/{resource_type}/schema 必须先于 /{resource_type}/{resource_id}
    # 注册，否则 "schema" 被当作 resource_id 走详情路由返回 404。
    async with console_stack() as stack:
        response = await stack.client.get(
            "/api/v1/resources/policy/schema",
            headers=tenant_headers(),
        )

    assert response.status_code == 200
    assert response.json()["data"]["schema"]["title"]


@pytest.mark.asyncio
async def test_RS6_schema_carries_runtime_mechanics_constraints() -> None:
    # RuntimeProfile schema 只暴露运行机制；必填、边界与默认值均供表单直接消费。
    async with console_stack() as stack:
        response = await stack.client.get(
            "/api/v1/resources/runtime_profile/schema",
            headers=tenant_headers(),
        )

    schema = response.json()["data"]["schema"]
    properties = schema["properties"]
    assert set(properties) == {
        "request_timeout_ms",
        "max_retries",
        "max_rounds",
        "concurrency",
        "memory_budget_mb",
        "executor_config",
    }
    assert properties["request_timeout_ms"]["minimum"] == 100
    assert properties["request_timeout_ms"]["maximum"] == 120_000
    assert properties["max_retries"]["maximum"] == 5
    assert properties["concurrency"]["default"] == 1


@pytest.mark.asyncio
async def test_RS6_unknown_kind_rejected() -> None:
    async with console_stack() as stack:
        response = await stack.client.get(
            "/api/v1/resources/not_a_kind/schema",
            headers=tenant_headers(),
        )

    assert response.status_code == 400
    assert response.json()["code"] != 0
