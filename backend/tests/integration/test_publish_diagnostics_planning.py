"""golden-path-closure TASK-024 / F-S-18：发布定位 + 依赖规划 API 契约（integration）。

- 发布问题定位：validate-publish 返回结构化诊断（校验项+定位），fail-closed 不静默。
- Capability Dependency Planning：`GET /studio/agents/{id}/dependencies` 依赖图
  （kind/id/version/status），not_resolved 诚实呈现。
- Version Diff 为前端组件能力（spec 双版本客户端对比），jsdom 覆盖
  （frontend/apps/console/src/components/__tests__/SpecDiffModal.test.tsx）。

真实边界：真实 SQLite RegistryStore + Console HTTP ASGI，不 mock Store。
"""

from __future__ import annotations

import pytest

from tests.console_helpers import console_stack, tenant_headers

REQUIRE_HEADERS = tenant_headers(request_id="req-fs18")


@pytest.mark.asyncio
async def test_fs18_dependency_plan_reports_capability_status() -> None:
    """依赖规划：capability 可解析状态（published/not_resolved）+ workflow ref。"""
    async with console_stack() as stack:
        # 发布一个 skill 供 agent 引用
        skill = await stack.client.post(
            "/api/v1/resources/skill",
            json={
                "resource_id": "fs18-skill",
                "version": "1",
                "spec": {"name": "fs18-skill", "instructions": "s"},
            },
            headers=REQUIRE_HEADERS,
        )
        assert skill.status_code == 200, skill.text
        published = await stack.client.post(
            "/api/v1/resources/skill/fs18-skill/versions/1:publish",
            json={},
            headers=REQUIRE_HEADERS,
        )
        assert published.status_code == 200, published.text

        # agent 引用已发布 skill + 未发布（not_resolved）skill
        agent = await stack.client.post(
            "/api/v1/resources/agent_definition",
            json={
                "resource_id": "fs18-agent",
                "version": "1",
                "spec": {
                    "name": "fs18-agent",
                    "system_prompt": "s",
                    "owner": "e2e",
                    "model_policy": {
                        "primary_model_ref": {"id": "fs18-model", "version": "1"},
                        "fallback_model_refs": [],
                    },
                    "capabilities": [
                        {
                            "capability_ref": "fs18-skill",
                            "version_pin": "1",
                            "type": "skill",
                        },
                        {
                            "capability_ref": "fs18-ghost",
                            "version_pin": "1",
                            "type": "skill",
                        },
                    ],
                },
            },
            headers=REQUIRE_HEADERS,
        )
        assert agent.status_code == 200, agent.text

        plan = await stack.client.get(
            "/studio/agents/fs18-agent/dependencies",
            headers=REQUIRE_HEADERS,
        )
        assert plan.status_code == 200, plan.text
        body = plan.json()
        assert body["code"] == 0
        nodes = {node["capability_ref"]: node for node in body["data"]["nodes"]}
        # 已发布 capability → published 状态
        assert nodes["fs18-skill"]["status"] == "published"
        assert nodes["fs18-skill"]["kind"] == "skill"
        assert nodes["fs18-skill"]["version_pin"] == "1"
        # 未发布引用 → not_resolved 诚实呈现（不静默成功）
        assert nodes["fs18-ghost"]["status"] == "not_resolved"


@pytest.mark.asyncio
async def test_fs18_publish_issues_are_structured_diagnostics() -> None:
    """发布定位：publish 校验失败返回结构化诊断清单（fail-closed），关联 request_id。"""
    async with console_stack() as stack:
        # agent 引用不存在的模型 → 发布校验失败（结构化 message 含定位信息）
        created = await stack.client.post(
            "/api/v1/resources/agent_definition",
            json={
                "resource_id": "fs18-broken-agent",
                "version": "1",
                "spec": {
                    "name": "fs18-broken-agent",
                    "system_prompt": "s",
                    "owner": "e2e",
                    "model_policy": {
                        "primary_model_ref": {"id": "fs18-missing-model", "version": "1"},
                        "fallback_model_refs": [],
                    },
                    "capabilities": [],
                },
            },
            headers=REQUIRE_HEADERS,
        )
        assert created.status_code == 200, created.text
        validated = await stack.client.post(
            "/api/v1/resources/agent_definition/fs18-broken-agent/versions/1:validate-publish",
            json={},
            headers=REQUIRE_HEADERS,
        )
        assert validated.status_code == 200, validated.text
        body = validated.json()
        assert body["code"] == 0
        # 结构化问题清单：valid=false + 非空 issues（校验项+定位；§14.4 契约字段）
        assert body["data"]["valid"] is False
        diagnostics = body["data"]["issues"]
        assert isinstance(diagnostics, list) and len(diagnostics) >= 1
        assert any("fs18-missing-model" in item for item in diagnostics)
        # 规则 23：envelope 含 request_id（定位信息关联链路 ID）
        assert body["request_id"] == "req-fs18"
