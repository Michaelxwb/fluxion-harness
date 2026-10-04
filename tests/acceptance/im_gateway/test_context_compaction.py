"""[S-01] 分级压缩端到端串联（FEAT-01..05）：真实 WS → Gateway → Runtime → PG → 模型 HTTP 探针。

不得 Mock 的真实边界：真实企微 WS 探针（入站帧）→ 真实 Gateway → 真实 Runtime HTTP/SSE →
真实 PostgreSQL（`runtime.canonical_event` 的压缩审计行）→ 真实 OpenAI 兼容模型探针
（`tests/e2e/openai_probe_app.py`：记录**每次真实请求体**，供断言"模型到底收到了什么"）。
成功路径不含任何响应拦截（本用例不是浏览器场景，没有也不得引入 `page.route(` 一类伪造）。

E2E 在编码阶段只登记不执行（manifest `kind=e2e`）：由需求级
`cf_acceptance_runner.py --manifest … --include-e2e` 统一执行。

场景构造：把该 Agent 的 snip 阈值压到 3 组（配置随 Run 冻结，见 E-04 的口径），于是一次
"两个来回 + 四个工具回合"的会话就足以触发**真实的 snip**——链路每一段都是生产实现。

覆盖分工（不重复也不留空）：
- 本用例：snip 真的在请求缝触发、开头诉求仍在、模型收到的请求里没有孤儿 TOOL、审计事件落库；
- 「整轮超预算 ⇒ 结果外置 + 头尾预览」由 E-02 / B-02 在集成层取证（本栈没有能产出 >8 KiB
  结果的工具，塞一个假工具就变成自造行为而不是验生产链路）。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    BOUND_EXTERNAL_USER_ID,
    CHAT_ID,
    TENANT,
    GatewayStack,
    latest_run_id,
    wait_for_new_run_terminal,
)

#: 会话开头的诉求——被 snip 省略掉中间历史之后，它必须还在（"保头"）。
OPENING = "开头诉求：这份清单很长，请逐条核对后告诉我结论"
FOLLOW_UP = "继续：把剩下的也核对一遍"
FINAL_TEXT = "核对完毕"
TOOL_ROUNDS = 4

#: 只压**触发阈值**（`max_groups` 的语义就是阈值，不是保留总数），保头保尾用生产默认值（3 / 20）：
#: 短会话才会触发真实的 snip，而省略口径仍是线上那一套。
SNIP_RUNTIME_CONFIG: dict[str, Any] = {"budget": {"compaction": {"snip": {"max_groups": 3}}}}

WAIT_TIMEOUT_SEC = 150.0
MARKER = "[历史省略]"


async def _execute(statement: str, params: dict[str, Any]) -> None:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.begin() as connection:
            await connection.execute(text(statement), params)
    finally:
        await engine.dispose()


async def _count(statement: str, params: dict[str, Any]) -> int:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            return int(await connection.scalar(text(statement), params) or 0)
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


def _arm_early_snip(stack: GatewayStack) -> None:
    """把该 Agent 的 snip 阈值压到 3 组（真实 `control.agent_definition` 行）。

    必须在**本条会话的第一次 Run 之前**写完：压缩配置在 Run 建立时解析并冻结进
    `policy_json`（E-04），之后改配置只影响后续新 Run。
    """

    async def run() -> None:
        await _execute(
            "UPDATE control.agent_definition SET runtime_config_json = CAST(:cfg AS jsonb) "
            "WHERE tenant_id = :t AND key = 'e2e_im_agent'",
            {"t": TENANT, "cfg": json.dumps(SNIP_RUNTIME_CONFIG, ensure_ascii=False)},
        )

    _run_in_thread(run)


def _run_in_thread(factory: Callable[[], Any]) -> Any:
    """独立线程 + 独立事件循环执行：验收栈存在多个事件循环，不能借用调用方的那个。"""
    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            outcome["value"] = asyncio.run(factory())
        except BaseException as exc:  # noqa: BLE001 - 原样抛回
            outcome["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def _script_tool_rounds(stack: GatewayStack, rounds: int) -> None:
    """让模型探针连调 `rounds` 次工具（`current_time` 是栈里现成的只读工具）后才收尾。"""
    response = httpx.post(
        f"{stack.llm_url}/script",
        json={
            "tools": [{"name": "current_time", "arguments": "{}"} for _ in range(rounds)],
            "final_text": FINAL_TEXT,
        },
        timeout=10.0,
    )
    response.raise_for_status()


def _clear_script(stack: GatewayStack) -> None:
    httpx.post(f"{stack.llm_url}/script", json={}, timeout=10.0).raise_for_status()


async def _push(stack: GatewayStack, *, text: str) -> None:
    probe = stack.ws_probe
    assert probe is not None
    message_id = f"compact-{uuid.uuid4().hex[:8]}"
    await probe.push_message(  # type: ignore[attr-defined]
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id=BOUND_EXTERNAL_USER_ID,
        text=text,
        reply_id=f"req-{message_id}",
        chat_id=CHAT_ID,
    )


async def _send_until_terminal(stack: GatewayStack, text: str) -> str:
    """推一条入站消息，等**这次新建的** Run 到终态（不能拿"收到任意回复"当同步点）。"""
    previous = await latest_run_id()
    await _push(stack, text=text)
    return await wait_for_new_run_terminal(previous_run_id=previous)


def _probe_requests(stack: GatewayStack) -> list[dict[str, Any]]:
    response = httpx.get(f"{stack.llm_url}/requests", timeout=10.0)
    response.raise_for_status()
    return list(response.json().get("requests") or [])


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "") for part in content if isinstance(part, dict)
        )
    return ""


def _assert_no_orphan_tools(messages: list[dict[str, Any]]) -> None:
    """按**顺序**累积已声明的 `tool_call_id`：每条 tool 消息都必须被前面的 assistant 声明过。

    孤儿 tool 消息会让供应商拒绝**整个**请求（历史上真实发生过），所以这条断言直接对着
    "模型实际收到的那份"来检，而不是对着本地重建的中间产物。
    """
    declared: set[str] = set()
    for message in messages:
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict) and isinstance(call.get("id"), str):
                declared.add(call["id"])
        if message.get("role") == "tool":
            assert message.get("tool_call_id") in declared, (
                f"模型收到了孤儿 tool 消息：tool_call_id={message.get('tool_call_id')!r}"
            )


async def test_s01_compaction_composes_on_the_real_chain(gateway_stack: GatewayStack) -> None:
    stack = gateway_stack
    _arm_early_snip(stack)

    # 第一个来回：建立"会话开头"。这轮不调工具，让 OPENING 成为对话区的第一组。
    _clear_script(stack)
    assert await _send_until_terminal(stack, OPENING) == "COMPLETED"

    # 第二个来回：四个工具回合 —— 对话区组数越过阈值，snip 必须在请求缝真的触发。
    _script_tool_rounds(stack, TOOL_ROUNDS)
    try:
        assert await _send_until_terminal(stack, FOLLOW_UP) == "COMPLETED"
    finally:
        _clear_script(stack)

    requests = _probe_requests(stack)
    compacted = [
        body
        for body in requests
        if MARKER in json.dumps(body.get("messages") or [], ensure_ascii=False)
    ]
    assert compacted, f"没有任何一次模型请求带省略标记：snip 没在请求缝执行（共 {len(requests)} 次请求）"

    sent = list(compacted[-1].get("messages") or [])
    texts = [_message_text(message) for message in sent]

    assert any(OPENING in value for value in texts), (
        f"开头诉求被压缩掉了（保头失效）：{[t[:40] for t in texts]}"
    )
    assert any(FOLLOW_UP in value for value in texts), "本轮追问必须还在"
    _assert_no_orphan_tools(sent)

    # 审计：压缩事件真的落库（canonical_event，字段形状由 E-03 覆盖）
    compacted_events = await _count(
        "SELECT count(*) FROM runtime.canonical_event "
        "WHERE tenant_id = :t AND event_type = 'CONTEXT_COMPACTED'",
        {"t": TENANT},
    )
    assert compacted_events >= 1, "压缩发生了却没有落审计事件"
