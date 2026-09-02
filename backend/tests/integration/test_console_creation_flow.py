"""console-creation-flow-fix TASK-001（CF-S-01）：创建流列表语义。

Console 列表 = 每资源「当前版本（任意状态）」一行：新建 draft Agent 立即可见，
发布后状态翻转。修复前 `GET /api/v1/resources` 只列 PUBLISHED（detail 半边已由
console_resources.get 修复），新建 draft 在 UI 不可达；本测试在真实 HTTP 边界
（ASGITransport + SQLiteRegistryStore）钉住列表半边。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient, Response

from fluxion.resources import ResourceKind
from tests.console_helpers import (
    console_stack,
    create_resource,
    publish_resource,
    tenant_headers,
)


def _secret_spec() -> dict[str, object]:
    return {"name": "openai", "secret_ref": "secret://tenant-a/openai", "purpose": "llm"}


def _provider_spec() -> dict[str, object]:
    return {
        "protocol": "openai-compatible",
        "base_url": "https://api.example.invalid/v1",
        "credential_ref": "secret://tenant-a/openai",
        "default_model": "test-model",
        "request_timeout_ms": 60_000,
        "max_retries": 1,
    }


def _model_spec() -> dict[str, object]:
    return {
        "name": "test-model",
        "provider_ref": {"id": "prov-a", "version": "1"},
    }


def _agent_spec() -> dict[str, object]:
    return {
        "name": "客户服务助手",
        "description": "CF-S-01 创建流",
        "system_prompt": "保持严谨",
        "owner": "admin-a",
        "model_policy": {
            "primary_model_ref": {"id": "model.prov-a", "version": "1"},
        },
    }


async def _seed_model_chain(client: AsyncClient) -> None:
    """ADR-A008 三层链前置：SECRET + MODEL_PROVIDER + MODEL_DEFINITION 全部发布。"""
    for kind, resource_id, spec in (
        (ResourceKind.SECRET, "openai", _secret_spec()),
        (ResourceKind.MODEL_PROVIDER, "prov-a", _provider_spec()),
        (ResourceKind.MODEL_DEFINITION, "model.prov-a", _model_spec()),
    ):
        await create_resource(
            client, kind=kind, resource_id=resource_id, version="1", spec=spec
        )
        await publish_resource(
            client,
            kind=kind,
            resource_id=resource_id,
            version="1",
            expected_base_version=None,
        )


async def _list_agents(client: AsyncClient) -> list[dict[str, object]]:
    listed = await client.get(
        "/api/v1/resources",
        params={"resource_type": "agent_definition", "page": 1, "page_size": 100},
        headers=tenant_headers(),
    )
    assert listed.status_code == 200
    return list(listed.json()["data"]["items"])


def _row(items: list[dict[str, object]], resource_id: str) -> dict[str, object] | None:
    return next(
        (item for item in items if item["resource_id"] == resource_id), None
    )


@pytest.mark.asyncio
async def test_CF_S01_draft_agent_visible_in_list_until_published() -> None:
    async with console_stack() as stack:
        await _seed_model_chain(stack.client)

        created = await create_resource(
            stack.client,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="agent-cs-1",
            version="1",
            spec=_agent_spec(),
        )
        draft_items = await _list_agents(stack.client)

        # 编辑入口语义：`:working-draft` 复用既有 draft v1（无 published base 不 fork 新版本）
        working: Response = await stack.client.post(
            "/api/v1/resources/agent_definition/agent-cs-1:working-draft",
            headers=tenant_headers(),
        )

        published = await publish_resource(
            stack.client,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="agent-cs-1",
            version="1",
            expected_base_version=None,
        )
        published_items = await _list_agents(stack.client)

    assert created.status_code == 200

    draft_row = _row(draft_items, "agent-cs-1")
    assert draft_row is not None, "新建 draft Agent 必须出现在 Console 列表（CF-S-01 修复点）"
    assert draft_row["status"] == "draft"
    assert draft_row["version"] == "1"

    assert working.status_code == 200
    assert working.json()["data"]["version"] == "1"  # 复用 draft v1，不重复 fork
    assert working.json()["data"]["status"] == "draft"

    assert published.status_code == 200
    published_row = _row(published_items, "agent-cs-1")
    assert published_row is not None
    assert published_row["status"] == "published"  # 发布后状态翻转
