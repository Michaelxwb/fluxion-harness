"""B-108 / S-05 / RULE-08: 入站 Redis 原子去重与降级。

不得 Mock 的真实边界：真实 Redis（SET NX EX 600 / TTL / 故障）+ 真实 Runtime HTTP
接收端（Gateway 经真实 HTTP 调用 POST /v1/runs 并被观测）。
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import redis.asyncio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fakes import FakeConsoleClient, make_envelope, resolved_response
from muad_api.catalog import MessageCatalog
from muad_common import SharedSettings
from muad_im_gateway.application.inbound import DEDUPE_TTL_SEC, InboundPipeline
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.dedupe import (
    NullDedupeStore,
    RedisDedupeStore,
    build_dedupe_store,
)

DEDUPE_PREFIX = "im:dedupe"
RUNS_PATH = "/v1/runs"
READY_TIMEOUT_SEC = 15.0
TENANT_ID = "tenant-dedupe"


class RuntimeReceiver:
    """真实 Runtime HTTP 接收端：记录 POST /v1/runs 并返回真实 SSE 流。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        app = FastAPI()

        @app.post(RUNS_PATH)
        async def _runs(request: Request) -> StreamingResponse:
            self.requests.append(json.loads((await request.body()).decode("utf-8")))

            async def _stream() -> AsyncIterator[str]:
                for event_type, data in (
                    ("run.created", {"run_id": "run-1", "resumed": False}),
                    ("message.delta", {"delta": "已收到"}),
                    ("run.completed", {"status": "COMPLETED", "final_text": "已收到"}),
                ):
                    yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

            return StreamingResponse(_stream(), media_type="text/event-stream")

        self._app = app
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def start(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        config = uvicorn.Config(self._app, host="127.0.0.1", port=port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + READY_TIMEOUT_SEC
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError("runtime receiver did not start")
        self.url = f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


@pytest.fixture()
async def runtime_receiver() -> AsyncIterator[RuntimeReceiver]:
    receiver = RuntimeReceiver()
    receiver.start()
    try:
        yield receiver
    finally:
        receiver.stop()


@pytest.fixture()
async def redis_store() -> AsyncIterator[RedisDedupeStore]:
    settings = SharedSettings()
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL 未配置：入站去重验收需要真实 Redis（不得 skip）")
    store = await build_dedupe_store(settings.redis_url)
    if not isinstance(store, RedisDedupeStore):
        raise RuntimeError("Redis 不可达，build_dedupe_store 降级为 Null store：验收需要真实 Redis")
    try:
        yield store
    finally:
        await store.aclose()


def _pipeline(
    *,
    store: Any,
    receiver: RuntimeReceiver,
    catalog: MessageCatalog,
) -> tuple[InboundPipeline, FakeChannelAdapter]:
    adapter = FakeChannelAdapter()
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    pipeline = InboundPipeline(
        dedupe=store,
        console=console,
        runtime=RuntimeClient(receiver.url),
        catalog=catalog,
        tenant_id=TENANT_ID,
        locale="zh-CN",
    )
    return pipeline, adapter


async def _cleanup_keys(store: RedisDedupeStore, message_ids: list[str]) -> None:
    for message_id in message_ids:
        await store.release(f"{DEDUPE_PREFIX}:WECOM:{message_id}")


async def test_b108_duplicate_message_is_ignored_with_single_downstream_call(
    redis_store: RedisDedupeStore,
    runtime_receiver: RuntimeReceiver,
    catalog: MessageCatalog,
) -> None:
    pipeline, adapter = _pipeline(store=redis_store, receiver=runtime_receiver, catalog=catalog)
    message_id = f"msg-{time.time_ns()}"
    envelope = make_envelope(text="帮我检查设备", message_id=message_id)
    try:
        await pipeline.handle(adapter, envelope)
        assert len(runtime_receiver.requests) == 1
        assert runtime_receiver.requests[0]["message"]["id"] == message_id  # message_id 原样透传

        await pipeline.handle(adapter, envelope)  # 重复投递：ACK/忽略
        assert len(runtime_receiver.requests) == 1

        key = f"{DEDUPE_PREFIX}:WECOM:{message_id}"
        assert await redis_store.get_value(key) == "1"
        ttl = await redis_store._client.ttl(key)  # noqa: SLF001 - 断言真实 Redis TTL
        assert 0 < ttl <= DEDUPE_TTL_SEC
        assert DEDUPE_TTL_SEC == 600
    finally:
        await _cleanup_keys(redis_store, [message_id])


async def test_s05_set_nx_ex600_and_second_delivery_creates_no_second_run(
    redis_store: RedisDedupeStore,
    runtime_receiver: RuntimeReceiver,
    catalog: MessageCatalog,
) -> None:
    pipeline, adapter = _pipeline(store=redis_store, receiver=runtime_receiver, catalog=catalog)
    message_id = f"msg-s05-{time.time_ns()}"
    envelope = make_envelope(text="普通消息", message_id=message_id)
    key = f"{DEDUPE_PREFIX}:WECOM:{message_id}"
    try:
        assert await redis_store.set_if_absent(key, DEDUPE_TTL_SEC) is True  # SET NX 首次成功
        assert await redis_store.set_if_absent(key, DEDUPE_TTL_SEC) is False  # 第二次 NX 失败
        ttl = await redis_store._client.ttl(key)  # noqa: SLF001
        assert 595 <= ttl <= 600

        await pipeline.handle(adapter, envelope)  # 已存在 → 视为重复，不创建 Run
        assert runtime_receiver.requests == []
    finally:
        await _cleanup_keys(redis_store, [message_id])


async def test_b108_concurrent_same_message_id_calls_downstream_once(
    redis_store: RedisDedupeStore,
    runtime_receiver: RuntimeReceiver,
    catalog: MessageCatalog,
) -> None:
    pipeline, adapter = _pipeline(store=redis_store, receiver=runtime_receiver, catalog=catalog)
    message_id = f"msg-concurrent-{time.time_ns()}"
    envelope = make_envelope(text="并发同一消息", message_id=message_id)
    try:
        await asyncio.gather(
            pipeline.handle(adapter, envelope),
            pipeline.handle(adapter, envelope),
        )
        assert len(runtime_receiver.requests) == 1  # SET NX 原子性：跨副本只放行一次
    finally:
        await _cleanup_keys(redis_store, [message_id])


async def test_b108_redis_failure_degrades_to_at_least_once(
    runtime_receiver: RuntimeReceiver,
    catalog: MessageCatalog,
) -> None:
    # 指向不可达端口：store 操作抛 DedupeStoreError，管道必须继续处理（at-least-once）
    broken = RedisDedupeStore(redis.asyncio.Redis.from_url("redis://127.0.0.1:1"))
    pipeline, adapter = _pipeline(store=broken, receiver=runtime_receiver, catalog=catalog)
    message_id = f"msg-degraded-{time.time_ns()}"
    envelope = make_envelope(text="Redis 不可用", message_id=message_id)
    try:
        await pipeline.handle(adapter, envelope)
        assert len(runtime_receiver.requests) == 1
        assert runtime_receiver.requests[0]["message"]["id"] == message_id
    finally:
        await broken.aclose()


async def test_b108_negative_control_without_store_duplicates_pass_through(
    runtime_receiver: RuntimeReceiver,
    catalog: MessageCatalog,
) -> None:
    """反证：去重关闭（Null store，仅进程内不跨副本）时同一 message_id 会重复下游调用。

    用于证明上面的去重断言不是空断言（若实现被改回不跨副本去重，此用例与 b108 用例会同时暴露）。
    """
    pipeline, adapter = _pipeline(
        store=NullDedupeStore(), receiver=runtime_receiver, catalog=catalog
    )
    envelope = make_envelope(text="去重关闭", message_id=f"msg-null-{time.time_ns()}")
    await pipeline.handle(adapter, envelope)
    await pipeline.handle(adapter, envelope)
    assert len(runtime_receiver.requests) == 2
