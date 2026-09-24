"""B-127 / S-04 / RULE-09 / RISK-02: Worker 主动投递与失败降级
（真实 Worker 进程 → Gateway HTTP → 真实 Redis → 生产 Adapter → 真实本地 WS 接收端）。

不得 Mock 的真实边界：真实 Worker 进程（投递循环，真实 PG/Redis）、真实 Gateway 进程、
生产 `WeComAdapter` → 官方 SDK → 真实 `wss://` 探针；验收类不制造 RED（Baseline）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx
from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    BOUND_EXTERNAL_USER_ID,
    CHAT_ID,
    GatewayStack,
    count_tenant_rows,
    purge_tenant,
)
from tests.e2e.seed_im_gateway import seed_delivery_task

DELIVERIES_PATH = "/internal/deliveries"
DELIVERY_TTL_SEC = 604800
WAIT_TIMEOUT_SEC = 90.0
INTENT = "b127_delivery_intent"
SEND_FAILURE_INTENT = "b127_send_failure"


async def _scalar(statement: str, params: dict[str, object]) -> object:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(statement), params)
    finally:
        await engine.dispose()


async def _execute(statement: str, params: dict[str, object]) -> None:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.begin() as connection:
            await connection.execute(text(statement), params)
    finally:
        await engine.dispose()


async def _wait_for(predicate: Any, *, what: str, timeout: float = WAIT_TIMEOUT_SEC) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await _maybe(predicate):
            return
        await asyncio.sleep(0.5)
    raise AssertionError(what)


async def _maybe(predicate: Any) -> bool:
    result = predicate()
    if asyncio.iscoroutine(result):
        result = await result
    return bool(result)


def _delivered_texts(stack: GatewayStack) -> list[str]:
    probe = stack.ws_probe
    assert probe is not None
    return [
        str(((item.frame.get("body") or {}).get("text") or {}).get("content") or "")
        for item in probe.received  # type: ignore[attr-defined]
        if item.frame.get("cmd") == "aibot_send_msg"
    ]


def _delivered_chats(stack: GatewayStack) -> list[str]:
    probe = stack.ws_probe
    assert probe is not None
    return [
        str((item.frame.get("body") or {}).get("chatid") or "")
        for item in probe.received  # type: ignore[attr-defined]
        if item.frame.get("cmd") == "aibot_send_msg"
    ]


async def _seed(stack: GatewayStack, intent: str) -> dict[str, Any]:
    return seed_delivery_task(
        tenant_id=stack.tenant_id,
        agent_id=stack.agent_id,
        platform_user_id=stack.platform_user_id,
        intent_key=intent,
        bot_id=BOT_ID,
        external_user_id=BOUND_EXTERNAL_USER_ID,
        external_conversation_id=CHAT_ID,
    )


async def _delivery_row(task_id: uuid.UUID) -> dict[str, Any]:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT delivery_status, delivery_attempts, status FROM task.task_execution "
                        "WHERE id = :id"
                    ),
                    {"id": task_id},
                )
            ).one()
        return {"delivery_status": row[0], "attempts": row[1], "task_status": row[2]}
    finally:
        await engine.dispose()


async def test_s04_worker_delivers_to_route_with_7d_dedupe(
    gateway_stack: GatewayStack,
) -> None:
    seeded = await _seed(gateway_stack, INTENT)
    # Worker 进程按 route 投递最终结果到真实 WS 接收端
    await _wait_for(
        lambda: any(INTENT in text for text in _delivered_texts(gateway_stack)),
        what="Worker 未在超时内把最终结果投到真实 WS",
    )
    assert BOT_ID in {
        probe_map.connection_bots[item.connection]  # type: ignore[attr-defined]
        for item in gateway_stack.ws_probe.received  # type: ignore[attr-defined]
        if item.frame.get("cmd") == "aibot_send_msg"
        for probe_map in (gateway_stack.ws_probe,)
    }
    assert CHAT_ID in _delivered_chats(gateway_stack)

    # 重放同一 delivery_key：200 + deduplicated，且不再次发送
    before = len(_delivered_texts(gateway_stack))
    body = {
        "task_id": str(seeded["task_id"]),
        "delivery_key": seeded["delivery_key"],
        "route": {
            "channel": "WECOM",
            "bot_id": BOT_ID,
            "external_user_id": BOUND_EXTERNAL_USER_ID,
            "external_conversation_id": CHAT_ID,
        },
        "message": {"type": "text", "text": "重放投递"},
    }
    async with httpx.AsyncClient(base_url=gateway_stack.gateway_url, timeout=30.0) as client:
        replay = await client.post(DELIVERIES_PATH, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["deduplicated"] is True
    await asyncio.sleep(1.0)
    assert len(_delivered_texts(gateway_stack)) == before, "重放不得再次发送"

    # 沿用 7d 成功键
    import redis.asyncio

    settings = SharedSettings()
    redis_url = settings.redis_url
    assert redis_url, "REDIS_URL 未配置"
    redis_client = redis.asyncio.Redis.from_url(redis_url)
    try:
        ttl = await redis_client.ttl(f"delivery:dedupe:{seeded['delivery_key']}")
    finally:
        await redis_client.aclose()
    assert DELIVERY_TTL_SEC - 120 < ttl <= DELIVERY_TTL_SEC, ttl


async def test_b127_failure_retries_then_exhausts_without_swallowing_fact(
    gateway_stack: GatewayStack,
) -> None:
    settings = SharedSettings()
    seeded = await _seed(gateway_stack, SEND_FAILURE_INTENT)
    # 直接种到"下一次失败即耗尽"：attempts = max - 1
    await _execute(
        "UPDATE task.task_execution SET delivery_attempts = :attempts WHERE id = :id",
        {"attempts": settings.delivery_max_attempts - 1, "id": seeded["task_id"]},
    )
    assert seeded["delivery_key"].startswith("task:")
    probe = gateway_stack.ws_probe
    assert probe is not None
    probe.fail_reply_bots.add(BOT_ID)  # 故障注入：SDK 发送失败
    try:
        await _wait_for(
            lambda: _is_exhausted(seeded["task_id"], settings.delivery_max_attempts),
            what="投递未在耗尽次数后置 FAILED",
        )
    finally:
        probe.clear_injections()

    row = await _delivery_row(seeded["task_id"])
    assert row["delivery_status"] == "FAILED", row
    assert int(row["attempts"] or 0) >= settings.delivery_max_attempts
    # 不吞业务事实：Task 自身终态与投递失败事件都保留
    assert row["task_status"] == "COMPLETED", row
    events = await _scalar(
        "SELECT count(*) FROM task.task_event WHERE tenant_id = :t AND event_type = 'DELIVERY_FAILED'",
        {"t": gateway_stack.tenant_id},
    )
    assert int(events or 0) >= 1


async def _is_exhausted(task_id: uuid.UUID, max_attempts: int) -> bool:
    row = await _delivery_row(task_id)
    return row["delivery_status"] == "FAILED" and int(row["attempts"] or 0) >= max_attempts


async def test_b127_retryable_failure_recovers_after_injection_cleared(
    gateway_stack: GatewayStack,
) -> None:
    seeded = await _seed(gateway_stack, INTENT)
    probe = gateway_stack.ws_probe
    assert probe is not None
    probe.fail_reply_bots.add(BOT_ID)
    try:
        await _wait_for(
            lambda: _attempts_at_least(seeded["task_id"], 1),
            what="未观察到首次投递尝试",
            timeout=30.0,
        )
        row = await _delivery_row(seeded["task_id"])
        # 可重试失败：状态为 FAILED（属 Worker 的可重试集合）且留 DELIVERY_RETRY 事件，
        # 绝不误标 SENT；恢复后由退避重试补发
        assert row["delivery_status"] == "FAILED", row
        assert int(row["attempts"] or 0) >= 1, row
        retry_events = await _scalar(
            "SELECT count(*) FROM task.task_event WHERE tenant_id = :t AND event_type = 'DELIVERY_RETRY'",
            {"t": gateway_stack.tenant_id},
        )
        assert int(retry_events or 0) >= 1, "可重试失败必须留 DELIVERY_RETRY 事件"
    finally:
        probe.clear_injections()

    # 恢复后重试成功（发送失败不误标成功，也不永久卡住）
    await _wait_for(
        lambda: _is_sent(seeded["task_id"]),
        what="清除故障注入后投递未恢复为 SENT",
    )
    assert any("b127_delivery_intent" in text for text in _delivered_texts(gateway_stack))


async def _attempts_at_least(task_id: uuid.UUID, count: int) -> bool:
    row = await _delivery_row(task_id)
    return int(row["attempts"] or 0) >= count


async def _is_sent(task_id: uuid.UUID) -> bool:
    row = await _delivery_row(task_id)
    return row["delivery_status"] == "SENT"


async def test_b127_cleanup_leaves_no_delivery_residue(gateway_stack: GatewayStack) -> None:
    await purge_tenant()
    for table in ("task.task_execution", "task.delivery_route", "task.task_event"):
        assert await count_tenant_rows(table) == 0, table
