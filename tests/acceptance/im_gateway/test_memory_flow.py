"""记忆候选确认端到端（真实 Gateway → Runtime → PostgreSQL → LLM 探针）。

不得 Mock 的真实边界：真实企微 WS 探针 → 真实 Gateway → 真实 Runtime HTTP/SSE →
真实 PostgreSQL（`runtime.user_memory` / `runtime.user_memory_history` /
`runtime.run_interrupt` / `runtime.tool_call_audit`）→ 真实 LLM 探针。

0020 后的新契约（2026-10-15 随 TASK-031 改写，替换已删除的 `source_type` 断言）：
- 自然语言 remember 只产生**待确认候选**（工具回执 `saved=false` + `confirmation_required=true`），
  事实在真实用户确认（`确认 <proposal_id>`）后以 `author=USER` 落库并写版本历史；
- 读侧按 `author`（USER/ADMIN/AGENT）与派生 `status` 表达来源与状态，不再有 `source_type` 列。

E2E 场景在**编码阶段只登记不执行**（manifest `kind=e2e`）：本文件由需求级
`cf_acceptance_runner.py --manifest … --include-e2e` 统一执行。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

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
    restart_process,
    wait_for_memory_background_idle,
)
from tests.e2e.wecom_probe_app import frame_text

WAIT_TIMEOUT_SEC = 150.0
REPLY_TIMEOUT_SEC = 120.0
USER_TEXT = "记住：以后都用中文回答我"
MEMORY_KEY = "reply.language"
MEMORY_VALUE = "中文"
FINAL_TEXT = "好的，已记下：以后都用中文回答你。"
#: 原话证据要求整条消息（`evidence.verify_quote`：整句相等才算来源，不做词法猜测）。
QUOTE = USER_TEXT
REMEMBER_ARGUMENTS = json.dumps(
    {
        "memory_key": MEMORY_KEY,
        "value": MEMORY_VALUE,
        "category": "PREFERENCE",
        "source_quote": QUOTE,
    },
    ensure_ascii=False,
)
#: 后台提炼探针模板：本文件只关心前台确认链路，让 job 以空批次收尾，不产生额外候选。
EMPTY_CONSOLIDATION = json.dumps({"candidates": [], "task_completed": False})
NEW_CONVERSATION_TEXT = "已创建新会话"


async def _fetch(statement: str, params: dict[str, object]) -> list[dict[str, Any]]:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            rows = (await connection.execute(text(statement), params)).mappings().all()
            return [dict(row) for row in rows]
    finally:
        await engine.dispose()


async def _wait_for(predicate: Callable[[], Any], *, what: str, timeout: float = WAIT_TIMEOUT_SEC) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.3)
    raise AssertionError(what)


def _replies(stack: GatewayStack) -> list[str]:
    probe = stack.ws_probe
    assert probe is not None
    texts: list[str] = []
    for item in probe.received:  # type: ignore[attr-defined]
        if item.frame.get("cmd") in ("aibot_respond_msg", "aibot_send_msg"):
            texts.append(frame_text(item.frame))
    return texts


async def _push(stack: GatewayStack, *, text: str) -> str:
    probe = stack.ws_probe
    assert probe is not None
    message_id = f"memory-{uuid.uuid4().hex[:8]}"
    await probe.push_message(  # type: ignore[attr-defined]
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id=BOUND_EXTERNAL_USER_ID,
        text=text,
        reply_id=f"req-{message_id}",
        chat_id=CHAT_ID,
    )
    return message_id


async def _memory_rows(stack: GatewayStack) -> list[dict[str, Any]]:
    return await _fetch(
        "SELECT memory_key, author, user_id, category, content_json, source_quote, version, enabled "
        "FROM runtime.user_memory WHERE tenant_id = :t ORDER BY update_time",
        {"t": stack.tenant_id},
    )


async def _history_rows(stack: GatewayStack) -> list[dict[str, Any]]:
    return await _fetch(
        "SELECT version, change_reason, actor_type, actor_id FROM runtime.user_memory_history"
        " WHERE tenant_id = :t ORDER BY version DESC",
        {"t": stack.tenant_id},
    )


async def _pending_proposal(stack: GatewayStack) -> dict[str, Any] | None:
    rows = await _fetch(
        "SELECT id::text AS id, run_id::text AS run_id, prompt_text, proposal_json"
        " FROM runtime.run_interrupt"
        " WHERE tenant_id = :t AND interrupt_type = 'MEMORY_CONFIRMATION' AND status = 'WAITING'",
        {"t": stack.tenant_id},
    )
    return rows[0] if len(rows) == 1 else None


async def _require_pending_proposal(stack: GatewayStack) -> dict[str, Any]:
    proposal = await _pending_proposal(stack)
    assert proposal is not None, "期望恰好一条待确认候选"
    return proposal


async def _history_versions(stack: GatewayStack) -> list[int]:
    return [item["version"] for item in await _history_rows(stack)]


async def _history_versions_are(stack: GatewayStack, expected: list[int]) -> bool:
    return await _history_versions(stack) == expected


async def _run_is_terminal(stack: GatewayStack, run_id: str) -> bool:
    rows = await _fetch(
        "SELECT status FROM runtime.run_record WHERE tenant_id = :t AND id = :run_id",
        {"t": stack.tenant_id, "run_id": uuid.UUID(run_id)},
    )
    return bool(rows) and rows[0]["status"] in ("COMPLETED", "FAILED", "CANCELLED")


async def _proposal_receipts(stack: GatewayStack, proposal_id: str) -> list[str]:
    return [item for item in await _probe_tool_messages(stack) if proposal_id in item]


async def _confirmation_diagnostics(stack: GatewayStack) -> str:
    """确认未生效时的有界现场：最近 Run 状态 / 中断状态 / 出站文本（失败信息用）。"""
    runs = await _fetch(
        "SELECT id::text AS id, status, conversation_id::text AS conversation_id, left(input_text, 40)"
        " AS input_text FROM runtime.run_record WHERE tenant_id = :t ORDER BY create_time DESC LIMIT 5",
        {"t": stack.tenant_id},
    )
    interrupts = await _fetch(
        "SELECT id::text AS id, status, run_id::text AS run_id FROM runtime.run_interrupt"
        " WHERE tenant_id = :t ORDER BY create_time DESC LIMIT 5",
        {"t": stack.tenant_id},
    )
    replies = "\n".join(_replies(stack))[-500:]
    return f"runs={runs}; interrupts={interrupts}; replies_tail={replies!r}"


async def _remember_audits(stack: GatewayStack) -> list[dict[str, Any]]:
    return await _fetch(
        "SELECT run_id, tool_name, status, tool_call_id, args_preview_json, artifact_id "
        "FROM runtime.tool_call_audit WHERE tenant_id = :t AND tool_name = 'remember' "
        "ORDER BY create_time",
        {"t": stack.tenant_id},
    )


async def _probe_tool_messages(stack: GatewayStack) -> list[str]:
    """模型实际收到的 tool 消息内容（探针记录的真实请求体）。"""
    response = httpx.get(f"{stack.llm_url}/requests", timeout=10.0)
    response.raise_for_status()
    bodies = response.json().get("requests") or []
    texts: list[str] = []
    for body in bodies:
        for message in body.get("messages") or []:
            if isinstance(message, dict) and message.get("role") == "tool":
                texts.append(str(message.get("content") or ""))
    return texts


@pytest.fixture(scope="module")
def memory_probe(gateway_stack: GatewayStack) -> GatewayStack:
    """把 LLM 探针切成「首轮调用 `remember` → 收到工具结果后给最终文本」。

    探针进程按 env 决定行为，故改 env 后重启它；其余用例不受影响（模块级独立栈）。
    `OPENAI_PROBE_TOOL_ONCE=1`：确认恢复轮不再重复提议（否则整段会话第二次要工具）。
    """
    probe = gateway_stack.processes["llm-probe"]
    probe.env["OPENAI_PROBE_TOOL_NAME"] = "remember"
    probe.env["OPENAI_PROBE_TOOL_ARGUMENTS"] = REMEMBER_ARGUMENTS
    probe.env["OPENAI_PROBE_FINAL_TEXT"] = FINAL_TEXT
    probe.env["OPENAI_PROBE_TOOL_ONCE"] = "1"
    probe.env["OPENAI_PROBE_CONSOLIDATION_TEXT"] = EMPTY_CONSOLIDATION
    restart_process(probe)
    return gateway_stack


@pytest.fixture(scope="module", autouse=True)
async def _cleanup_memory(gateway_stack: GatewayStack) -> AsyncIterator[None]:
    """补 `runtime.user_memory` 的清理：0020 表带 FK（history→memory），必须引用方先行。"""
    yield
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.begin() as connection:
            for statement in (
                "DELETE FROM runtime.memory_consolidation_job WHERE tenant_id = :t",
                "DELETE FROM runtime.memory_command_receipt WHERE tenant_id = :t",
                "DELETE FROM runtime.memory_scope WHERE tenant_id = :t",
                "DELETE FROM runtime.user_memory_history WHERE tenant_id = :t",
                "DELETE FROM runtime.user_memory WHERE tenant_id = :t",
            ):
                await connection.execute(text(statement), {"t": gateway_stack.tenant_id})
    finally:
        await engine.dispose()


async def _wait_for_gateway_ws(stack: GatewayStack) -> None:
    """等真实 Gateway 在 WS 探针上完成订阅 —— 未连上时推送会直接断言失败。

    探针按 bot **定向**投递（`connection_bots[index] == bot_id`），所以等待条件必须落在
    **目标 bot** 的连接上，只判「有任意连接」在共享栈上会假就绪。先例：既有
    `test_binding.py::_wait_for_gateway_ws`（那里判的是订阅帧 + 任意连接，因为其断言不依赖 bot 定向）。
    """
    probe = stack.ws_probe
    assert probe is not None, "WS 探针未接入栈"
    await _wait_for(
        lambda: BOT_ID in getattr(probe, "connection_bots", {}).values(),
        what=f"Gateway 未在超时内连上真实 WS 探针（bot {BOT_ID}）",
        timeout=REPLY_TIMEOUT_SEC,
    )


async def _new_conversation(stack: GatewayStack) -> None:
    """/new 是真实命令：等真实出站文本（命令不产生 Run）。

    换会话**前**先等记忆后台静止：上一个 Run 的 consolidation 收尾会向旧会话追加
    `MEMORY_CONSOLIDATED` 并 bump `conversation.update_time`，若赶在 `/new` 之后落地，
    新会话立刻被旧会话顶掉 —— 下一次推送落回旧会话，探针 `tool_once` 看到历史工具结果
    便不再调 `remember`（2026-10-15 全量顺序下实测的 S-05 超时）。
    """
    await wait_for_memory_background_idle(stack)
    replies_before = len(_replies(stack))
    await _push(stack, text="/new")
    await _wait_for(
        lambda: NEW_CONVERSATION_TEXT in "\n".join(_replies(stack)[replies_before:]),
        what="/new 未在超时内创建真实会话",
        timeout=REPLY_TIMEOUT_SEC,
    )


async def test_s01_gateway_message_writes_user_memory(memory_probe: GatewayStack) -> None:
    """[S-01][E2E] 用户说"记住：以后都用中文回答我" → 真实候选展示 → 用户确认后落一行本人记忆。

    新契约：未确认前不产生事实；确认后 `author=USER`、版本历史 actor=USER（不再有 source_type）。
    """
    stack = memory_probe
    await _wait_for_gateway_ws(stack)

    await _push(stack, text=USER_TEXT)
    await _wait_for(
        lambda: _pending_proposal(stack),
        what="真实 Run 未停在待确认的记忆候选",
        timeout=REPLY_TIMEOUT_SEC,
    )
    proposal = await _require_pending_proposal(stack)
    candidate = proposal["proposal_json"]["candidate"]
    assert candidate["memory_key"] == MEMORY_KEY
    assert candidate["source_quote"] == QUOTE
    assert await _memory_rows(stack) == [], "未经真实确认不得产生事实"

    # 用户真实确认（新的一条真实入站消息，幂等键 = message_id）
    await _push(stack, text=f"确认 {proposal['id']}")
    await _wait_for(lambda: _memory_rows(stack), what="记忆未在真实确认后落库")

    rows = await _memory_rows(stack)
    assert len(rows) == 1
    row = rows[0]
    assert row["memory_key"] == MEMORY_KEY
    assert row["author"] == "USER"
    assert row["category"] == "PREFERENCE"
    assert row["enabled"] is True
    assert row["content_json"] == {"value": MEMORY_VALUE}
    assert row["source_quote"] == QUOTE
    # 归属当前对话用户（不是别的用户、也不是工具入参能指定的人）
    assert str(row["user_id"]) == str(stack.platform_user_id)

    history = await _history_rows(stack)
    assert [(item["version"], item["change_reason"]) for item in history] == [(1, "CREATE")]
    assert history[0]["actor_type"] == "USER"
    assert str(history[0]["actor_id"]) == str(stack.platform_user_id)

    audits = await _remember_audits(stack)
    assert len(audits) == 1
    assert audits[0]["status"] == "OK"
    assert audits[0]["tool_call_id"]  # 模型给的调用 id，不是工具名
    assert audits[0]["run_id"] is not None
    assert audits[0]["args_preview_json"]["memory_key"] == MEMORY_KEY
    assert audits[0]["args_preview_json"]["category"] == "PREFERENCE"

    # 确认恢复轮跑完：Run 到终态后本会话不再追加事件 —— 下一个用例 `/new` 才不会被旧会话
    # 的异步执行抢走"最新会话"（EventWriter 每次 append 都会 bump conversation.update_time）。
    await _wait_for(
        lambda: _run_is_terminal(stack, proposal["run_id"]),
        what="确认后 Run 未在超时内到终态",
        timeout=REPLY_TIMEOUT_SEC,
    )


async def test_s05_receipt_reaches_model_and_write_is_audited(memory_probe: GatewayStack) -> None:
    """[S-05][E2E] 候选回执经真实链路交付，确认后写入可审计。

    回执的**素材**落在模型侧：工具结果必须是 `saved=false` + `confirmation_required=true` +
    `proposal_id`（不承诺"此后必然生效"）。模型如何措辞属模型行为，不在探针模型的断言范围内。
    审计行必须能读出 `memory_key`/`category`，且不包含已删除的 `source_type`。
    """
    stack = memory_probe
    await _wait_for_gateway_ws(stack)
    # 新会话：探针 tool_once 口径下，同一会话里已有工具结果就不会再次要工具
    await _new_conversation(stack)
    replies_before = len(_replies(stack))

    await _push(stack, text=USER_TEXT)
    await _wait_for(
        lambda: _pending_proposal(stack),
        what="真实 Run 未停在待确认的记忆候选",
        timeout=REPLY_TIMEOUT_SEC,
    )
    proposal = await _require_pending_proposal(stack)
    expected = proposal["proposal_json"]["expected_version"]
    target = (expected or 0) + 1
    await _push(stack, text=f"确认 {proposal['id']}")
    try:
        await _wait_for(
            lambda: _history_versions_are(stack, list(range(target, 0, -1))),
            what="确认后版本历史未递增",
        )
    except AssertionError as exc:
        raise AssertionError(f"{exc}; {await _confirmation_diagnostics(stack)}") from exc

    # 确认恢复轮的真实模型请求里带着候选回执（工具消息）；恢复执行是异步的，等它到
    await _wait_for(
        lambda: _proposal_receipts(stack, proposal["id"]),
        what="模型未在确认恢复轮收到候选回执",
        timeout=REPLY_TIMEOUT_SEC,
    )
    receipts = await _proposal_receipts(stack, proposal["id"])
    payload = json.loads(receipts[-1])
    assert payload["saved"] is False
    assert payload["confirmation_required"] is True
    assert payload["proposal_id"] == proposal["id"]
    for promise in ("必然生效", "每次都会", "一定会", "永久生效"):
        assert promise not in receipts[-1]

    delivered = _replies(stack)[replies_before:]
    assert delivered and delivered[0].strip()  # 真实出站回复非空

    history = await _history_rows(stack)
    assert history[0]["change_reason"] == ("CREATE" if expected is None else "CONFIRM")
    assert {item["actor_type"] for item in history} == {"USER"}

    audits = await _remember_audits(stack)
    latest = audits[-1]
    assert latest["run_id"] is not None
    assert latest["artifact_id"] is None  # 小结果不外置
    assert latest["args_preview_json"]["memory_key"] == MEMORY_KEY
    assert latest["args_preview_json"]["category"] == "PREFERENCE"
    assert "source_type" not in latest["args_preview_json"]

    # 收尾：确认恢复轮到终态，本模块结束后不再有异步事件追加
    await _wait_for(
        lambda: _run_is_terminal(stack, proposal["run_id"]),
        what="确认后 Run 未在超时内到终态",
        timeout=REPLY_TIMEOUT_SEC,
    )
