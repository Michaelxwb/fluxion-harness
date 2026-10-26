"""E-06: Redis 连接故障下的边界（真实 Gateway 进程 → 真实 Redis（故障/恢复）→ Runtime/Stream）。

架构事实（TASK-009 起与生产同构）：Gateway 是**请求面**，入站经 `im.inbound` 消费组、
出站经 `im.outbound` 命令流；`im.outbound` 的可靠性与 bot 长连接都在 Redis 之后。于是
Redis 不可用时 Gateway 的正确行为是**显式不可用**（就绪 503、投递 500），而不是假装送达；
入站事实由 Connector 持久化在 Stream 里，恢复后照常被消费（不丢消息）。

不得 Mock 的真实边界：真实 Gateway 进程（可用/不可用 Redis 两套）、真实 Redis、真实
Runtime/Console 进程、真实 Connector 进程 → 官方 SDK → 真实本地 WS 探针；验收类不制造 RED。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    BOUND_EXTERNAL_USER_ID,
    CHAT_ID,
    GatewayStack,
    ServiceProcess,
    free_port,
)
from tests.e2e.seed_im_gateway import TENANT
from tests.e2e.wecom_probe_app import frame_text

DELIVERIES_PATH = "/internal/deliveries"
WAIT_TIMEOUT_SEC = 60.0
DEAD_REDIS_URL = "redis://127.0.0.1:1/0"


async def _scalar(statement: str, params: dict[str, object]) -> object:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(statement), params)
    finally:
        await engine.dispose()


async def _wait_for(predicate) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    while time.monotonic() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.3)
    raise AssertionError("条件未在超时内满足")


def _gateway_process(
    stack: GatewayStack, root: Path, *, redis_url: str, name: str
) -> ServiceProcess:
    artifacts = root / f"artifacts-{name}"
    cache = root / f"skill-cache-{name}"
    artifacts.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    return ServiceProcess(
        name=name,
        module="muad_im_gateway.main",
        port=free_port(),
        log_path=root / f"{name}.log",
        env={
            **os.environ,
            "DATABASE_URL": SharedSettings().require_database_url(),
            "REDIS_URL": redis_url,
            "ARTIFACT_ROOT": str(artifacts),
            "SKILL_CACHE_ROOT": str(cache),
            "DEFAULT_TENANT_ID": TENANT,
            "INTERNAL_SERVICE_TOKEN": "e2e-im-internal-service-token",
            "CONSOLE_PLATFORM_URL": stack.console_url,
            "AGENT_RUNTIME_URL": stack.runtime_url,
            # 请求面只路由：长连接在 Connector，Gateway 不拿 WS 地址/CA。
            "GATEWAY_ROUTED_CHANNELS": "WECOM",
        },
    )


async def _http_ready(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            return (await client.get(f"{url}/healthz")).status_code == 200
    except httpx.HTTPError:
        return False


async def _status_of(url: str) -> int:
    async with httpx.AsyncClient(timeout=2.0) as client:
        return (await client.get(f"{url}/readyz")).status_code


@pytest.fixture()
async def degraded_gateway(
    gateway_stack: GatewayStack, tmp_path: Path
) -> AsyncIterator[ServiceProcess]:
    """Redis 不可用的 Gateway 进程（先停掉栈内正常 Gateway，避免同组消费互相抢事件）。"""
    gateway_stack.processes["gateway"].stop()
    process = _gateway_process(
        gateway_stack, tmp_path, redis_url=DEAD_REDIS_URL, name="gateway-degraded"
    )
    process.start()
    try:
        await _wait_for(lambda: _http_ready(process.url))
        yield process
    finally:
        process.stop()


def _sent_texts(stack: GatewayStack) -> list[str]:
    probe = stack.ws_probe
    assert probe is not None
    texts: list[str] = []
    for item in probe.received:  # type: ignore[attr-defined]
        if item.frame.get("cmd") in ("aibot_respond_msg", "aibot_send_msg"):
            texts.append(frame_text(item.frame))
    return texts


def _delivery_body(stack: GatewayStack, key: str) -> dict[str, object]:
    return {
        "tenant_id": stack.tenant_id,
        "task_id": key.split(":")[1],
        "delivery_key": key,
        "route": {
            "channel": "WECOM",
            "bot_id": BOT_ID,
            "external_user_id": BOUND_EXTERNAL_USER_ID,
            "external_conversation_id": CHAT_ID,
        },
        "message": {"type": "text", "text": "降级投递"},
    }


async def test_e06_dead_redis_gateway_fails_explicitly_and_keeps_inbound_durable(
    gateway_stack: GatewayStack, degraded_gateway: ServiceProcess
) -> None:
    """Redis 不可用：就绪显式 503、投递显式 500；入站事实留在 Stream 里等恢复。"""
    assert (await _status_of(degraded_gateway.url)) == 503, (
        "Redis 不可用时 /readyz 必须显式 503（im.inbound/im.outbound 都在 Redis 之后）"
    )
    assert (await _http_ready(degraded_gateway.url)), "存活探针不因依赖故障重启 Pod"

    delivered_before = len(_sent_texts(gateway_stack))
    key = f"task:{uuid.uuid4()}:final"
    async with httpx.AsyncClient(
        base_url=degraded_gateway.url,
        timeout=30.0,
        headers=gateway_stack.service_headers(),
    ) as client:
        response = await client.post(DELIVERIES_PATH, json=_delivery_body(gateway_stack, key))
    assert response.status_code == 500, response.text
    assert response.json()["code"] != "", "失败必须带显式错误码，不得假装送达"
    await asyncio.sleep(1.0)
    assert len(_sent_texts(gateway_stack)) == delivered_before, "Redis 不可用时不得真实发出任何消息"

    # 入站事实由 Connector 持久化在 im.inbound：停机窗口内推的消息不丢。
    probe = gateway_stack.ws_probe
    assert probe is not None
    marker = f"e06-durable-{uuid.uuid4().hex[:8]}"
    await probe.push_message(  # type: ignore[attr-defined]
        bot_id=BOT_ID,
        message_id=marker,
        external_user_id=BOUND_EXTERNAL_USER_ID,
        text=marker,
        reply_id="req-e06-durable",
        chat_id=CHAT_ID,
    )

    # 恢复一个正常 Gateway：积压的入站事件被消费并建 Run（at-least-once、不丢）。
    restored = _gateway_process(
        gateway_stack,
        degraded_gateway.log_path.parent,
        redis_url=SharedSettings().require_redis_url(),
        name="gateway-restored",
    )
    restored.start()
    try:
        await _wait_for(lambda: _http_ready(restored.url))

        async def _run_exists() -> bool:
            runs = await _scalar(
                "SELECT count(*) FROM runtime.run_record WHERE tenant_id = :t AND input_text = :text",
                {"t": gateway_stack.tenant_id, "text": marker},
            )
            return int(runs or 0) == 1

        await _wait_for(_run_exists)
    finally:
        restored.stop()


async def test_e06_dedupe_recovers_after_redis_available(
    gateway_stack: GatewayStack, tmp_path: Path
) -> None:
    gateway_stack.processes["gateway"].stop()
    process = _gateway_process(
        gateway_stack, tmp_path, redis_url=SharedSettings().require_redis_url(), name="gateway-restored"
    )
    process.start()
    probe = gateway_stack.ws_probe
    assert probe is not None
    try:
        await _wait_for(lambda: _http_ready(process.url))
        message_id = f"e06-dup-{uuid.uuid4().hex[:8]}"
        for _ in range(2):
            await probe.push_message(  # type: ignore[attr-defined]
                bot_id=BOT_ID,
                message_id=message_id,
                external_user_id=BOUND_EXTERNAL_USER_ID,
                text="恢复后的去重",
                reply_id="req-e06-dup",
                chat_id=CHAT_ID,
            )
        await asyncio.sleep(3.0)
        # 恢复后：同 message_id 只产生一个 Run（入口去重 + 下游幂等键兜底）
        runs = await _scalar(
            "SELECT count(*) FROM runtime.run_record WHERE tenant_id = :t AND input_text = :text",
            {"t": gateway_stack.tenant_id, "text": "恢复后的去重"},
        )
        assert int(runs or 0) == 1, f"恢复后去重未生效：runs={runs}"
    finally:
        process.stop()
