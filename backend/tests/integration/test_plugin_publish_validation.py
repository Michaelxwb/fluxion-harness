from __future__ import annotations

import pytest

from fluxion.resources import ResourceKind
from tests.console_helpers import (
    ConsoleTestStack,
    console_stack,
    create_resource,
    publish_resource,
    tenant_headers,
)


@pytest.mark.asyncio
async def test_S_P13_06_console_publishes_model_provider_spec() -> None:
    # ADR-A008（TASK-002）：消灭双事实源。模型供应商经 `kind=model_provider`
    # （ProviderDefinition 形状）发布，不再经 `PLUGIN(plugin_type=model_provider)`。
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.SECRET,
            resource_id="browser-key",
            spec={"name": "browser-key", "secret_ref": "secret://tenant-a/browser-key"},
        )
        created = await create_resource(
            stack.client,
            kind=ResourceKind.MODEL_PROVIDER,
            resource_id="browser-provider",
            spec={
                "protocol": "openai-compatible",
                "base_url": "http://127.0.0.1:9878/v1",
                "credential_ref": "secret://tenant-a/browser-key",
                "default_model": "browser-model",
                "request_timeout_ms": 3000,
                "max_retries": 0,
            },
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.MODEL_PROVIDER,
            resource_id="browser-provider",
        )

    assert created.status_code == 200
    assert published.status_code == 200
    assert published.json()["data"]["status"] == "published"


@pytest.mark.asyncio
async def test_E_P13_06_console_publishes_plugin_definition_spec() -> None:
    # ADR-A009：PLUGIN 是 Extension（PluginDefinition 形状），不再是模型供应商载体。
    async with console_stack() as stack:
        created = await create_resource(
            stack.client,
            kind=ResourceKind.PLUGIN,
            resource_id="package-plugin",
            spec={"name": "package-plugin", "package": "x", "trust_level": "trusted"},
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.PLUGIN,
            resource_id="package-plugin",
        )

    assert created.status_code == 200
    assert published.status_code == 200
    assert published.json()["data"]["status"] == "published"


@pytest.mark.asyncio
async def test_E_P13_06_console_rejects_model_provider_spec_under_plugin_kind() -> None:
    # ADR-A008：`PLUGIN(plugin_type=model_provider)` 退出模型链——ProviderDefinition
    # 形状的 spec 不再被 PLUGIN kind 接受（extra=forbid 拒绝 plugin_type 等字段）。
    async with console_stack() as stack:
        created = await create_resource(
            stack.client,
            kind=ResourceKind.PLUGIN,
            resource_id="provider-as-plugin",
            spec={
                "plugin_type": "model_provider",
                "protocol": "openai_compatible",
                "base_url": "http://127.0.0.1:9878/v1",
                "model": "browser-model",
            },
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.PLUGIN,
            resource_id="provider-as-plugin",
        )

    assert created.status_code == 200
    assert published.status_code == 400
    payload = published.json()
    assert payload["code"] != 0
    assert "Extra inputs are not permitted" in payload["message"]


async def _seed_model_definition(stack: ConsoleTestStack) -> None:
    await create_resource(
        stack.client,
        kind=ResourceKind.SECRET,
        resource_id="provider-key",
        spec={"name": "provider-key", "secret_ref": "secret://tenant-a/provider-key"},
    )
    await create_resource(
        stack.client,
        kind=ResourceKind.MODEL_PROVIDER,
        resource_id="test",
        spec={
            "protocol": "openai-compatible",
            "base_url": "https://models.example.com/v1",
            "credential_ref": "secret://tenant-a/provider-key",
            "default_model": "default",
        },
    )
    await publish_resource(
        stack.client, kind=ResourceKind.MODEL_PROVIDER, resource_id="test"
    )
    await create_resource(
        stack.client,
        kind=ResourceKind.MODEL_DEFINITION,
        resource_id="model.ok",
        spec={"name": "default", "provider_ref": {"id": "test", "version": "1"}},
    )
    await publish_resource(
        stack.client, kind=ResourceKind.MODEL_DEFINITION, resource_id="model.ok"
    )


