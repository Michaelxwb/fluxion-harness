"""E2E-01 / E2E-02：Command Plane 端到端验收（D1 改写后范围）。

- E2E-01（Web 真实链路）：/new → 普通 prompt → 长任务 /stop → /status。
- E2E-02（stub-im 语义链）：/bind → /help → /skills → /skill → /new（真 PG + 真 Runtime）。

先写测试记 RED：E2E-01/02 在 TASK-007 实现前不存在（命令审计等断言依赖 TASK-007）。
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.plugins.channel_adapters import StubImChannelAdapter
from fluxion.protocols.channel import ExternalChannelMessage
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.console_contracts import ConsoleActor
from fluxion.services.runtime_app import RuntimeApplicationService
from tests.channel_helpers import verified_identity
from tests.runtime_helpers import TEST_POSTGRES_DSN, publish_resource, seed_agent_definition

BASE = "http://channel"


def _actor() -> ConsoleActor:
    return ConsoleActor(
        tenant_id="tenant-a", actor_id="admin-a",
        request_id="req-direct", trace_id="trace-direct",
    )


@pytest.mark.asyncio
async def test_E2E01_web_new_prompt_stop_status() -> None:
    from fluxion.api.channel import create_app as create_channel_app
    from fluxion.services.runtime_utils import DevEchoModelProvider

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    runtime = RuntimeApplicationService.create_dev_bundle(store)
    await runtime.initialize()
    channel = ChannelApplicationService(store, runtime)
    console = ConsoleApplicationService(store)
    app = create_channel_app(channel)
    client = AsyncClient(transport=ASGITransport(app=app), base_url=BASE)
    real_complete = DevEchoModelProvider.complete

    async def slow_complete(self, request):  # type: ignore[no-untyped-def]
        await asyncio.sleep(30)
        return await real_complete(self, request)

    try:
        await console.create_platform_user(_actor(), platform_user_id="user-e2e", display_name="E2E")
        await publish_resource(
            store, tenant_id="tenant-a", kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant", version="1", spec={"max_rounds": 8, "default": True},
        )
        agent_id = await _publish_echo_agent(store)
        issued = await console.issue_chat_access(_actor(), platform_user_id="user-e2e", agent_id=agent_id)
        headers = {"Authorization": f"Bearer {issued.token}", "X-Tenant-ID": "tenant-a"}

        new_result = await client.post(
            "/api/v1/channels/web/access/messages",
            json={"conversation_id": "conv-e2e", "message_id": "m-new", "content": "/new"},
            headers=headers,
        )
        assert new_result.status_code == 200, new_result.text
        assert new_result.json()["data"]["kind"] == "command"

        hello = await client.post(
            "/api/v1/channels/web/access/messages",
            json={"conversation_id": "conv-e2e", "message_id": "m-hi", "content": "hello"},
            headers=headers,
        )
        assert hello.json()["data"]["kind"] == "message"

        DevEchoModelProvider.complete = slow_complete  # type: ignore[method-assign]
        slow_task = asyncio.ensure_future(
            client.post(
                "/api/v1/channels/web/access/messages",
                json={"conversation_id": "conv-e2e", "message_id": "m-slow", "content": "long task"},
                headers=headers,
            )
        )
        await asyncio.sleep(2)
        try:
            stopped = await client.post(
                "/api/v1/channels/web/access/messages",
                json={"conversation_id": "conv-e2e", "message_id": "m-stop", "content": "/stop"},
                headers=headers,
            )
            assert stopped.json()["data"]["code"] == "stop_requested"
        finally:
            DevEchoModelProvider.complete = real_complete  # type: ignore[method-assign]
            slow_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await slow_task

        status = await client.post(
            "/api/v1/channels/web/access/messages",
            json={"conversation_id": "conv-e2e", "message_id": "m-status", "content": "/status"},
            headers=headers,
        )
        assert status.json()["data"]["command"] == "status"
    finally:
        await client.aclose()
        await runtime.close()
        await store.close()


async def _publish_echo_agent(store: PostgreSQLRegistryStore) -> str:
    await seed_agent_definition(store, system_prompt="你是测试代理。", provider_id="dev.echo")
    return "assistant"


def _im(content: str, message_id: str) -> ExternalChannelMessage:
    from uuid import uuid4

    return ExternalChannelMessage(
        tenant_id="tenant-a", channel_user_id="im-1", conversation_id="conv-im-1",
        message_id=message_id, content=content, agent_id="assistant",
        request_id=f"req_{uuid4().hex}", trace_id=f"trace_{uuid4().hex}",
    )


@pytest.mark.asyncio
async def test_E2E02_stub_im_full_chain() -> None:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    runtime = RuntimeApplicationService.create_dev_bundle(store)
    await store.initialize()
    await runtime.initialize()
    try:
        service = ChannelApplicationService(store, runtime)
        await service.create_platform_user("tenant-a", "user-a")
        await publish_resource(
            store, tenant_id="tenant-a", kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant", version="1", spec={"max_rounds": 8, "default": True},
        )
        await publish_resource(
            store, tenant_id="tenant-a", kind=ResourceKind.SKILL, resource_id="s1",
            version="1",
            spec={"name": "s1", "instructions": "做事。", "required_capabilities": [], "visibility": "public"},
        )
        await seed_agent_definition(
            store, system_prompt="你是测试代理。", provider_id="dev.echo",
            capabilities=[{"type": "skill", "capability_ref": "s1", "version_pin": "1"}],
        )
        im = StubImChannelAdapter()
        issued = await service.issue_bind_code("tenant-a", "user-a")

        bound = await service.handle(im, _im(f"/bind {issued.code}", "m-1"), verified=None)
        assert bound.kind == "bound"

        me = verified_identity("im-1")
        help_result = await service.handle(im, _im("/help", "m-2"), verified=me)
        assert help_result.to_payload()["code"] == "ok"

        skills = await service.handle(im, _im("/skills", "m-3"), verified=me)
        assert "s1" in skills.to_payload()["output"]

        used = await service.handle(im, _im("/skill s1 干活", "m-4"), verified=me)
        assert used.kind == "message"
        assert used.execution_id is not None

        rotated = await service.handle(im, _im("/new", "m-5"), verified=me)
        assert rotated.to_payload()["code"] == "ok"

        after = await service.handle(im, _im("after", "m-6"), verified=me)
        assert after.kind == "message"
    finally:
        await runtime.close()
        await store.close()
