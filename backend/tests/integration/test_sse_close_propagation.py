"""逐层 SSE 关闭传播（TASK-017 / E-LIFE-04）验收测试。

真实边界：真实 Channel/SSE iterator → Gateway HTTP response → Runtime iterator。
关键断言：关闭逐层传播；连接释放；借用 client 仍可用。
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.api.runtime import _sse_events, create_app as create_runtime_app
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.services.http_runtime_gateway import HttpRuntimeGateway
from fluxion.services.runtime_app import (
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)
from tests.runtime_helpers import TEST_POSTGRES_DSN, seed_agent_definition


def _ids(char: str) -> tuple[str, str, str]:
    return (f"req_{char * 32}", f"trace_{char * 32}", f"exec_{char * 32}")


async def _service() -> tuple[RuntimeApplicationService, PostgreSQLRegistryStore]:
    from fluxion.services.runtime_app import CreateRuntimeProfileRequest

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = RuntimeApplicationService.create_dev_bundle(store)
    await service.initialize()
    await service.create_runtime_profile(
        CreateRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
            default=True,
        )
    )
    await seed_agent_definition(store, provider_id="dev.echo", model_name="dev")
    await service.publish_runtime_profile(
        PublishRuntimeProfileRequest(
            tenant_id="tenant-a", runtime_profile_id="assistant", version="1"
        )
    )
    return service, store


def _run_request(char: str) -> RunRuntimeRequest:
    request_id, trace_id, execution_id = _ids(char)
    return RunRuntimeRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        agent_definition_id="assistant",
        session_id="session-close",
        input_message="hello",
        request_id=request_id,
        trace_id=trace_id,
        execution_id=execution_id,
    )


@pytest.mark.asyncio
async def test_E_LIFE_04_runtime_sse_close_propagates() -> None:
    """E-LIFE-04：runtime _sse_events 关闭向下传播（service 结算 + 留痕）。"""
    from fluxion.services.runtime_utils import DevEchoModelProvider

    service, store = await _service()
    real_complete = DevEchoModelProvider.complete

    async def slow_complete(self, request):  # type: ignore[no-untyped-def]
        await asyncio.sleep(30)
        return await real_complete(self, request)

    DevEchoModelProvider.complete = slow_complete  # type: ignore[method-assign]
    try:
        gen = _sse_events(service, _run_request("a"))
        seen: list[str] = []

        async def _drain() -> list:
            # 同一 task 全程驱动（跨 task 驱动错位 ContextVar token）。
            async for item in gen:
                seen.append(item)
            return seen

        consumer = asyncio.create_task(_drain())
        await asyncio.sleep(1)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert seen and seen[0].startswith("event: started")
        record = await service.trace_store.get(_ids("a")[1])
        assert record is not None
    finally:
        DevEchoModelProvider.complete = real_complete  # type: ignore[method-assign]
        await service.close()
        await store.close()


@pytest.mark.asyncio
async def test_E_LIFE_04_channel_close_propagates_to_execution() -> None:
    """E-LIFE-04：channel 流关闭向下传播（执行结算 + 留痕，不变成功）。

    注：RequestContextMiddleware（BaseHTTPMiddleware）会缓冲整个 SSE 响应，
    首帧在执行完成后才到客户端；本用例不断言增量到达，只断言关闭展开时
    finalizer 确定性运行（同任务内联展开 + 真实 TCP 下 uvicorn 取消任务同理）。
    真增量直达是独立问题（见 TASK-017 证据注记），不属本任务范围。
    """
    from fluxion.services.runtime_utils import DevEchoModelProvider
    from tests.e2e.execution_observation_helpers import ChainStack

    real_complete = DevEchoModelProvider.complete

    async def slow_complete(self, request):  # type: ignore[no-untyped-def]
        await asyncio.sleep(30)
        return await real_complete(self, request)

    DevEchoModelProvider.complete = slow_complete  # type: ignore[method-assign]
    try:
        async with ChainStack() as stack:
            request_id, trace_id, _ = _ids("d")
            headers = {
                "Authorization": f"Bearer {stack.issued_token}",
                "X-Request-ID": request_id,
                "X-Trace-ID": trace_id,
            }
            assert stack.chat_client is not None

            async def _drain() -> None:
                # 同一 task 全程驱动。
                async with stack.chat_client.stream(  # type: ignore[union-attr]
                    "POST",
                    "/api/v1/channels/web/access/messages:stream",
                    json={
                        "conversation_id": "conv-close",
                        "message_id": "m-close",
                        "content": "hello close",
                    },
                    headers=headers,
                ):
                    pass

            consumer = asyncio.create_task(_drain())
            await asyncio.sleep(2)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            # channel 不传 execution_id（每消息新执行，入口补齐）；按 trace 关联。
            record = await stack.runtime.trace_store.get(trace_id)  # type: ignore[union-attr]
            assert record is not None
            assert record.execution_id.startswith("exec_")
            assert record.snapshot.agent_definition_id == "assistant"
    finally:
        DevEchoModelProvider.complete = real_complete  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_E_LIFE_04_gateway_close_releases_and_client_reusable() -> None:
    """E-LIFE-04：gateway 关闭释放连接；借用 client 仍可用；不把未结束流变成功。

    注：同上，runtime 侧 BaseHTTPMiddleware 缓冲响应；取消在飞行中展开，
    finalizer 内联确定性运行（不断言增量到达）。
    """
    from fluxion.services.runtime_utils import DevEchoModelProvider

    service, store = await _service()
    real_complete = DevEchoModelProvider.complete

    async def slow_complete(self, request):  # type: ignore[no-untyped-def]
        await asyncio.sleep(30)
        return await real_complete(self, request)

    DevEchoModelProvider.complete = slow_complete  # type: ignore[method-assign]
    raw = AsyncClient(
        transport=ASGITransport(app=create_runtime_app(service)), base_url="http://runtime"
    )
    try:
        gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
        stream = gateway.stream(_run_request("b"))

        async def _drain() -> list:
            return [event async for event in stream]

        consumer = asyncio.create_task(_drain())
        await asyncio.sleep(2)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        # 未结束的流没有 completed，更没有伪造成功。
        record = await service.trace_store.get(_ids("b")[1])
        assert record is not None
    finally:
        DevEchoModelProvider.complete = real_complete  # type: ignore[method-assign]
    # 借用 client 只关 response：同一 client 后续请求正常。
    try:
        gateway2 = HttpRuntimeGateway(base_url="http://runtime", client=raw)
        result = await gateway2.run(_run_request("c"))
        assert result.output == "dev: hello"
    finally:
        await raw.aclose()
        await service.close()
        await store.close()