@pytest.mark.asyncio
async def test_B_E02_publish_blocks_unavailable_credential() -> None:
    """B-E-02：Provider 引用不存在的 Credential → 发布被阻断（fail-closed），
    返回可操作错误，不产生 published 版本（TASK-009 返工：发布链接入完整校验）。"""
    async with console_stack() as stack:
        created = await create_resource(
            stack.client,
            kind=ResourceKind.MODEL_PROVIDER,
            resource_id="prov-bad-cred",
            spec={
                "protocol": "openai-compatible",
                "base_url": "http://127.0.0.1:9878/v1",
                "default_model": "browser-model",
                "credential_ref": "secret://tenant-a/missing-cred@1",
            },
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.MODEL_PROVIDER,
            resource_id="prov-bad-cred",
        )
        # 阻断后仍是 draft，未产生 published 版本
        row = await stack.store.get(
            ResourceKind.MODEL_PROVIDER, "prov-bad-cred", tenant_id="tenant-a", version="1"
        )

    assert created.status_code == 200
    assert published.status_code == 400
    payload = published.json()
    assert payload["code"] != 0
    assert "missing-cred" in payload["message"]
    assert row is not None and row.status.value == "draft"


def _agent_spec(
    *, capabilities: list[dict[str, object]], model_ref_id: str = "model.ok"
) -> dict[str, object]:
    return {
        "name": "agent-under-test",
        "system_prompt": "x",
        "owner": "admin",
        "model_policy": {
            "primary_model_ref": {"id": model_ref_id, "version": "1"},
            "fallback_model_refs": [],
        },
        "capabilities": capabilities,
    }


@pytest.mark.asyncio
async def test_B_S04_publish_blocks_agent_with_missing_skill_reference() -> None:
    """B-S-04：Agent 引用不存在的 Skill → 发布返回可操作问题清单，
    不产生 published 版本。"""
    async with console_stack() as stack:
        await _seed_model_definition(stack)
        created = await create_resource(
            stack.client,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="dangling-skill-agent",
            spec=_agent_spec(
                capabilities=[
                    {"capability_ref": "missing-skill", "version_pin": "1", "type": "skill"}
                ]
            ),
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="dangling-skill-agent",
        )
        row = await stack.store.get(
            ResourceKind.AGENT_DEFINITION,
            "dangling-skill-agent",
            tenant_id="tenant-a",
            version="1",
        )

    assert created.status_code == 200
    assert published.status_code == 400
    payload = published.json()
    assert payload["code"] != 0
    assert "missing-skill" in payload["message"]
    assert row is not None and row.status.value == "draft"


@pytest.mark.asyncio
async def test_B_S04_publish_blocks_agent_with_missing_model_definition() -> None:
    """B-S-04：model_policy.primary_model_ref 指向不存在的 ModelDefinition →
    发布阻断（ADR-A008 引用完整性）。"""
    async with console_stack() as stack:
        created = await create_resource(
            stack.client,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="dangling-model-agent",
            spec=_agent_spec(capabilities=[], model_ref_id="missing-model"),
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="dangling-model-agent",
        )

    assert created.status_code == 200
    assert published.status_code == 400
    payload = published.json()
    assert payload["code"] != 0
    assert "missing-model" in payload["message"]


@pytest.mark.asyncio
async def test_B_S04_publish_blocks_agent_with_draft_model_definition() -> None:
    """exact 引用命中 Draft 仍不可发布，避免上线后 Runtime fail-closed。"""
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.MODEL_DEFINITION,
            resource_id="draft-model",
            spec={"name": "draft", "provider_ref": {"id": "ghost", "version": "1"}},
        )
        await create_resource(
            stack.client,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="draft-model-agent",
            spec=_agent_spec(capabilities=[], model_ref_id="draft-model"),
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="draft-model-agent",
        )

    assert published.status_code == 400
    assert "draft-model@1" in published.json()["message"]
    assert "未发布" in published.json()["message"]


