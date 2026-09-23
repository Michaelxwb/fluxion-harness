"""[S-03][E-05][B-143] Worker→真实 Gateway→Redis→本地渠道探针 最终投递。"""

from __future__ import annotations

from typing import Any, cast

import time
import uuid

import httpx
from sqlalchemy import text

from .environment import INTERNAL_TOKEN, LiveStack, run_async, run_db
from .helpers import load_resolved, submission_context
from muad_agent_runtime.application.task_client import WorkerTaskClient

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

    deliveries = http.get(f"{live_stack.channel_url}/probe/deliveries").json()["deliveries"]
    assert len(deliveries) == 1, deliveries
    assert deliveries[0]["bot_id"] == live_stack.bot_id

    # 同一 delivery_key 重放：真实 Gateway + 真实 Redis 去重，不重复触达渠道
    replay = http.post(
        f"{live_stack.gateway_url}/internal/deliveries",
        json={
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
    deliveries = http.get(f"{live_stack.channel_url}/probe/deliveries").json()["deliveries"]
    assert len(deliveries) == 1
    assert status == "COMPLETED"
