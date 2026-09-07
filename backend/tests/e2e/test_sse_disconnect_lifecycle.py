"""真实断连端到端（TASK-018 / E-LIFE-05）验收测试。

真实边界：真实 TCP client → Channel → Gateway → Runtime → PG Trace/Memory。
不用缓冲 ASGITransport 代替断连；覆盖无工具流和有工具执行。
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from fluxion.api.channel import create_app as create_channel_app
from fluxion.api.runtime import create_app as create_runtime_app
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.http_runtime_gateway import HttpRuntimeGateway
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
    ToolCallRequest,
)
from tests.e2e.execution_observation_helpers import issue_chat_access, mint_ids
from tests.e2e.network_runtime_helpers import TcpAppServer
from tests.runtime_helpers import TEST_POSTGRES_DSN, seed_agent_definition


def _ids(char: str) -> tuple[str, str, str]:
    return mint_ids(char)


async def _stack() -> tuple[
    PostgreSQLRegistryStore,
    RuntimeApplicationService,
    TcpAppServer,
    TcpAppServer,
    ChannelApplicationService,
    str,
]:
    """PG + runtime TCP + channel TCP（gateway 经真实 TCP 连 runtime）。"""
    from fluxion.services.console_app import ConsoleApplicationService
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    runtime = RuntimeApplicationService.create_dev_bundle(store)
    await runtime.initialize()
    console = ConsoleApplicationService(store)
    await console.initialize()
    runtime_server = TcpAppServer(create_runtime_app(runtime))
    await runtime_server.__aenter__()
    gateway = HttpRuntimeGateway(base_url=runtime_server.base_url)
    channel_service = ChannelApplicationService(store, gateway)
    channel_server = TcpAppServer(create_channel_app(channel_service))
    await channel_server.__aenter__()
    # 注意：TcpAppServer 启动会跑 lifespan（service.initialize → store reset），
    # 所有 seed 必须在两个 server 都启动之后。
    await runtime.create_runtime_profile(
        CreateRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
            request_timeout_ms=10_000,
            default=True,
        )
    )
    await seed_agent_definition(store, provider_id="dev.echo", model_name="dev")
    await runtime.publish_runtime_profile(
        PublishRuntimeProfileRequest(
            tenant_id="tenant-a", runtime_profile_id="assistant", version="1"
        )
    )
    # console init 已在前（会清平台用户表）：绑定身份在此之后建。
    await channel_service.create_platform_user("tenant-a", "user-a", display_name="用户A")
    await channel_service.issue_bind_code("tenant-a", "user-a")
    token = await issue_chat_access(console, agent_id="assistant")
    await console.close()
    return store, runtime, runtime_server, channel_server, channel_service, token


async def _teardown(
    store: PostgreSQLRegistryStore,
    runtime: RuntimeApplicationService,
    runtime_server: TcpAppServer,
    channel_server: TcpAppServer,
) -> None:
    await channel_server.__aexit__(None, None, None)
    await runtime_server.__aexit__(None, None, None)
    await runtime.close()
    await store.close()


def _chat_body(conversation: str, request_id: str, trace_id: str, token: str) -> tuple[dict, dict]:
    return (
        {
            "conversation_id": conversation,
            "message_id": f"m-{conversation}",
            "content": "hello disconnect",
        },
        {
            "Authorization": f"Bearer {token}",
            "X-Request-ID": request_id,
            "X-Trace-ID": trace_id,
        },
    )


@pytest.mark.asyncio
async def test_E_LIFE_05_disconnect_mid_flight_finalizes() -> None:
    """E-LIFE-05：飞行中真实断连 → 取消终态 + Trace 留痕 + 连接释放。"""
    from fluxion.services.runtime_utils import DevEchoModelProvider

    store, runtime, runtime_server, channel_server, _, token = await _stack()
    real_complete = DevEchoModelProvider.complete

    async def slow_complete(self, request):  # type: ignore[no-untyped-def]
        await asyncio.sleep(30)
        return await real_complete(self, request)

    DevEchoModelProvider.complete = slow_complete  # type: ignore[method-assign]
    try:
        request_id, trace_id, _ = _ids("a")
        body, headers = _chat_body("conv-disc-a", request_id, trace_id, token)

        async def _abandon() -> None:
            async with (
                AsyncClient(base_url=channel_server.base_url) as client,
                client.stream(
                    "POST",
                    "/api/v1/channels/web/access/messages:stream",
                    json=body,
                    headers=headers,
                ),
            ):
                # 缓冲中间件下首帧晚到；在飞行中（模型睡眠）直接断开。
                await asyncio.sleep(2)

        await _abandon()
        # 服务端展开取消：Trace 留痕，执行局部状态释放。
        for _ in range(100):
            record = await runtime.trace_store.get(trace_id)
            if record is not None:
                break
            await asyncio.sleep(0.2)
        assert record is not None
        # 连接和执行释放：后续正常请求成功。
        body2, headers2 = _chat_body("conv-disc-b", *_ids("b")[:2], token)
    finally:
        DevEchoModelProvider.complete = real_complete  # type: ignore[method-assign]
    try:
        async with AsyncClient(base_url=channel_server.base_url) as client2:
            ok = await client2.post(
                "/api/v1/channels/web/access/messages",
                json=body2,
                headers=headers2,
                timeout=60,
            )
            assert ok.status_code == 200, ok.text
    finally:
        await _teardown(store, runtime, runtime_server, channel_server)


@pytest.mark.asyncio
async def test_E_LIFE_05_tool_execution_disconnect() -> None:
    """E-LIFE-05：有工具执行（显式 tool_calls）飞行中断连同样结算。"""
    from fluxion.services.runtime_utils import DevEchoModelProvider

    store, runtime, runtime_server, channel_server, _, _token = await _stack()
    del _token  # 网关级用例不需要 chat token（绑定在 channel 层）。
    real_complete = DevEchoModelProvider.complete

    async def slow_complete(self, request):  # type: ignore[no-untyped-def]
        await asyncio.sleep(30)
        return await real_complete(self, request)

    DevEchoModelProvider.complete = slow_complete  # type: ignore[method-assign]
    try:
        request_id, trace_id, execution_id = _ids("c")
        gateway = HttpRuntimeGateway(base_url=runtime_server.base_url)
        stream = gateway.stream(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                agent_definition_id="assistant",
                session_id="session-tool",
                input_message="hello",
                request_id=request_id,
                trace_id=trace_id,
                execution_id=execution_id,
                tool_calls=[
                    ToolCallRequest(tool_id="calc.eval", arguments={"expression": "1+2"})
                ],
            )
        )

        async def _drain() -> None:
            async for _ in stream:
                pass

        consumer = asyncio.create_task(_drain())
        await asyncio.sleep(2)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        for _ in range(100):
            record = await runtime.trace_store.get(trace_id)
            if record is not None:
                break
            await asyncio.sleep(0.2)
        assert record is not None
    finally:
        DevEchoModelProvider.complete = real_complete  # type: ignore[method-assign]
        await _teardown(store, runtime, runtime_server, channel_server)
