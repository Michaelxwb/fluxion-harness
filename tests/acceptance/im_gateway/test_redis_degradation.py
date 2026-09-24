"""E-06: Redis 连接故障下的入站与投递降级（真实 Gateway 进程 → 真实 Redis（故障/恢复）→ Runtime/WS）。

不得 Mock 的真实边界：真实 Gateway 进程（可用/不可用 Redis 两套）、真实 Redis、真实
Runtime/Console 进程、生产 `WeComAdapter` → 官方 SDK → 真实本地 WS 探针；验收类不制造 RED。
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
    CHAT_ID,
    BOUND_EXTERNAL_USER_ID,
    GatewayStack,
    ServiceProcess,
    free_port,
)
from tests.e2e.seed_im_gateway import TENANT

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
    probe = stack.ws_probe
    assert probe is not None
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
            "WECOM_WS_URL": stack.wecom_ws_url,
            "WECOM_WS_CA_FILE": str(probe.cert_path),  # type: ignore[attr-defined]
        },
    )


async def _wait_ws_connected(stack: GatewayStack, baseline: int) -> None:
    """等新起的 Gateway 进程在真实 WS 探针上完成认证（推送前必须已连上）。"""
    probe = stack.ws_probe
    assert probe is not None
    await _wait_for(
        lambda: len(probe.connections) > baseline  # type: ignore[attr-defined]
        and probe.frames_of("aibot_subscribe")  # type: ignore[attr-defined]
    )


async def _http_ready(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            return (await client.get(f"{url}/healthz")).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture()
async def degraded_gateway(
    gateway_stack: GatewayStack, tmp_path: Path
) -> AsyncIterator[ServiceProcess]:
    """Redis 不可用的 Gateway 进程（先停掉栈内正常 Gateway，避免同 bot 双消费干扰）。"""
    gateway_stack.processes["gateway"].stop()
    probe = gateway_stack.ws_probe
    assert probe is not None
    baseline = len(probe.connections)  # type: ignore[attr-defined]
    process = _gateway_process(
        gateway_stack, tmp_path, redis_url=DEAD_REDIS_URL, name="gateway-degraded"
    )
    process.start()
    try:
        await _wait_for(lambda: _http_ready(process.url))
        await _wait_ws_connected(gateway_stack, baseline)
        yield process
    finally:
        process.stop()


def _sent_texts(stack: GatewayStack) -> list[str]:
    probe = stack.ws_probe
    assert probe is not None
    return [
        str(((item.frame.get("body") or {}).get("text") or {}).get("content") or "")
        for item in probe.received  # type: ignore[attr-defined]
        if item.frame.get("cmd") == "aibot_send_msg"
    ]


async def test_e06_inbound_and_delivery_continue_at_least_once_without_redis(
    gateway_stack: GatewayStack, degraded_gateway: ServiceProcess
) -> None:
    probe = gateway_stack.ws_probe
    assert probe is not None
    since = len(_sent_texts(gateway_stack))

    # 入站：Redis 故障下仍继续处理（at-least-once），消息进入 Runtime 并有回复
    message_id = f"e06-{uuid.uuid4().hex[:8]}"
    await probe.push_message(  # type: ignore[attr-defined]
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id=BOUND_EXTERNAL_USER_ID,
        text="Redis 故障下的入站",
        reply_id="req-e06",
        chat_id=CHAT_ID,
    )
    await _wait_for(lambda: _has_reply_after(gateway_stack, since))

    # 投递：Redis 故障下降级为 at-least-once（允许重复），但不得宣称失败
    key = f"task:{uuid.uuid4()}:final"
    body = {
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
    delivered_before = len(_sent_texts(gateway_stack))
    async with httpx.AsyncClient(base_url=degraded_gateway.url, timeout=30.0) as client:
        first = await client.post(DELIVERIES_PATH, json=body)
        second = await client.post(DELIVERIES_PATH, json=body)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["data"]["accepted"] is True
    assert second.json()["data"]["accepted"] is True
    # 降级语义：允许重复投递（不承诺 exactly-once），两次都真实发送
    await _wait_for(lambda: len(_sent_texts(gateway_stack)) - delivered_before >= 2)


def _has_reply_after(stack: GatewayStack, since: int) -> bool:
    return len(_sent_texts(stack)) > since


async def test_e06_dedupe_recovers_after_redis_available(
    gateway_stack: GatewayStack, tmp_path: Path
) -> None:
    gateway_stack.processes["gateway"].stop()
    process = _gateway_process(
        gateway_stack, tmp_path, redis_url=SharedSettings().require_redis_url(), name="gateway-restored"
    )
    probe = gateway_stack.ws_probe
    assert probe is not None
    baseline = len(probe.connections)  # type: ignore[attr-defined]
    process.start()
    try:
        await _wait_for(lambda: _http_ready(process.url))
        await _wait_ws_connected(gateway_stack, baseline)
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
        # 恢复后：同 message_id 只产生一个 Run（去重生效）
        runs = await _scalar(
            "SELECT count(*) FROM runtime.run_record WHERE tenant_id = :t AND input_text = :text",
            {"t": gateway_stack.tenant_id, "text": "恢复后的去重"},
        )
        assert int(runs or 0) == 1, f"恢复后去重未生效：runs={runs}"
    finally:
        process.stop()
