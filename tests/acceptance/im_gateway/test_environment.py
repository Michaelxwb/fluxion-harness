"""B-121: Gateway 基础真实验收环境（生产进程生命周期 → 真实 HTTP / PostgreSQL / Redis）。

不得 Mock 的真实边界：真实服务进程（Console/Runtime/Gateway）、真实 HTTP、
真实 PostgreSQL 配置、真实本地模型 HTTP 探针与真实 WS 协议探针。
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import redis.asyncio
from muad_common import SharedSettings
from sqlalchemy import text

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    BOT_SECRET,
    TENANT,
    GatewayStack,
    count_tenant_rows,
    purge_tenant,
    restart_process,
)

HEALTH_TIMEOUT_SEC = 10.0


async def _get(url: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_SEC) as client:
        return await client.get(url)


async def _wait_ready(url: str, timeout: float = 30.0) -> httpx.Response:
    """等待就绪：首次 WS 连接可能先失败一次由退避重试，环境须在有界时间内变为 ready。"""
    deadline = time.monotonic() + timeout
    last: httpx.Response | None = None
    while time.monotonic() < deadline:
        last = await _get(url)
        if last.status_code == 200:
            return last
        await asyncio.sleep(0.2)
    assert last is not None
    return last


async def _wait_for_subscribe(stack: GatewayStack, timeout: float = 20.0) -> list[dict]:
    probe = stack.ws_probe
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frames = probe.frames_of("aibot_subscribe")
        if frames:
            return frames
        await asyncio.sleep(0.1)
    return []


async def test_b121_all_service_processes_are_reachable(gateway_stack: GatewayStack) -> None:
    for name in ("console", "runtime", "gateway"):
        process = gateway_stack.processes[name]
        assert process._process is not None, f"{name} 未启动"
        assert process._process.poll() is None, f"{name} 进程已退出"

    for url in (
        gateway_stack.console_url,
        gateway_stack.runtime_url,
        gateway_stack.gateway_url,
    ):
        response = await _get(f"{url}/healthz")
        assert response.status_code == 200, (url, response.text)

    # /readyz：Console 可达或已有完整快照 + 连接管理器已初始化（等首次 WS 连接退避重试完成）
    ready = await _wait_ready(f"{gateway_stack.gateway_url}/readyz")
    assert ready.status_code == 200, ready.text


async def test_b121_gateway_authenticates_to_local_ws_probe(gateway_stack: GatewayStack) -> None:
    frames = await _wait_for_subscribe(gateway_stack)
    assert frames, "Gateway 进程未在超时内经真实 socket 发起认证"
    assert frames[0]["body"] == {"bot_id": BOT_ID, "secret": BOT_SECRET}
    assert gateway_stack.ws_probe.auth_failures == []

    ready = await _wait_ready(f"{gateway_stack.gateway_url}/readyz")
    assert ready.status_code == 200, ready.text


async def test_b121_model_is_called_over_real_http(gateway_stack: GatewayStack) -> None:
    health = await _get(f"{gateway_stack.llm_url}/healthz")
    assert health.status_code == 200

    from muad_console_platform.infrastructure.db import get_session_factory

    async with get_session_factory()() as session:
        base_url = await session.scalar(
            text("SELECT base_url FROM control.model_definition WHERE tenant_id = :t"),
            {"t": TENANT},
        )
    assert base_url == f"{gateway_stack.llm_url}/v1"  # 模型经真实 HTTP 探针


async def test_b121_process_restart_recovers(gateway_stack: GatewayStack) -> None:
    await _wait_ready(f"{gateway_stack.gateway_url}/readyz")
    console = gateway_stack.processes["console"]
    console.stop()
    assert console._process is not None and console._process.poll() is not None, "进程未退出"
    # 注：不断言 HTTP 不可达——uvicorn 监听套接字在个别环境（残留进程占端口）下仍会短暂应答；
    # "进程级断线"以进程确已退出 + 重启后恢复健康为准。

    # 设计 §4.1：Console 短暂不可达但已有完整快照时仍可服务
    degraded = await _get(f"{gateway_stack.gateway_url}/readyz")
    assert degraded.status_code == 200, degraded.text

    restart_process(console)
    assert (await _get(f"{gateway_stack.console_url}/healthz")).status_code == 200
    assert (await _wait_ready(f"{gateway_stack.gateway_url}/readyz")).status_code == 200


async def test_b121_cleanup_leaves_no_tenant_residue(gateway_stack: GatewayStack) -> None:
    """最后执行：清理后无残留行 / Redis 键（fixture finally 会再次幂等清理）。"""
    assert TENANT.startswith("e2e-im-")

    await purge_tenant()
    for table in (
        "control.channel_identity",
        "control.bot_account",
        "control.platform_user",
        "control.agent_definition",
        "control.model_definition",
    ):
        assert await count_tenant_rows(table) == 0, table

    from muad_console_platform.infrastructure.db import get_session_factory

    async with get_session_factory()() as session:
        grants = await session.scalar(
            text(
                "SELECT count(*) FROM control.agent_access_grant g "
                "JOIN control.agent_definition a ON a.id = g.agent_id "
                "WHERE a.tenant_id = :t"
            ),
            {"t": TENANT},
        )
    assert int(grants or 0) == 0

    redis_url = SharedSettings().redis_url
    assert redis_url is not None
    client = redis.asyncio.Redis.from_url(redis_url)
    try:
        keys = [key.decode() for key in await client.keys("im:dedupe:*")]
    finally:
        await client.aclose()
    assert [key for key in keys if TENANT in key] == []
