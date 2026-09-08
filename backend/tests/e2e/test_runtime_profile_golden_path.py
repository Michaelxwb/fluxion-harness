"""TASK-002（golden-path-closure）B-S-01：RuntimeProfile 默认解析链 E2E。

Console 新建 Agent（`runtime_profile_ref=None`）→ 发布 → 执行 Resolve 成功。
真实边界：Console HTTP API → Registry → ContextResolver → Runtime（dev bundle
真实 AgentRuntime + dev.echo stub provider）；不 mock Store/Resolver/Runtime。

空租户断言：不手工 seed 任何同名 RuntimeProfile——profile 经部署级
platform-default（bootstrap 等价 seed）与 ADR-A010 默认链解析。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from fluxion.resources import ResourceKind
from fluxion.services.runtime_app import RuntimeApplicationService
from fluxion.services.runtime_contracts import RunRuntimeRequest
from tests.console_helpers import ConsoleTestStack, console_stack, tenant_headers
from tests.runtime_helpers import publish_resource, seed_model_definition

TENANT = "tenant-a"


def _agent_spec_without_profile_ref(model_id: str, model_version: str) -> dict[str, object]:
    return {
        "name": "空租户助手",
        "description": "B-S-01：无 runtime_profile_ref 的 Agent",
        "system_prompt": "保持严谨",
        "owner": "builder-1",
        "model_policy": {
            "primary_model_ref": {"id": model_id, "version": model_version}
        },
        # runtime_profile_ref 留空 → 解析层取租户默认（ADR-A010）
        "capabilities": [],
    }


@pytest.mark.asyncio
async def test_bs01_console_agent_without_profile_ref_resolves_via_default_chain() -> None:
    async with console_stack() as stack:
        # 部署级 bootstrap 等价：platform-default（ADR-A010 默认链第二级）与
        # 模型接入（dev.echo stub provider 对应的 ModelDefinition）。
        await publish_resource(
            stack.store,
            tenant_id=TENANT,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="platform-default",
            version="1",
            spec={"max_rounds": 8},
        )
        model = await seed_model_definition(
            stack.store, tenant_id=TENANT, provider_id="dev.echo", model_name="echo"
        )

        created = await _create_agent(stack.client, model.id, model.version)
        assert created.status_code == 200, created.text
        payload = created.json()
        assert payload["code"] == 0, payload
        assert payload["data"]["status"] == "draft"

        published = await _publish_agent(stack.client)
        assert published.status_code == 200, published.text
        assert published.json()["data"]["status"] == "published"

        # 空租户断言：全程无同名 RuntimeProfile（B-S-01 关键边界）
        same_name = await stack.store.get(
            ResourceKind.RUNTIME_PROFILE, "agent-bs01", tenant_id=TENANT
        )
        assert same_name is None

        # 真实执行链：dev bundle AgentRuntime + ContextResolver 十段管线
        runtime = RuntimeApplicationService.create_dev_bundle(stack.store)
        result = await runtime.run(
            RunRuntimeRequest(
                tenant_id=TENANT,
                user_id="user-bs01",
                runtime_profile_id="agent-bs01",  # mechanics 透传坐标；解析走默认链
                agent_definition_id="agent-bs01",
                session_id="session-bs01",
                input_message="hello",
            )
        )
        payload_run = result.to_payload()
        assert payload_run["runtime_profile_id"] == "platform-default", payload_run
        assert payload_run["output"] == "echo: hello"
        assert payload_run["execution_id"]


async def _create_agent(
    client: AsyncClient, model_id: str, model_version: str
) -> object:
    return await client.post(
        "/studio/agents",
        json={
            "resource_id": "agent-bs01",
            "version": "1",
            "spec": _agent_spec_without_profile_ref(model_id, model_version),
        },
        headers=tenant_headers(request_id="req-bs01-create"),
    )


async def _publish_agent(client: AsyncClient) -> object:
    return await client.post(
        "/studio/agents/agent-bs01/versions/1:publish",
        headers=tenant_headers(request_id="req-bs01-publish"),
    )
