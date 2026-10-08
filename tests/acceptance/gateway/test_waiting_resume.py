"""S-06/E-13/E-17（E2E）：Gateway 等待态、SSE 断流重连与主动投递。

不得 Mock 的真实边界：真实 Gateway 进程（官方 WeCom SDK → 真实 WS 探针）→ 真实 Runtime
HTTP/SSE（创建 + `GET /v1/runs/{id}/events?after_seq=` 重连）→ 真实 Worker 执行 ASYNC JOIN
Skill → 真实 PostgreSQL/Redis；gate 文件控制任务完成时机，模型为真实 HTTP join 探针。
E-13 经真实 TCP 中继强制断开在飞 SSE（不杀进程），覆盖“等待中断流→按已确认 seq 重连”。
"""

from __future__ import annotations

import asyncio
import re
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
STOP_CANCELLED_TEXT = "当前任务已停止"
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
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return result
        await asyncio.sleep(0.25)
    raise AssertionError(what)


async def _hold(predicate: Any, *, seconds: float, what: str) -> None:
    """负向观察窗：在 `seconds` 内条件必须一直成立（用于“排队期间没有新 Run”一类断言）。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        assert result, what
        await asyncio.sleep(0.2)


def _frames(stack: WaitingGatewayStack, reply_id: str | None = None) -> list[dict[str, Any]]:
    frames = [
        item.frame
        for item in stack.ws_probe.received
        if item.frame.get("cmd") in (STREAM_CMD, SEND_CMD)
    ]
    if reply_id is not None:
        frames = [
            frame
            for frame in frames
            if str((frame.get("headers") or {}).get("req_id") or "") == reply_id
        ]
    return frames


def _send_frames(stack: WaitingGatewayStack) -> list[dict[str, Any]]:
    return [item.frame for item in stack.ws_probe.received if item.frame.get("cmd") == SEND_CMD]


def _stream_finished(frame: dict[str, Any]) -> bool:
    body = frame.get("body") or {}
    return bool((body.get("stream") or {}).get("finish"))


async def _push(stack: WaitingGatewayStack, text: str, *, message_id: str | None = None) -> str:
    resolved = message_id or f"gateway-{uuid.uuid4().hex[:10]}"
    await stack.ws_probe.push_message(
        bot_id="e2e-bot",
        message_id=resolved,
        external_user_id=stack.external_user_id,
        text=text,
        reply_id=f"req-{resolved}",
        chat_id=stack.chat_id,
    )
    return resolved


def _configure_join(stack: WaitingGatewayStack, root: Path, *, gate: Path | None = None) -> Path:
    gate_path = gate or (root / f"gate-{uuid.uuid4().hex[:8]}")
    response = httpx.post(
        stack.live.llm_url + "/configure",
        json={"skill_key": stack.live.skill_key, "gate": str(gate_path), "calls": 1},
        timeout=10,
    )
    response.raise_for_status()
    return gate_path


async def _run_ids() -> set[str]:
    rows = await _rows(
        "SELECT id::text FROM runtime.run_record WHERE tenant_id = :t", {"t": TENANT}
    )
    return {str(row[0]) for row in rows}


async def _run_status(run_id: str) -> str:
    value = await _scalar(
        "SELECT status FROM runtime.run_record WHERE id = :r", {"r": uuid.UUID(run_id)}
    )
    return str(value or "")


async def _status_is(run_id: str, status: str) -> bool:
    """`_wait_for` 谓词：async 调用必须整体 await，不能写成 `lambda: await_fn() == x`（恒 False）。"""
    return await _run_status(run_id) == status


async def _only_waiting_run(conversation_id: str, run_id: str) -> bool:
    return await _runs_in_conversation(conversation_id) == [(run_id, "WAITING_TOOL")]


async def _fresh_runs(conversation_id: str, before: set[str]) -> list[tuple[str, str]]:
    """本用例消息之后新建的 Run（会话可能承载同模块先前用例的历史 Run）。"""
    rows = await _runs_in_conversation(conversation_id)
    return [(rid, status) for rid, status in rows if rid not in before]


async def _only_fresh_waiting_run(conversation_id: str, run_id: str, before: set[str]) -> bool:
    return await _fresh_runs(conversation_id, before) == [(run_id, "WAITING_TOOL")]


async def _run_input(run_id: str) -> str:
    value = await _scalar(
        "SELECT input_text FROM runtime.run_record WHERE id = :r", {"r": uuid.UUID(run_id)}
    )
    return str(value or "")


async def _conversation_of(run_id: str) -> str:
    value = await _scalar(
        "SELECT conversation_id::text FROM runtime.run_record WHERE id = :r",
        {"r": uuid.UUID(run_id)},
    )
    return str(value or "")


async def _runs_in_conversation(conversation_id: str) -> list[tuple[str, str]]:
    rows = await _rows(
        "SELECT id::text, status FROM runtime.run_record "
        "WHERE tenant_id = :t AND conversation_id = :c ORDER BY create_time",
        {"t": TENANT, "c": uuid.UUID(conversation_id)},
    )
    return [(str(row[0]), str(row[1])) for row in rows]


async def _new_run(before: set[str], *, status: str | None = None) -> str | None:
    fresh = sorted(await _run_ids() - before)
    for run_id in fresh:
        current = await _run_status(run_id)
        if status is None or current == status:
            return run_id
    return None


async def _join_task(run_id: str) -> dict[str, Any]:
    row = (
        await _rows(
            "SELECT o.task_id::text, t.delivery_mode, t.status FROM runtime.tool_operation o "
            "JOIN task.task_execution t ON t.id = o.task_id WHERE o.run_id = :r",
            {"r": uuid.UUID(run_id)},
        )
    )[0]
    return {"task_id": str(row[0]), "delivery_mode": str(row[1]), "status": str(row[2])}


async def _continuation(run_id: str) -> dict[str, Any]:
    rows = await _rows(
        "SELECT turns, tool_calls, input_tokens, output_tokens, wait_generation "
        "FROM runtime.run_continuation WHERE run_id = :r",
        {"r": uuid.UUID(run_id)},
    )
    assert rows, "等待 Run 必须有 continuation 检查点"
    row = rows[0]
    return {
        "turns": int(row[0] or 0),
        "tool_calls": int(row[1] or 0),
        "input_tokens": int(row[2] or 0),
        "output_tokens": int(row[3] or 0),
        "wait_generation": int(row[4] or 0),
    }


async def _canonical_count(run_id: str, stream_type: str) -> int:
    value = await _scalar(
        "SELECT count(*) FROM runtime.canonical_event WHERE run_id = :r AND stream_type = :s",
        {"r": uuid.UUID(run_id), "s": stream_type},
    )
    return int(value or 0)


async def _wait_new_waiting_run(before: set[str]) -> str:
    run_id = await _wait_for(
        lambda: _new_run(before, status="WAITING_TOOL"),
        what="推送的 JOIN Run 未进入 WAITING_TOOL",
    )
    assert isinstance(run_id, str)
    return run_id


async def test_s06_join_waiting_holds_silence_and_replies_on_original_message(
    gateway_stack: WaitingGatewayStack, tmp_path: Path
) -> None:
    stack = gateway_stack
    gate = _configure_join(stack, tmp_path)
    before = await _run_ids()
    message_id = await _push(stack, "S-06 检查任务")
    reply_id = f"req-{message_id}"
    run_id = await _wait_new_waiting_run(before)

    # 等待阶段：状态帧可以有，**完成帧不可以有**（run.waiting 是非终态等待）。
    await _hold(
        lambda: not any(_stream_finished(frame) for frame in _frames(stack, reply_id)),
        seconds=1.5,
        what="等待阶段出现了完成帧",
    )
    assert await _run_status(run_id) == "WAITING_TOOL"

    gate.touch()
    await _wait_for(
        lambda: any(_stream_finished(frame) for frame in _frames(stack, reply_id)),
        what="接续后原消息未收到最终文本",
        timeout=REPLY_TIMEOUT_SEC,
    )
    finished = [frame for frame in _frames(stack, reply_id) if _stream_finished(frame)]
    assert len(finished) == 1, "终态只能输出一次"
    assert "actual_job_result" in frame_text(finished[0]), frame_text(finished[0])
    assert await _run_status(run_id) == "COMPLETED"

    # 无额外 Worker FINAL_ONLY 通知：JOIN 任务不独立投递，结果只回原消息的回复流。
    task = await _join_task(run_id)
    assert task["delivery_mode"] == "NONE", task
    assert not any(
        "actual_job_result" in frame_text(frame) for frame in _send_frames(stack)
    ), "JOIN 结果不得借主动通知再发一次"

    # /stop 不被排队阻塞：同一路由仍有等待中的 Run 时，命令仍即时生效并取消等待。
    _configure_join(stack, tmp_path)
    before_stop = await _run_ids()
    await _push(stack, "S-06 第二条")
    waiting_id = await _wait_new_waiting_run(before_stop)
    stop_message = await _push(stack, "/stop")
    await _wait_for(
        lambda: any(
            STOP_CANCELLED_TEXT in frame_text(frame)
            for frame in _frames(stack, f"req-{stop_message}")
        ),
        what="/stop 回执未在等待 Run 存续期间到达（命令被排队阻塞）",
        timeout=REPLY_TIMEOUT_SEC,
    )
    await _wait_for(
        lambda: _status_is(waiting_id, "CANCELLED"),
        what="等待中的 Run 未因 /stop 取消",
    )


async def test_e13_sse_drop_during_waiting_replays_without_new_task_or_budget_reset(
    gateway_stack: WaitingGatewayStack, tmp_path: Path
) -> None:
    stack = gateway_stack
    gate = _configure_join(stack, tmp_path)
    before = await _run_ids()
    message_id = await _push(stack, "E-13 断流任务")
    reply_id = f"req-{message_id}"
    run_id = await _wait_new_waiting_run(before)
    conversation = await _conversation_of(run_id)
    during = await _continuation(run_id)

    # 等待期间断开 SSE：任务必须继续执行（断流 ≠ 取消）。
    stack.runtime_proxy.drop_active()
    assert await _run_status(run_id) == "WAITING_TOOL", "断流不得改变 Run 状态"
    await _wait_for(
        lambda: "run_stream_reconnecting" in stack.gateway_log.read_text(errors="replace"),
        what="Gateway 未按已确认 seq 重连现有 Run",
    )
    log_text = stack.gateway_log.read_text(errors="replace")
    match = re.search(rf"run_stream_reconnecting run_id={run_id} after_seq=(\d+)", log_text)
    assert match is not None, "重连日志缺少 run_id/after_seq"
    assert int(match.group(1)) >= 1, "重连必须从已确认的 canonical seq 之后回放"

    gate.touch()
    await _wait_for(
        lambda: any(_stream_finished(frame) for frame in _frames(stack, reply_id)),
        what="断流重连后原消息未收到最终文本",
        timeout=REPLY_TIMEOUT_SEC,
    )
    finished = [frame for frame in _frames(stack, reply_id) if _stream_finished(frame)]
    assert len(finished) == 1, "终态恢复后也只能输出一次"
    assert "actual_job_result" in frame_text(finished[0])
    assert await _run_status(run_id) == "COMPLETED"
    assert await _canonical_count(run_id, "run.completed") == 1, "终态 canonical 只能有一条"

    # 无新 Run/新 Task/预算重置：本用例只新增一条 Run，一条 JOIN Task，计数只前进不回退。
    assert await _fresh_runs(conversation, before) == [(run_id, "COMPLETED")]
    task = await _join_task(run_id)
    assert task["status"] == "COMPLETED"
    task_count = await _scalar(
        "SELECT count(*) FROM task.task_execution WHERE tenant_id = :t AND source_run_id = :r",
        {"t": TENANT, "r": uuid.UUID(run_id)},
    )
    assert int(task_count or 0) == 1, "重连不得新建 Task"
    after = await _continuation(run_id)
    assert after["wait_generation"] == during["wait_generation"] == 1, (during, after)
    assert after["turns"] > during["turns"], f"模型预算被重置：{during} -> {after}"
    assert after["input_tokens"] >= during["input_tokens"]


async def test_e17_waiting_tool_rejects_resume_queues_message_and_recovers_after_stop(
    gateway_stack: WaitingGatewayStack, tmp_path: Path
) -> None:
    stack = gateway_stack
    gate = _configure_join(stack, tmp_path)
    before = await _run_ids()
    await _push(stack, "E-17 原始输入")
    run_id = await _wait_new_waiting_run(before)
    conversation = await _conversation_of(run_id)

    # 显式人类 resume 在 WAITING_TOOL 上被 RUN_BUSY 拒绝，且不替换等待输入。
    response = httpx.post(
        f"{stack.live.runtime_url}/v1/runs/{run_id}/resume",
        headers={"X-Tenant-Id": TENANT, "Idempotency-Key": f"resume-rejected-{uuid.uuid4().hex}"},
        json={"input": {"type": "text", "text": "替换等待输入"}},
        timeout=30,
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "RUN_BUSY", response.text
    assert await _run_status(run_id) == "WAITING_TOOL"
    assert await _run_input(run_id) == "E-17 原始输入"

    # 普通新消息沿既有路由队列等待：等待输入不被替换，也不产生第二个 Run。
    await _push(stack, "E-17 队列中的新消息")
    await _hold(
        lambda: _only_fresh_waiting_run(conversation, run_id, before),
        seconds=2.0,
        what="等待期间第二消息绕过了队列（出现了第二个 Run 或等待被替换）",
    )
    assert await _run_input(run_id) == "E-17 原始输入"

    gate.touch()
    await _wait_for(
        lambda: _status_is(run_id, "COMPLETED"),
        what="原等待 Run 未完成",
        timeout=REPLY_TIMEOUT_SEC,
    )
    second_run = await _wait_for(
        lambda: _second_run(conversation, run_id, before),
        what="排队消息在等待结束后未创建自己的 Run",
    )
    assert isinstance(second_run, str)
    assert await _run_input(second_run) == "E-17 队列中的新消息"
    await _wait_for(
        lambda: _status_is(second_run, "COMPLETED"),
        what="排队消息的 Run 未完成",
        timeout=REPLY_TIMEOUT_SEC,
    )

    # /stop 可取消等待，之后新 Run 仍可正常创建。
    _configure_join(stack, tmp_path)
    before_stop = await _run_ids()
    await _push(stack, "E-17 待取消的等待")
    cancel_id = await _wait_new_waiting_run(before_stop)
    await _push(stack, "/stop")
    await _wait_for(
        lambda: _status_is(cancel_id, "CANCELLED"),
        what="/stop 未取消等待中的 Run",
    )
    recovery_gate = _configure_join(stack, tmp_path)
    recovery_gate.touch()
    before_recovery = await _run_ids()
    third_message = await _push(stack, "E-17 取消后的新消息")
    recovery_run = await _wait_for(
        lambda: _new_run(before_recovery),
        what="取消后新消息未能创建新 Run",
    )
    assert await _wait_for(
        lambda: _status_is(str(recovery_run), "COMPLETED"),
        what="取消后的新 Run 未完成",
        timeout=REPLY_TIMEOUT_SEC,
    )
    assert await _run_input(str(recovery_run)) == "E-17 取消后的新消息"
    await _wait_for(
        lambda: any(
            _stream_finished(frame) for frame in _frames(stack, f"req-{third_message}")
        ),
        what="取消后的新 Run 未在原消息回复流上收尾",
        timeout=REPLY_TIMEOUT_SEC,
    )


async def _second_run(conversation: str, first_run_id: str, before: set[str]) -> str | None:
    rows = await _runs_in_conversation(conversation)
    fresh = [
        run_id
        for run_id, _status in rows
        if run_id != first_run_id and run_id not in before
    ]
    return fresh[0] if fresh else None
