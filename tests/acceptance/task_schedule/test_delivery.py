"""[S-03][E-05][B-143] Worker→真实 Gateway→Redis→本地渠道探针 最终投递。"""

from __future__ import annotations

import time
import uuid
from typing import Any, cast

import httpx
from muad_agent_runtime.application.task_client import WorkerTaskClient
from sqlalchemy import text

from .environment import INTERNAL_TOKEN, TENANT, LiveStack, run_async, run_db
from .helpers import submission_context

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _submit_final_only(live_stack: LiveStack) -> str:
    context, resolved = submission_context(live_stack)
    from dataclasses import replace

    from muad_contracts import DeliveryRouteInput

    context = replace(
        context,
        delivery_route=DeliveryRouteInput(
            channel="WECOM",
            bot_id=live_stack.bot_id,
            external_user_id="e2e-external-user",
            external_conversation_id="e2e-conversation",
        ),
    )

    async def submit() -> dict[str, Any]:
        client = WorkerTaskClient(live_stack.worker_url, service_token=INTERNAL_TOKEN)
        try:
            return await client.submit_task(
                context, skill=resolved["skill"], input_data={"case": "final-delivery"}
            )
        finally:
            await client.aclose()

    return str(run_async(submit)["task_id"])


def _task_row(live_stack: LiveStack, task_id: str) -> tuple[Any, ...]:
    async def query(factory: Any) -> Any:
        async with factory() as session:
            return (
                await session.execute(
                    text(
                        "SELECT status, delivery_mode, delivery_status, delivery_key, delivered_at, "
                        "delivery_attempts FROM task.task_execution WHERE id = :id"
                    ),
                    {"id": uuid.UUID(task_id)},
                )
            ).one()

    return cast(tuple[Any, ...], run_db(query))


def _wait(predicate: Any, timeout_sec: float = 90.0) -> Any:
    deadline = time.monotonic() + timeout_sec
    value = predicate()
    while not value and time.monotonic() < deadline:
        time.sleep(1.0)
        value = predicate()
    return value


def _my_deliveries(live_stack: LiveStack, http: httpx.Client) -> list[dict[str, Any]]:
    """只取**本栈 bot** 的投递 —— 探针是共享的，全局计数会数到别的套件。

    根因（2026-10-02 实测）：`claim` 不带租户谓词（`worker/claimer.py:20-27`，Worker 是无状态
    通用工作池，任务行自带 `tenant_id`），于是**别的域套件留下的可领取任务**会被本栈 Worker
    领走，并按本栈环境注入的探针 URL 投递 —— 探针里因此多出一条不属于本用例的投递，
    `assert len(deliveries) == 1` 这种**全局计数**断言随即偶发失败（实测：单独跑全绿，
    放进 `verify-e2e` 的 14 条 verifier 顺序里必红）。断言限定到本栈 bot 后，意图
    （"失败那次不计、成功恰好一条"）不变，但不再依赖别的套件的行为。
    """
    payload = http.get(f"{live_stack.channel_url}/probe/deliveries").json()
    return [item for item in payload["deliveries"] if item.get("bot_id") == live_stack.bot_id]


def test_s03_final_delivery_reaches_probe_once_and_is_deduped(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    http.post(f"{live_stack.channel_url}/probe/reset")
    task_id = _submit_final_only(live_stack)

    def delivered() -> bool:
        return bool(_task_row(live_stack, task_id)[2] == "SENT")

    assert _wait(delivered), f"投递必须成功: {_task_row(live_stack, task_id)}"
    status, delivery_mode, delivery_status, delivery_key, delivered_at, attempts = _task_row(
        live_stack, task_id
    )
    assert status == "COMPLETED"
    assert delivery_mode == "FINAL_ONLY"
    assert delivery_key == f"task:{task_id}:final"
    assert delivered_at is not None and attempts >= 1

    deliveries = _my_deliveries(live_stack, http)
    assert len(deliveries) == 1, deliveries

    # 同一 delivery_key 重放：真实 Gateway + 真实 Redis 去重，不重复触达渠道
    replay = http.post(
        f"{live_stack.gateway_url}/internal/deliveries",
        headers=live_stack.service_headers(),
        json={
            # 交付契约要求 `tenant_id`（交付审计的幂等键要用它）
            "tenant_id": TENANT,
            "task_id": task_id,
            "delivery_key": delivery_key,
            "route": {
                "channel": "WECOM",
                "bot_id": live_stack.bot_id,
                "external_user_id": "e2e-external-user",
                "external_conversation_id": "e2e-conversation",
            },
            "message": {"type": "text", "text": "replay"},
            "artifact_ids": [],
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["duplicate"] is True
    assert http.get(f"{live_stack.channel_url}/probe/deliveries").json()["deliveries"] == deliveries


def test_e05_channel_failure_retries_without_fake_sent(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    http.post(f"{live_stack.channel_url}/probe/reset")
    http.post(f"{live_stack.channel_url}/probe/fail-next", json={"count": 1})
    task_id = _submit_final_only(live_stack)

    def sent() -> bool:
        return bool(_task_row(live_stack, task_id)[2] == "SENT")

    assert _wait(sent), "首次渠道失败后必须重试成功（不伪造 SENT）"
    status, _, delivery_status, _, _, attempts = _task_row(live_stack, task_id)
    assert delivery_status == "SENT"
    assert attempts >= 2, "失败必须计入尝试并重试"
    deliveries = _my_deliveries(live_stack, http)
    assert len(deliveries) == 1, deliveries
    assert status == "COMPLETED"
