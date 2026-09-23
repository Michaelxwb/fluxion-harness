"""B-130: 第二 Runtime 实例与真实 Worker 进程（真实进程 → 真实 PostgreSQL/Redis）。

不得 Mock 的真实边界：生产第二 Runtime 实例与生产 Worker 进程（独立 uvicorn 进程、真实
socket、真实 PG/Redis）+ 真实 Gateway 投递 → 生产 WeComAdapter → 真实本地 WS 探针。
"""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx
from muad_common import SharedSettings
from muad_contracts import ChannelContext, MessageInput, RunRequest
from muad_im_gateway.application.runtime_client import RuntimeClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.im_gateway.environment import (
    GatewayStack,
    count_tenant_rows,
    purge_tenant,
)
from tests.e2e.seed_im_gateway import seed_delivery_task

HEALTH_TIMEOUT_SEC = 10.0
B130_TIMEOUT_SEC = 60.0
B130_INTENT = "b130_delivery_intent"


async def _get(url: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_SEC) as client:
        return await client.get(url)


async def _wait_for(predicate: object, *, what: str, timeout: float = B130_TIMEOUT_SEC) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return
        await asyncio.sleep(0.2)
    raise AssertionError(what)


async def _run_until_terminal(
    runtime_url: str, gateway_stack: GatewayStack, *, message_id: str
) -> dict[str, str]:
    """经生产 RuntimeClient（真实 SSE 封套解析）跑完整 Run，返回终态与 conversation_id。"""
    client = RuntimeClient(runtime_url)
    try:
        request = RunRequest(
            agent_id=gateway_stack.agent_id,
            platform_user_id=gateway_stack.platform_user_id,
            conversation_id=None,
            channel=ChannelContext(
                type="WECOM",
                bot_id=gateway_stack.bot_id,
                external_conversation_id=gateway_stack.chat_id,
            ),
            message=MessageInput(id=message_id, type="text", text="B-130 你好"),
        )
        conversation_id = ""
        status = ""
        async for event in client.create_run(
            request, tenant_id=gateway_stack.tenant_id, trace_id="trace-b130"
        ):
            if event.type == "run.created":
                conversation_id = str((event.data or {}).get("conversation_id") or "")
            if event.type == "run.completed":
                status = str((event.data or {}).get("status") or "")
        return {"conversation_id": conversation_id, "status": status}
    finally:
        await client.aclose()


async def _delivery_status(task_id: uuid.UUID) -> str:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(
                text("SELECT delivery_status FROM task.task_execution WHERE id = :id"),
                {"id": task_id},
            )
        return str(value or "")
    finally:
        await engine.dispose()


def _probe_texts(gateway_stack: GatewayStack) -> list[str]:
    probe = gateway_stack.ws_probe
    assert probe is not None, "WS 探针未接入栈"
    return [
        str(((item.frame.get("body") or {}).get("text") or {}).get("content") or "")
        for item in probe.received  # type: ignore[attr-defined]
        if item.frame.get("cmd") == "aibot_send_msg"
    ]


async def test_b130_both_runtime_instances_are_healthy_and_share_logical_routing(
    gateway_stack: GatewayStack,
) -> None:
    # 两个 Runtime 实例都是真实进程且健康
    for name in ("runtime", "runtime-2", "worker"):
        process = gateway_stack.processes[name]
        assert process._process is not None and process._process.poll() is None, name
    for url in (gateway_stack.runtime_url, gateway_stack.runtime2_url):
        assert (await _get(f"{url}/healthz")).status_code == 200, url
        assert (await _get(f"{url}/readyz")).status_code == 200, url

    first = await _run_until_terminal(
        gateway_stack.runtime_url, gateway_stack, message_id="b130-msg-1"
    )
    second = await _run_until_terminal(
        gateway_stack.runtime2_url, gateway_stack, message_id="b130-msg-2"
    )

    assert first["status"] == "COMPLETED", first
    assert second["status"] == "COMPLETED", second
    # 实例替换不改变路由结论：同一逻辑 Agent/用户由同一 conversation 承载（无 pod 亲和）
    assert first["conversation_id"], first
    assert first["conversation_id"] == second["conversation_id"]


async def test_b130_worker_process_consumes_and_persists_delivery_fact(
    gateway_stack: GatewayStack,
) -> None:
    seeded = seed_delivery_task(
        tenant_id=gateway_stack.tenant_id,
        agent_id=gateway_stack.agent_id,
        platform_user_id=gateway_stack.platform_user_id,
        intent_key=B130_INTENT,
        bot_id=gateway_stack.bot_id,
        external_user_id=gateway_stack.bound_external_user_id,
        external_conversation_id=gateway_stack.chat_id,
    )
    # Worker 进程（真实投递循环）→ 真实 Gateway → 生产 Adapter → 真实 WS 探针
    await _wait_for(
        lambda: any(B130_INTENT in text for text in _probe_texts(gateway_stack)),
        what="Worker 未在超时内把投递发到真实 WS 探针",
    )
    # 持久投递事实：真实 PG 中该 Task 落为 SENT
    status = ""
    deadline = time.monotonic() + B130_TIMEOUT_SEC
    while time.monotonic() < deadline:
        status = await _delivery_status(seeded["task_id"])
        if status == "SENT":
            break
        await asyncio.sleep(0.5)
    assert status == "SENT", f"delivery_status={status}"


async def test_b130_cleanup_leaves_no_residue(gateway_stack: GatewayStack) -> None:
    """最后执行：清理后无残留行（fixture finally 会再次幂等清理）。"""
    assert gateway_stack.tenant_id.startswith("e2e-im-")

    await purge_tenant()
    for table in (
        "task.task_execution",
        "task.delivery_route",
        "task.task_event",
        "runtime.run_record",
        "runtime.canonical_event",
        "control.bot_account",
        "control.channel_identity",
    ):
        assert await count_tenant_rows(table) == 0, table
