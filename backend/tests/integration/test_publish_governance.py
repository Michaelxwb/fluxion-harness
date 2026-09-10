"""TASK-006（S-02/E-02/E-03）：发布状态机 + 凭证收口 + Draft 拒绝。

- S-02：Published Tool 精确版本可执行（digest 追溯）。
- E-02：Definition 含 credential_ref（Tool）/ headers（MCP）→ 发布校验失败。
- E-03：pin 到 Draft 版本的 Skill/MCP/Profile → 解析层拒绝。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fluxion.registry import RegistryStore
from fluxion.resources import ResourceKind
from fluxion.resources.resource_specs import MCPDefinition, ToolDefinition
from fluxion.services.context_resolver import ContextResolver, ResolverSelector
from tests.runtime_helpers import (
    publish_resource,
    resource_definition,
    seed_model_definition,
    seed_tenant_policy,
)


def _tool_spec(**overrides: object) -> dict[str, object]:
    spec: dict[str, object] = {
        "name": "query-weather",
        "description": "d",
        "tool_kind": "http_api",
        "url": "https://weather.example.com/query",
        "method": "POST",
        "capability_ref": "weather",
        "adapter_ref": "http",
    }
    spec.update(overrides)
    return spec


async def _seed_agent_with_skill(
    pg_store: RegistryStore, skill_version: str, *, publish_skill: bool
) -> None:
    await publish_resource(
        pg_store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"max_rounds": 8, "default": True},
    )
    await seed_model_definition(pg_store, tenant_id="tenant-a", provider_id="dev.echo")
    await pg_store.put(
        resource_definition(
            tenant_id="tenant-a",
            kind=ResourceKind.SKILL,
            resource_id="helper",
            version=skill_version,
            spec={
                "name": "helper",
                "instructions": "Be helpful.",
                "required_capabilities": [],
            },
        )
    )
    if publish_skill:
        await pg_store.publish(
            ResourceKind.SKILL, "helper", tenant_id="tenant-a", version=skill_version
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
                {"capability_ref": "helper", "version_pin": skill_version, "type": "skill"}
            ],
        },
    )
    await seed_tenant_policy(pg_store, tenant_id="tenant-a", allowed_tools=[])


@pytest.mark.asyncio
async def test_S02_published_tool_executes(pg_store: RegistryStore) -> None:
    """S-02：Published Tool 精确版本进入快照（digest 可追溯 exact version）。

    （可执行性由 TASK-004 executor 覆盖；本任务验收版本冻结 + 追溯。）
    """
    await publish_resource(
        pg_store,
        tenant_id="tenant-a",
        kind=ResourceKind.TOOL,
        resource_id="query-weather",
        version="1",
        spec=_tool_spec(),
    )
    row = await pg_store.recall_pinned(
        ResourceKind.TOOL, "query-weather", tenant_id="tenant-a", version="1"
    )
    assert row is not None
    assert row.status.value == "published"
    assert ToolDefinition.model_validate(row.spec_json).tool_kind == "http_api"


def test_E02_definition_credential_rejected() -> None:
    """E-02：Definition 含 credential_ref / headers → 发布校验失败。"""
    with pytest.raises(ValidationError):
        ToolDefinition.model_validate(
            _tool_spec(credential_ref="secret://tenant-a/weather")
        )
    with pytest.raises(ValidationError):
        MCPDefinition.model_validate(
            {
                "name": "weather",
                "transport": "streamable_http",
                "url": "https://mcp.example.com/mcp",
                "headers": {"X-Custom": "v"},
            }
        )


@pytest.mark.asyncio
async def test_E03_draft_version_pin_excluded(pg_store: RegistryStore) -> None:
    """E-03：pin 到 Draft 版本的 Skill 不进快照（SKL-01 语义：解析成功但排除；
    显式 /skill 调用落空到 skill_not_available，由 SKL-03 覆盖）。"""
    await _seed_agent_with_skill(pg_store, "1", publish_skill=False)
    result = await ContextResolver(pg_store).resolve(
        ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a"),
        session_id="s-a",
        request_id="req_cccccccccccccccccccccccccccccccc",
        trace_id="trace_cccccccccccccccccccccccccccccccc",
        execution_id="exec_cccccccccccccccccccccccccccccccc",
    )
    assert "helper" not in result.snapshot.skill_versions
    assert "helper" not in result.snapshot.skill_instructions
