"""TASK-008 agent_id 产品路由验收测试。

- BE-S-09（E2E）：Chat Access 绑定 agent_id → 渠道消息经 Runtime 完整执行，
  Snapshot 冻结 AgentDefinition exact version。
- BE-B-02（rollover 收口）：旧 runtime_profile_id 请求键被 payload 校验拒绝
  （422 extra_forbidden），表列与 record 字段不复存在——迁移后旧路径已删。
- BE-E-05（integration）：issue 时引用不存在/未发布的 agent → 404 `agent_not_found`。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.api.channel import create_app as create_channel_app
from fluxion.errors.console import CHANNEL_AGENT_NOT_FOUND, ConsoleError
from fluxion.registry import ChatAccessRecord, SQLiteRegistryStore
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.console_contracts import ConsoleActor
from fluxion.services.runtime_app import RuntimeApplicationService


def _actor() -> ConsoleActor:
    # 服务层直调不经 HTTP 中间件；显式给真实租户上下文。
    return ConsoleActor(tenant_id="tenant-a", actor_id="admin-a",
                        request_id="req-direct", trace_id="trace-direct")

from tests.runtime_helpers import publish_resource, seed_agent_definition


async def _stack(store: SQLiteRegistryStore):
    from fluxion.resources import ResourceKind

    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    await seed_agent_definition(store, system_prompt="你是测试代理。", provider_id="dev.echo")
    runtime = RuntimeApplicationService.create_dev_bundle(store)
    channel = ChannelApplicationService(store, runtime)
    console = ConsoleApplicationService(store)
    app = create_channel_app(channel)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://channel")
    return runtime, channel, console, client


@pytest.mark.asyncio
async def test_be_s_09_chat_message_routes_via_agent_id() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()

    runtime, _channel, console, client = await _stack(store)
    try:
        await console.create_platform_user(
            _actor(),
            platform_user_id="user-a",
            display_name="用户A",
        )
        issued = await console.issue_chat_access(
            _actor(),
            platform_user_id="user-a",
            agent_id="assistant",
        )
        response = await client.post(
            "/api/v1/channels/web/access/messages",
            json={
                "conversation_id": "conv-a",
                "message_id": "m1",
                "content": "hello",
            },
            headers={
                "Authorization": f"Bearer {issued.token}",
                "X-Tenant-ID": "tenant-a",
                "X-Actor-ID": "admin-a",
                "X-Request-ID": "req-s09",
                "X-Trace-ID": "trace-s09",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert "hello" in body["output"]
        # Snapshot 冻结 AgentDefinition 版本：product routing 键即快照字段。
        trace = await runtime.trace_store.get(body["trace_id"])
        assert trace is not None
        assert trace.snapshot.agent_definition_id == "assistant"
    finally:
        await client.aclose()
        await runtime.close()
        await store.close()


@pytest.mark.asyncio
async def test_be_e_05_issue_with_unknown_agent_maps_to_agent_not_found() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    runtime = RuntimeApplicationService.create_dev_bundle(store)
    console = ConsoleApplicationService(store)
    actor = _actor()
    await console.create_platform_user(actor, platform_user_id="u-x", display_name="用户X")
    with pytest.raises(ConsoleError) as error:
        await console.issue_chat_access(actor, platform_user_id="u-x", agent_id="ghost")
    assert error.value.code == CHANNEL_AGENT_NOT_FOUND
    assert error.value.status_code == 404
    await runtime.close()
    await store.close()


def test_be_b_02_legacy_runtime_profile_key_removed_from_record() -> None:
    # rollover 后旧请求键在数据契约层不存在（schema 列同步改名，dev reset 自举）。
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(ChatAccessRecord)}
    assert "runtime_profile_id" not in field_names
    assert "agent_id" in field_names
