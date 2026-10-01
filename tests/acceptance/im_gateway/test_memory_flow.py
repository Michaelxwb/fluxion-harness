"""[S-01/S-05] 记忆写入与回执端到端（真实 Gateway → Runtime → PostgreSQL → LLM 探针）。

不得 Mock 的真实边界：真实企微 WS 探针 → 真实 Gateway → 真实 Runtime HTTP/SSE →
真实 PostgreSQL（`runtime.user_memory` / `runtime.tool_call_audit`）→ 真实 LLM 探针。

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
)
from tests.e2e.wecom_probe_app import frame_text

WAIT_TIMEOUT_SEC = 150.0
REPLY_TIMEOUT_SEC = 120.0
USER_TEXT = "记住：以后都用中文回答我"
MEMORY_KEY = "reply.language"
MEMORY_VALUE = "中文"
FINAL_TEXT = "好的，已记下：以后都用中文回答你。"
REMEMBER_ARGUMENTS = json.dumps(
    {
        "memory_key": MEMORY_KEY,
        "value": MEMORY_VALUE,
        "category": "PREFERENCE",
        "source_type": "USER_EXPLICIT",
    },
    ensure_ascii=False,
)


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
        "SELECT memory_key, source_type, user_id, category, content_json, version, enabled "
        "FROM runtime.user_memory WHERE tenant_id = :t ORDER BY update_time",
        {"t": stack.tenant_id},
    )


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
    """
    probe = gateway_stack.processes["llm-probe"]
    probe.env["OPENAI_PROBE_TOOL_NAME"] = "remember"
    probe.env["OPENAI_PROBE_TOOL_ARGUMENTS"] = REMEMBER_ARGUMENTS
    probe.env["OPENAI_PROBE_FINAL_TEXT"] = FINAL_TEXT
    restart_process(probe)
    return gateway_stack


@pytest.fixture(scope="module", autouse=True)
async def _cleanup_memory(gateway_stack: GatewayStack) -> AsyncIterator[None]:
    """补 `runtime.user_memory` 的清理：共享栈的 RUNTIME_CLEANUP 不含本表。"""
    yield
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM runtime.user_memory WHERE tenant_id = :t"),
                {"t": gateway_stack.tenant_id},
            )
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


async def test_s01_gateway_message_writes_user_memory(memory_probe: GatewayStack) -> None:
    """[S-01][E2E] 用户说"记住：以后都用中文回答我" → 真实链路落一行本人记忆（USER_EXPLICIT）。"""
    stack = memory_probe
    await _wait_for_gateway_ws(stack)
    replies_before = len(_replies(stack))

    await _push(stack, text=USER_TEXT)
    await _wait_for(
        lambda: len(_replies(stack)) > replies_before,
        what="未在超时内收到出站回复（Run 未跑通）",
        timeout=REPLY_TIMEOUT_SEC,
    )
    await _wait_for(lambda: _memory_rows(stack), what="记忆未落库")

    rows = await _memory_rows(stack)
    assert len(rows) == 1
    row = rows[0]
    assert row["memory_key"] == MEMORY_KEY
    assert row["source_type"] == "USER_EXPLICIT"
    assert row["category"] == "PREFERENCE"
    assert row["enabled"] is True
    assert row["content_json"] == {"value": MEMORY_VALUE}
    # 归属当前对话用户（不是别的用户、也不是工具入参能指定的人）
    assert str(row["user_id"]) == str(stack.platform_user_id)

    audits = await _remember_audits(stack)
    assert len(audits) == 1
    assert audits[0]["status"] == "OK"
    assert audits[0]["tool_call_id"]  # 模型给的调用 id，不是工具名
    assert audits[0]["run_id"] is not None


async def test_s05_receipt_reaches_model_and_write_is_audited(memory_probe: GatewayStack) -> None:
    """[S-05][E2E] 写入回执经真实链路交付，且审计行可读出 `memory_key`。

    回执的**素材**落在模型侧：工具结果必须是 `{"saved": true, "memory_key": …}`（不是被截断的
    Artifact 预览、也不是 `saved: false`）。模型如何措辞属模型行为，不在探针模型的断言范围内。
    """
    stack = memory_probe
    await _wait_for_gateway_ws(stack)
    replies_before = len(_replies(stack))

    await _push(stack, text=USER_TEXT)
    await _wait_for(
        lambda: len(_replies(stack)) > replies_before,
        what="未在超时内收到出站回复",
        timeout=REPLY_TIMEOUT_SEC,
    )
    delivered = _replies(stack)[replies_before:]
    assert delivered and delivered[0].strip()  # 真实出站回复非空

    receipts = [item for item in await _probe_tool_messages(stack) if MEMORY_KEY in item]
    assert receipts, "模型未收到含 memory_key 的工具回执"
    payload = json.loads(receipts[-1])
    assert payload["saved"] is True
    assert payload["memory_key"] == MEMORY_KEY
    assert isinstance(payload["version"], int)
    # 回执不得承诺"此后必然生效"——记忆是否生效受注入上限约束，承诺即是错话
    for promise in ("必然生效", "每次都会", "一定会", "永久生效"):
        assert promise not in receipts[-1]

    audits = await _remember_audits(stack)
    assert audits, "审计缺 remember 行"
    latest = audits[-1]
    assert latest["run_id"] is not None
    assert latest["artifact_id"] is None  # 小结果不外置
    assert latest["args_preview_json"]["memory_key"] == MEMORY_KEY
    assert latest["args_preview_json"]["source_type"] == "USER_EXPLICIT"