@pytest.mark.asyncio
async def test_B_S04_publish_blocks_missing_fallback_model_definition() -> None:
    """fallback_model_refs 与 primary 使用相同的完整性规则。"""
    async with console_stack() as stack:
        await _seed_model_definition(stack)
        spec = _agent_spec(capabilities=[])
        model_policy = spec["model_policy"]
        assert isinstance(model_policy, dict)
        model_policy["fallback_model_refs"] = [{"id": "ghost-fallback", "version": "7"}]
        await create_resource(
            stack.client,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="missing-fallback-agent",
            spec=spec,
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="missing-fallback-agent",
        )

    assert published.status_code == 400
    assert "ghost-fallback@7" in published.json()["message"]


@pytest.mark.asyncio
async def test_B_S04_publish_blocks_model_definition_with_missing_provider() -> None:
    """ModelDefinition.provider_ref 必须指向已发布 ProviderDefinition。"""
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.MODEL_DEFINITION,
            resource_id="orphan-model",
            spec={
                "name": "orphan",
                "provider_ref": {"id": "ghost-provider", "version": "3"},
            },
        )
        validated = await stack.client.post(
            "/api/v1/resources/model_definition/orphan-model/versions/1:validate-publish",
            headers=tenant_headers(),
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.MODEL_DEFINITION,
            resource_id="orphan-model",
        )

    assert validated.status_code == 200
    assert validated.json()["data"]["valid"] is False
    assert "ghost-provider@3" in " ".join(validated.json()["data"]["issues"])
    assert published.status_code == 400


@pytest.mark.asyncio
async def test_E_E05_publish_blocks_agent_with_uncovered_skill_requirements() -> None:
    """E-05：Skill required_capabilities 未被 Agent 声明的 Tool 覆盖 →
    发布期即拦截（PlanningService 接入发布链），不是运行时才失败。"""
    async with console_stack() as stack:
        await _seed_model_definition(stack)
        await create_resource(
            stack.client,
            kind=ResourceKind.SKILL,
            resource_id="search-skill",
            spec={
                "name": "search",
                "instructions": "搜索",
                "required_capabilities": ["tool.search"],
            },
        )
        await publish_resource(
            stack.client, kind=ResourceKind.SKILL, resource_id="search-skill"
        )
        created = await create_resource(
            stack.client,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="uncovered-agent",
            spec=_agent_spec(
                capabilities=[
                    {"capability_ref": "search-skill", "version_pin": "1", "type": "skill"}
                ]
            ),
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="uncovered-agent",
        )

    assert created.status_code == 200
    assert published.status_code == 400
    payload = published.json()
    assert payload["code"] != 0
    assert "tool.search" in payload["message"]


@pytest.mark.asyncio
async def test_B_S04_publish_accepts_agent_with_resolvable_references() -> None:
    """正向控制：引用齐备（skill 已发布 + required capabilities 已声明 +
    ModelDefinition 存在）→ 发布成功，不误伤合法配置。"""
    async with console_stack() as stack:
        await _seed_model_definition(stack)
        await create_resource(
            stack.client,
            kind=ResourceKind.SKILL,
            resource_id="search-skill",
            spec={
                "name": "search",
                "instructions": "搜索",
                "required_capabilities": ["tool.search"],
            },
        )
        await publish_resource(
            stack.client, kind=ResourceKind.SKILL, resource_id="search-skill"
        )
        created = await create_resource(
            stack.client,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="healthy-agent",
            spec=_agent_spec(
                capabilities=[
                    {"capability_ref": "search-skill", "version_pin": "1", "type": "skill"},
                    {"capability_ref": "tool.search", "version_pin": "1", "type": "tool"},
                ]
            ),
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="healthy-agent",
        )

    assert created.status_code == 200
    assert published.status_code == 200
    assert published.json()["data"]["status"] == "published"
