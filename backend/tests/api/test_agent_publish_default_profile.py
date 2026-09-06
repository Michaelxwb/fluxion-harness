"""Agent 发布 ADR-A010 默认链场景：无 ref 且无默认配置时 fail-closed；
创建并发布 default=true 的 RuntimeProfile 后放行。"""

from __future__ import annotations

import pytest

from tests.console_helpers import console_stack, tenant_headers
from tests.runtime_helpers import seed_model_definition, seed_tenant_policy


async def _seed_agent_without_ref(store) -> None:  # type: ignore[no-untyped-def]
    from fluxion.agents.definitions import AgentDefinition, AgentModelPolicy
    from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
    from fluxion.resources.contracts import ExactResourceVersion

    await seed_model_definition(store, tenant_id="tenant-a", provider_id="dev.echo")
    await seed_tenant_policy(store, tenant_id="tenant-a")
    # 留草稿（不发布）：复现“新建 Agent 后直接点发布”的真实路径。
    await store.put(
        ResourceDefinition(
            kind=ResourceKind.AGENT_DEFINITION,
            id="assistant",
            tenant_id="tenant-a",
            version="1",
            status=ResourceStatus.DRAFT,
            spec_json=AgentDefinition(
                name="assistant",
                system_prompt="你是助手。",
                owner="fixture",
                model_policy=AgentModelPolicy(
                    primary_model_ref=ExactResourceVersion(id="model.dev.echo", version="1")
                ),
            ).model_dump(mode="json"),
        )
    )


@pytest.mark.asyncio
async def test_publish_without_ref_nor_default_fails_closed() -> None:
    async with console_stack() as stack:
        await stack.service.initialize()
        await _seed_agent_without_ref(stack.store)
        response = await stack.client.post(
            "/studio/agents/assistant/versions/1:publish",
            headers=tenant_headers(request_id="req-no-default"),
        )
        assert response.status_code == 400
        assert "runtime_profile_ref" in response.text


@pytest.mark.asyncio
async def test_create_and_publish_tenant_default_unblocks_agent_publish() -> None:
    async with console_stack() as stack:
        await stack.service.initialize()
        await _seed_agent_without_ref(stack.store)
        created = await stack.client.post(
            "/studio/runtime-profiles",
            json={
                "resource_id": "tenant-default",
                "version": "1",
                "visibility": "tenant",
                "spec": {"request_timeout_ms": 30000, "max_retries": 1, "default": True},
            },
            headers=tenant_headers(request_id="req-create-profile"),
        )
        assert created.status_code == 200, created.text
        published = await stack.client.post(
            "/studio/runtime-profiles/tenant-default/versions/1:publish",
            headers=tenant_headers(request_id="req-publish-profile"),
        )
        assert published.status_code == 200, published.text
        agent_pub = await stack.client.post(
            "/studio/agents/assistant/versions/1:publish",
            headers=tenant_headers(request_id="req-agent-publish"),
        )
        assert agent_pub.status_code == 200, agent_pub.text
        assert agent_pub.json()["code"] == 0


@pytest.mark.asyncio
async def test_second_default_rejected() -> None:
    """同租户第二个 default=true 拒绝并存（发布治理）。"""
    async with console_stack() as stack:
        await stack.service.initialize()
        await _seed_agent_without_ref(stack.store)
        for resource_id in ("tenant-default", "tenant-default-2"):
            created = await stack.client.post(
                "/studio/runtime-profiles",
                json={
                    "resource_id": resource_id,
                    "version": "1",
                    "visibility": "tenant",
                    "spec": {"request_timeout_ms": 30000, "max_retries": 1, "default": True},
                },
                headers=tenant_headers(request_id=f"req-{resource_id}"),
            )
            assert created.status_code == 200, created.text
        first = await stack.client.post(
            "/studio/runtime-profiles/tenant-default/versions/1:publish",
            headers=tenant_headers(request_id="req-pub-1"),
        )
        assert first.status_code == 200, first.text
        second = await stack.client.post(
            "/studio/runtime-profiles/tenant-default-2/versions/1:publish",
            headers=tenant_headers(request_id="req-pub-2"),
        )
        assert second.status_code != 200
