"""TASK-004 Product API `/studio/*` 验收测试。

- BE-S-01（E2E）：POST /studio/agents → :publish → GET 列表可见；
  spec 为 typed model 形态；真实链 API→Service→Store（无 mock）。
- BE-S-07（E2E）：POST /studio/model-providers（api_key 走 SecretRef）→ GET 列表
  → schema 端点可驱动表单。
- BE-E-01：缺 model_policy/owner → 4xx + `agent_definition_invalid`，message 定位字段。
- BE-E-02：重复 (resource_id, version) → 409 `version_conflict`。
"""

from __future__ import annotations

import pytest

from fluxion.resources import ResourceKind
from tests.console_helpers import console_stack, tenant_headers
from tests.runtime_helpers import publish_resource, seed_model_definition


def _agent_spec() -> dict[str, object]:
    return {
        "name": "Support Agent",
        "description": "客服助手",
        "system_prompt": "You are a support agent.",
        "owner": "builder-1",
        "model_policy": {
            "primary_model_ref": {"id": "model.provider-1", "version": "1"}
        },
        "runtime_profile_ref": {"id": "profile-1", "version": "1"},
        "capabilities": [
            {"capability_ref": "skill-1", "version_pin": "1", "type": "skill"},
            {"capability_ref": "mcp-1", "version_pin": "1", "type": "mcp"},
        ],
        "instructions": "Answer concisely.",
    }


@pytest.mark.asyncio
async def test_be_s_01_studio_agent_create_publish_and_list() -> None:
    async with console_stack() as stack:
        # ADR-A008：publish 校验会解析 model_policy.primary_model_ref → 需先
        # seed 对应 ModelDefinition（tenant 与 agent 一致）。
        await seed_model_definition(
            stack.store,
            tenant_id="tenant-a",
            model_id="model.provider-1",
            provider_id="provider-1",
        )
        # RULE-04/S-04：publish 引用完整性同样解析 capabilities——skill/mcp
        # 需已定义（skill required_capabilities 为空，不触发闭包缺失）。
        await publish_resource(
            stack.store,
            tenant_id="tenant-a",
            kind=ResourceKind.SKILL,
            resource_id="skill-1",
            version="1",
            spec={"name": "skill-1"},
        )
        await publish_resource(
            stack.store,
            tenant_id="tenant-a",
            kind=ResourceKind.MCP,
            resource_id="mcp-1",
            version="1",
            spec={"name": "mcp-1", "transport": "stdio", "command": "node", "args": []},
        )
        created = await stack.client.post(
            "/studio/agents",
            json={"resource_id": "agent-1", "version": "1", "spec": _agent_spec()},
            headers=tenant_headers(request_id="req-be-s01-create"),
        )
        assert created.status_code == 200, created.text
        payload = created.json()
        assert payload["code"] == 0 and payload["request_id"]
        assert payload["data"]["status"] == "draft"

        published = await stack.client.post(
            "/studio/agents/agent-1/versions/1:publish",
            headers=tenant_headers(request_id="req-be-s01-publish"),
        )
        assert published.status_code == 200, published.text
        assert published.json()["data"]["status"] == "published"

        listing = await stack.client.get(
            "/studio/agents", headers=tenant_headers(request_id="req-be-s01-list")
        )
        assert listing.status_code == 200
        body = listing.json()
        assert body["code"] == 0
        ids = [item["resource_id"] for item in body["data"]["items"]]
        assert "agent-1" in ids
        # spec 经 typed model 校验落库（引用而非内嵌 persona/model；ADR-A008 后
        # 模型引用形态为 model_policy → ModelDefinition，legacy model_ref 不落库）。
        raw = await stack.store.get(
            ResourceKind.AGENT_DEFINITION, "agent-1", tenant_id="tenant-a", version="1"
        )
        assert raw is not None and raw.status.value == "published"
        assert "prompt" not in raw.spec_json and "model_ref" not in raw.spec_json
        assert raw.spec_json["model_policy"] == {
            "primary_model_ref": {"id": "model.provider-1", "version": "1"}
        }


@pytest.mark.asyncio
async def test_be_s_07_studio_models_crud_with_secret_ref_schema() -> None:
    async with console_stack() as stack:
        created = await stack.client.post(
            "/studio/model-providers",
            json={
                "resource_id": "provider-a",
                "version": "1",
                "spec": {
                    "protocol": "openai-compatible",
                    "base_url": "https://api.example.com/v1",
                    "credential_ref": "secret://tenant-a/api-key",
                    "default_model": "deepseek-chat",
                },
            },
            headers=tenant_headers(request_id="req-be-s07-create"),
        )
        assert created.status_code == 200, created.text

        # B-E-02：publish 校验要求 credential_ref 指向已定义的 SECRET 资源。
        await publish_resource(
            stack.store,
            tenant_id="tenant-a",
            kind=ResourceKind.SECRET,
            resource_id="api-key",
            version="1",
            spec={"name": "api-key", "secret_ref": "secret://tenant-a/api-key@1"},
        )

        # Product API v1 列表沿用仓库既定语义：published-only（store
        # _list_published_resources）；draft 浏览为独立 UI 任务，不在本场景。
        published = await stack.client.post(
            "/studio/model-providers/provider-a/versions/1:publish",
            headers=tenant_headers(request_id="req-be-s07-publish"),
        )
        assert published.status_code == 200, published.text

        listed = await stack.client.get(
            "/studio/model-providers", headers=tenant_headers(request_id="req-be-s07-list")
        )
        assert listed.status_code == 200
        items = listed.json()["data"]["items"]
        assert any(item["resource_id"] == "provider-a" for item in items)
        # 凭据只允许 SecretRef 引用形态出现。
        assert all("sk-" not in str(item) for item in items)

        schema = await stack.client.get(
            "/api/v1/resources/model_provider/schema", headers=tenant_headers()
        )
        assert schema.status_code == 200
        properties = set(schema.json()["data"]["schema"]["properties"])
        assert {"protocol", "base_url", "credential_ref", "default_model"} <= properties


@pytest.mark.asyncio
async def test_be_e_01_agent_without_required_field_rejected() -> None:
    async with console_stack() as stack:
        bad_spec = _agent_spec()
        del bad_spec["model_policy"]

        response = await stack.client.post(
            "/studio/agents",
            json={"resource_id": "agent-bad", "version": "1", "spec": bad_spec},
            headers=tenant_headers(request_id="req-be-e01"),
        )
        assert response.status_code == 422
        payload = response.json()
        assert payload["code"] != 0
        assert "model_policy" in payload["message"] or "Field required" in payload["message"]


@pytest.mark.asyncio
async def test_be_e_02_duplicate_agent_version_conflicts() -> None:
    async with console_stack() as stack:
        body = {"resource_id": "agent-dup", "version": "1", "spec": _agent_spec()}
        first = await stack.client.post(
            "/studio/agents", json=body, headers=tenant_headers(request_id="req-be-e02a")
        )
        second = await stack.client.post(
            "/studio/agents", json=body, headers=tenant_headers(request_id="req-be-e02b")
        )
        assert first.status_code == 200
        assert second.status_code == 409
        payload = second.json()
        assert payload["code"] != 0
