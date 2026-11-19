"""S-06（新语义）：Gateway 入站触发 ASYNC Skill，Run 立即完成，结果后台投递。

真实边界：真实 Gateway 进程（官方 WeCom SDK → 真实 WS 探针）→ 真实 Runtime HTTP/SSE
（创建 + 事件流）→ 真实 Worker 执行 ASYNC DETACH Skill → 真实 PostgreSQL/Redis。gate
文件控制后台任务完成时机，模型为真实 HTTP 探针。

TASK-006 起 Run 侧 ASYNC 恒为 DETACH：Run 不再进入 WAITING_TOOL，也不再占用会话等待；
后台任务独立执行，完成后由 Worker 的持久投递意图把结果送到原入口。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.gateway.conftest import WaitingGatewayStack
from tests.acceptance.task_schedule.environment import TENANT
from tests.e2e.wecom_probe_app import frame_text

pytestmark = pytest.mark.e2e

WAIT_TIMEOUT_SEC = 180.0
REPLY_TIMEOUT_SEC = 120.0
STREAM_CMD = "aibot_respond_msg"
SEND_CMD = "aibot_send_msg"


async def _scalar(statement: str, params: dict[str, object]) -> object:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(statement), params)
    finally:
        await engine.dispose()


async def _rows(statement: str, params: dict[str, object]) -> list[tuple[Any, ...]]:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            return list((await connection.execute(text(statement), params)).all())
    finally:
        await engine.dispose()


async def _wait_for(predicate: Any, *, what: str, timeout: float = WAIT_TIMEOUT_SEC) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if hasattr(result, "__await__"):
            result = await result
        if result:
            return result
        await asyncio.sleep(0.25)
    raise AssertionError(what)


async def _run_ids() -> set[str]:
    rows = await _rows(
        "SELECT id::text FROM runtime.run_record WHERE tenant_id = :t", {"t": TENANT}
    )
    return {str(row[0]) for row in rows}


async def _new_run(before: set[str]) -> str | None:
    fresh = sorted(await _run_ids() - before)
    return fresh[-1] if fresh else None


async def _run_status(run_id: str) -> str:
    value = await _scalar(
        "SELECT status FROM runtime.run_record WHERE id = :r", {"r": uuid.UUID(run_id)}
    )
    return str(value or "")


async def _status_is(run_id: str, status: str) -> bool:
    return await _run_status(run_id) == status


async def _task_of_run(run_id: str) -> dict[str, str]:
    rows = await _rows(
        "SELECT t.id::text, t.completion_mode, t.status "
        "FROM runtime.tool_operation o JOIN task.task_execution t ON t.id = o.task_id "
        "WHERE o.run_id = :r",
        {"r": uuid.UUID(run_id)},
    )
    assert len(rows) == 1, rows
    return {
        "task_id": str(rows[0][0]),
        "completion_mode": str(rows[0][1]),
        "status": str(rows[0][2]),
    }


async def _task_status(task_id: str) -> str:
    value = await _scalar(
        "SELECT status FROM task.task_execution WHERE id = :id", {"id": uuid.UUID(task_id)}
    )
    return str(value or "")


async def _task_status_is(task_id: str, status: str) -> bool:
    return await _task_status(task_id) == status


def _frames(stack: WaitingGatewayStack, reply_id: str) -> list[dict[str, Any]]:
    return [
        item.frame
        for item in stack.ws_probe.received
        if item.frame.get("cmd") in (STREAM_CMD, SEND_CMD)
        and str((item.frame.get("headers") or {}).get("req_id") or "") == reply_id
    ]


def _send_frames(stack: WaitingGatewayStack) -> list[dict[str, Any]]:
    return [item.frame for item in stack.ws_probe.received if item.frame.get("cmd") == SEND_CMD]


async def _push(stack: WaitingGatewayStack, text: str) -> tuple[str, str]:
    message_id = f"gateway-{uuid.uuid4().hex[:10]}"
    await stack.ws_probe.push_message(
        bot_id="e2e-bot",
        message_id=message_id,
        external_user_id=stack.external_user_id,
        text=text,
        reply_id=f"req-{message_id}",
        chat_id=stack.chat_id,
    )
    return message_id, f"req-{message_id}"


def _configure_gated_skill(stack: WaitingGatewayStack, root: Path) -> Path:
    gate_path = root / f"gate-{uuid.uuid4().hex[:8]}"
    response = httpx.post(
        stack.live.llm_url + "/configure",
        json={"skill_key": stack.live.skill_key, "gate": str(gate_path), "calls": 1},
        timeout=10,
    )
    response.raise_for_status()
    return gate_path


async def test_s06_async_skill_completes_run_and_delivers_result_in_background(
    gateway_stack: WaitingGatewayStack, tmp_path: Path
) -> None:
    stack = gateway_stack
    gate = _configure_gated_skill(stack, tmp_path)
    before = await _run_ids()
    _, reply_id = await _push(stack, "S-06 后台任务投递")

    run_id = await _wait_for(lambda: _new_run(before), what="入站消息未建 Run")
    assert isinstance(run_id, str)

    await _wait_for(
        lambda: _status_is(run_id, "COMPLETED"),
        what="ASYNC DETACH 的 Run 未在当前回合完成",
        timeout=REPLY_TIMEOUT_SEC,
    )
    assert await _run_status(run_id) != "WAITING_TOOL"
    assert (
        await _scalar(
            "SELECT count(*) FROM runtime.canonical_event "
            "WHERE run_id = :r AND event_type = 'RUN_WAITING_TOOL'",
            {"r": uuid.UUID(run_id)},
        )
        == 0
    ), "Run 侧 ASYNC 不得产生 WAITING_TOOL"
    await _wait_for(
        lambda: _frames(stack, reply_id),
        what="原消息没有任何回复帧",
        timeout=REPLY_TIMEOUT_SEC,
    )

    task = await _task_of_run(run_id)
    assert task["completion_mode"] == "DETACH", task

    gate.touch()
    await _wait_for(
        lambda: _task_status_is(task["task_id"], "COMPLETED"),
        what="后台任务未在放行后完成",
        timeout=REPLY_TIMEOUT_SEC,
    )
    await _wait_for(
        lambda: any(
            "actual_job_result" in frame_text(frame) for frame in _send_frames(stack)
        ),
        what="后台任务结果未通过原入口主动投递",
        timeout=REPLY_TIMEOUT_SEC,
    )
    delivered = [
        frame_text(frame)
        for frame in _send_frames(stack)
        if "actual_job_result" in frame_text(frame)
    ]
    assert len(delivered) == 1, f"同一任务只允许送达一次：{delivered}"
