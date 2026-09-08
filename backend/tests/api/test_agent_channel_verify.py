"""Agent 渠道 verify 回归测试。

- resolve 链构建失败（如存量 RuntimeProfile spec 仍带 V2 已删除的
  request_timeout_ms / max_retries 字段导致 ValidationError）必须以
  problems 返回 200，而不是 500（规则 18：显式失败清单）。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.agents.definitions import AgentDefinition, AgentModelPolicy
from fluxion.api.console import create_app
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.resources.contracts import ExactResourceVersion
from fluxion.resources.resource_specs import RuntimeProfile
from fluxion.runtime.secrets import LocalEncryptedSecretStore
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.console_contracts import ConsoleActor
from tests.console_helpers import tenant_headers
from tests.runtime_helpers import (
    TEST_POSTGRES_DSN,
    publish_resource,
    seed_model_definition,
    seed_tenant_policy,
)


class _StaleProfileResolveRuntime:
    """resolve 替身：复现存量 profile 漂移时的 ValidationError。"""

    async def resolve_context(self, request: object) -> dict[str, object]:
        RuntimeProfile.model_validate(
            {
                "max_rounds": 8,
                "default": True,
                "request_timeout_ms": 30000,
                "max_retries": 1,
            }
        )
        raise AssertionError("unreachable")


def _actor() -> ConsoleActor:
    return ConsoleActor(
        tenant_id="tenant-a",
        actor_id="admin-a",
        request_id="req-verify",
        trace_id="trace-verify",
    )


async def _seed_published_agent_with_entry(service: ConsoleApplicationService) -> None:
    store = service._store  # type: ignore[attr-defined]
    await seed_model_definition(store, tenant_id="tenant-a", provider_id="dev.echo")
    await seed_tenant_policy(store, tenant_id="tenant-a")
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="p1",
        version="1",
        spec={"max_rounds": 8, "default": True},
    )
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
                runtime_profile_ref=ExactResourceVersion(id="p1", version="1"),
            ).model_dump(mode="json"),
        )
    )
    await store.publish(
        ResourceKind.AGENT_DEFINITION, "assistant", tenant_id="tenant-a", version="1"
    )
    await service.create_platform_user(_actor(), platform_user_id="alice", display_name="Alice")
    await service.issue_chat_access(_actor(), platform_user_id="alice", agent_id="assistant")


@pytest.mark.asyncio
async def test_verify_reports_resolve_failure_as_problems_not_500() -> None:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = ConsoleApplicationService(
        store, secret_store=LocalEncryptedSecretStore(master_key=b"c" * 32)
    )
    await service.initialize()
    try:
        await _seed_published_agent_with_entry(service)
        app = create_app(
            service,
            runtime_service=_StaleProfileResolveRuntime(),  # type: ignore[arg-type]
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/studio/agents/assistant/channels/web:verify",
                headers=tenant_headers(request_id="req-verify-resolve-fail"),
            )
    finally:
        await service.close()
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["ok"] is False
    assert any("resolve 链构建失败" in str(problem) for problem in payload["problems"])


@pytest.mark.asyncio
async def test_verify_ok_when_resolve_succeeds() -> None:
    class _OkRuntime:
        async def resolve_context(self, request: object) -> dict[str, object]:
            return {"resolved": True}

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = ConsoleApplicationService(
        store, secret_store=LocalEncryptedSecretStore(master_key=b"c" * 32)
    )
    await service.initialize()
    try:
        await _seed_published_agent_with_entry(service)
        app = create_app(
            service,
            runtime_service=_OkRuntime(),  # type: ignore[arg-type]
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/studio/agents/assistant/channels/web:verify",
                headers=tenant_headers(request_id="req-verify-ok"),
            )
    finally:
        await service.close()
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["ok"] is True
    assert payload["problems"] == []
    assert payload["resolve"] == {"resolved": True}
