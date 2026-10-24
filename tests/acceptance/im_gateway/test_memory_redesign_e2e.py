"""E-02/E-03/E-05/E-06 记忆重设计端到端（真实 Gateway/Runtime/PG/Redis/模型探针/双 Runtime 进程）。

不得 Mock 的真实边界
- E-02：真实 WS 探针 → 真实 Gateway（入站去重 + `message_id` 幂等键）→ 真实 Runtime
  （`/v1/runs` 与 WAITING_INPUT 的真实 resume）→ 真实 PG（`runtime.run_interrupt` 的
  MEMORY_CONFIRMATION、`user_memory`/`user_memory_history`/`run_submission`）；候选展示与确认结果
  都是真实 SSE 事件经生产渲染器投递到真实 WS 帧的文本。
- E-03：真实 Gateway（`/new` 命令 + 新会话路由）+ 真实 Runtime + 真实 PG + 真实模型 HTTP 探针；
  断言「模型实际收到的请求体」里出现已确认中文偏好，而用户输入只是「你好」（query 不含「中文」）。
- E-05：真实 Gateway/Runtime/PG + 模型探针 + Runtime 进程内真实 consolidation（MemorySupervisor
  消费真实 job）+ 真实 `/new` 跨会话续接；大 checkpoint 的引用与 bytes dropped 从真实请求体与
  Runtime `/metrics` 读取。
- E-06：真实两个 Runtime 子进程（同一 PG/Redis）；对 claim 执行者发 SIGKILL，另一实例在租约过期后
  接管；持久写入效果最多一次（真实 PG 回读 job/记忆事实）。

E2E 场景在编码阶段只编写 + collect，登记 `e2e_deferred`；执行归需求终验
（`code-flow acceptance run --include-e2e` 或
`uv run pytest -q tests/acceptance/im_gateway/test_memory_redesign_e2e.py`）。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
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

pytestmark = pytest.mark.e2e

WAIT_TIMEOUT_SEC = 240.0
REPLY_TIMEOUT_SEC = 150.0
NEW_CONVERSATION_TEXT = "已创建新会话"
CONSOLIDATION_MARKER = "Consolidate this frozen conversation input"
#: 脚本化提炼响应必须是严格 JSON 批次（`memory_consolidation_output.ConsolidationBatch`）。
EMPTY_BATCH = '{"candidates": [], "task_completed": false}'
COMPLETED_BATCH = '{"candidates": [], "task_completed": true}'
#: 双 Runtime 实例的真实进程身份（`environment.py` 按实例注入 POD_NAME ⇒ `instance_id` 前缀）。
RUNTIME_ONE = "e2e-runtime-1"
#: 事件诊断辅助用的租户（测试内赋值；只影响失败信息，不影响断言口径）。
TENANT_HOLDER: list[str] = [""]
RUNTIME_TWO = "e2e-runtime-2"


async def _fetch(statement: str, params: dict[str, object]) -> list[dict[str, Any]]:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            rows = (await connection.execute(text(statement), params)).mappings().all()
            return [dict(row) for row in rows]
    finally:
        await engine.dispose()


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


async def _push(stack: GatewayStack, *, text: str, message_id: str | None = None) -> str:
    probe = stack.ws_probe
    assert probe is not None
    identifier = message_id or f"e2e-mem-{uuid.uuid4().hex[:10]}"
    await probe.push_message(  # type: ignore[attr-defined]
        bot_id=BOT_ID,
        message_id=identifier,
        external_user_id=BOUND_EXTERNAL_USER_ID,
        text=text,
        reply_id=f"req-{identifier}",
        chat_id=CHAT_ID,
    )
    return identifier


async def _wait_for_gateway_ws(stack: GatewayStack, *, minimum_connections: int = 1) -> None:
    probe = stack.ws_probe
    assert probe is not None, "WS 探针未接入栈"
    await _wait_for(
        lambda: sum(1 for bot in getattr(probe, "connection_bots", {}).values() if bot == BOT_ID)
        >= minimum_connections,
        what=f"Gateway 未在超时内连上真实 WS 探针（bot {BOT_ID}）",
        timeout=REPLY_TIMEOUT_SEC,
    )


def _set_script(stack: GatewayStack, **payload: Any) -> None:
    """真实模型探针脚本（`POST /script`）：业务回合与提炼回合分开脚本化。"""
    response = httpx.post(f"{stack.llm_url}/script", json=payload, timeout=10.0)
    response.raise_for_status()


def _probe_requests(stack: GatewayStack) -> list[dict[str, Any]]:
    response = httpx.get(f"{stack.llm_url}/requests", timeout=10.0)
    response.raise_for_status()
    return [body for body in (response.json().get("requests") or []) if isinstance(body, dict)]


def _is_consolidation(body: dict[str, Any]) -> bool:
    return any(
        isinstance(message, dict)
        and message.get("role") == "system"
        and CONSOLIDATION_MARKER in str(message.get("content") or "")
        for message in body.get("messages") or []
    )


def _last_business_request(stack: GatewayStack) -> dict[str, Any]:
    """最近一次**业务回合**请求（提炼回合的 system 指令带固定 marker，须排除）。"""
    for body in reversed(_probe_requests(stack)):
        if not _is_consolidation(body):
            return body
    raise AssertionError("模型探针没有记录到业务回合请求")


def _system_text(body: dict[str, Any]) -> str:
    return "\n".join(
        str(message.get("content") or "")
        for message in body.get("messages") or []
        if isinstance(message, dict) and message.get("role") == "system"
    )


async def _latest_run_id(stack: GatewayStack) -> str | None:
    value = await _scalar(
        "SELECT id::text FROM runtime.run_record WHERE tenant_id = :tenant"
        " ORDER BY create_time DESC LIMIT 1",
        {"tenant": stack.tenant_id},
    )
    return None if value is None else str(value)


async def _run_count(stack: GatewayStack) -> int:
    return int(
        await _scalar(
            "SELECT count(*) FROM runtime.run_record WHERE tenant_id = :tenant",
            {"tenant": stack.tenant_id},
        )
        or 0
    )


async def _run_count_exceeds(stack: GatewayStack, previous: int) -> bool:
    return await _run_count(stack) > previous


async def _started_run(stack: GatewayStack, text: str) -> str:
    """推送一条真实消息并返回本次新建的 Run id（不假设终态：确认类 Run 会停在 WAITING_INPUT）。

    同步点用**行数增加**，不是"存在某行 id ≠ 上一行"：后者在本模块第 2 个用例起恒真
    （旧 Run 早就在），于是会立刻返回**上一条** Run（2026-10-15 实测：续接消息的 Run 已建在
    新会话里，用例却拿到旧会话的 Run，后续断言全部错位）。
    """
    before = await _run_count(stack)
    await _push(stack, text=text)
    await _wait_for(
        lambda: _run_count_exceeds(stack, before),
        what=f"推送 {text!r} 后未创建 Run",
        timeout=REPLY_TIMEOUT_SEC,
    )
    run_id = await _latest_run_id(stack)
    assert run_id is not None
    return run_id


async def _run_status(run_id: str) -> str | None:
    value = await _scalar(
        "SELECT status FROM runtime.run_record WHERE id = :run_id", {"run_id": uuid.UUID(run_id)}
    )
    return None if value is None else str(value)


async def _conversation_of(run_id: str) -> str:
    value = await _scalar(
        "SELECT conversation_id::text FROM runtime.run_record WHERE id = :run_id",
        {"run_id": uuid.UUID(run_id)},
    )
    assert value is not None, f"Run {run_id} 不存在"
    return str(value)


async def _run_is_terminal(run_id: str) -> bool:
    return await _run_status(run_id) in ("COMPLETED", "FAILED", "CANCELLED")


async def _wait_run_terminal(run_id: str) -> str:
    await _wait_for(
        lambda: _run_is_terminal(run_id),
        what=f"Run {run_id} 未在超时内到达终态",
    )
    status = await _run_status(run_id)
    assert status == "COMPLETED", f"Run {run_id} 终态为 {status}"
    return status


async def _waiting_proposal(run_id: str) -> dict[str, Any]:
    rows = await _fetch(
        "SELECT id::text AS id, prompt_text, proposal_json FROM runtime.run_interrupt"
        " WHERE run_id = :run_id AND interrupt_type = 'MEMORY_CONFIRMATION' AND status = 'WAITING'",
        {"run_id": uuid.UUID(run_id)},
    )
    assert len(rows) == 1, f"期望恰好一条待确认提案，实际 {len(rows)}"
    return rows[0]


async def _memory_rows(stack: GatewayStack, key: str) -> list[dict[str, Any]]:
    return await _fetch(
        "SELECT memory_key,author,content_json,version,enabled,is_deleted,retirement_reason,"
        " explicit_request,confirmed_count,confirmation_event_id,last_confirmed_at"
        " FROM runtime.user_memory WHERE tenant_id = :tenant AND memory_key = :key",
        {"tenant": stack.tenant_id, "key": key},
    )


async def _history_count(stack: GatewayStack, key: str) -> int:
    return int(
        await _scalar(
            "SELECT count(*) FROM runtime.user_memory_history WHERE tenant_id = :tenant"
            " AND memory_key = :key",
            {"tenant": stack.tenant_id, "key": key},
        )
        or 0
    )


async def _task_state_rows(stack: GatewayStack) -> list[dict[str, Any]]:
    return await _fetch(
        "SELECT memory_key,author,version,is_deleted,retirement_reason,content_json"
        " FROM runtime.user_memory WHERE tenant_id = :tenant AND user_id = :user_id"
        " AND category = 'TASK_STATE' ORDER BY memory_key",
        {"tenant": stack.tenant_id, "user_id": stack.platform_user_id},
    )


async def _reset_memories(stack: GatewayStack) -> None:
    """场景级清理：只清本租户的记忆事实与历史（含前序场景留下的 TASK_STATE）。

    「唯一未决/多条线索」这类断言必须不受前序场景残留影响；运行记录、事件与 job 行保留
    （失败 trace 仍可回查）。同租户的栈级 `purge_tenant` 在模块收尾仍会再清一遍。

    清之前先等记忆后台静止：前序用例的 consolidation job 可能在**清空之后**才收尾，
    它按当时探针脚本写下的 TASK_STATE 行会重新落进刚清空的表（2026-10-15 实测：
    e03 的 job 在 e05_default 清空后 0.9s 落地，断言看到两条线索）。
    """
    await wait_for_memory_background_idle(stack)
    for statement in (
        "DELETE FROM runtime.user_memory_history WHERE tenant_id = :tenant",
        "DELETE FROM runtime.user_memory WHERE tenant_id = :tenant",
    ):
        await _execute(statement, {"tenant": stack.tenant_id})


async def _wait_rows(
    statement: str, params: dict[str, object], *, what: str, timeout: float = REPLY_TIMEOUT_SEC
) -> list[dict[str, Any]]:
    """等一条真实 PG 查询出现结果（失败时把 what 原样抛出，由调用方带上诊断上下文）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = await _fetch(statement, params)
        if rows:
            return rows
        await asyncio.sleep(0.3)
    raise AssertionError(what)


async def _conversation_rows(stack: GatewayStack) -> list[dict[str, Any]]:
    return await _fetch(
        "SELECT id::text AS id, update_time, create_time, last_seq FROM runtime.conversation"
        " WHERE tenant_id = :tenant ORDER BY update_time DESC",
        {"tenant": stack.tenant_id},
    )


async def _conversation_events(conversation: str) -> list[dict[str, Any]]:
    return await _fetch(
        "SELECT seq,event_type,submission_id::text AS submission_id,payload_json->>'text' AS text"
        " FROM runtime.canonical_event"
        " WHERE tenant_id = :tenant AND conversation_id = :conversation AND is_deleted = false"
        " ORDER BY seq",
        {"tenant": TENANT_HOLDER[0], "conversation": uuid.UUID(conversation)},
    )


async def _job_row(run_id: str) -> dict[str, Any] | None:
    rows = await _fetch(
        "SELECT id::text AS id,status,attempt,claim_epoch,lease_owner,lease_until,result_json,error_code"
        " FROM runtime.memory_consolidation_job WHERE run_id = :run_id",
        {"run_id": uuid.UUID(run_id)},
    )
    return rows[0] if rows else None


async def _wait_job_terminal(run_id: str) -> dict[str, Any]:
    """等真实后台 consolidation（Runtime 进程内 MemorySupervisor）把 job 跑到终态。"""
    await _wait_for(
        lambda: _scalar(
            "SELECT count(*) FROM runtime.memory_consolidation_job"
            " WHERE run_id = :run_id AND status IN ('DONE','SKIPPED','FAILED')",
            {"run_id": uuid.UUID(run_id)},
        ),
        what=f"Run {run_id} 的 consolidation job 未在超时内终结",
    )
    row = await _job_row(run_id)
    assert row is not None and row["status"] in ("DONE", "SKIPPED"), row
    return row


async def _override_memory_settings(stack: GatewayStack, **memory: int) -> int:
    """真实 PG 写入一版平台设置（只覆盖 memory 子文档，其余按键默认补全）；返回新 revision。"""
    revision = int(
        await _scalar(
            "SELECT coalesce(max(revision), 0) FROM control.platform_setting"
            " WHERE tenant_id = :tenant AND is_deleted = false",
            {"tenant": stack.tenant_id},
        )
        or 0
    )
    await _execute(
        "INSERT INTO control.platform_setting (id, tenant_id, revision, settings_json)"
        " VALUES (:id, :tenant, :revision, CAST(:settings AS jsonb))",
        {
            "id": uuid.uuid4(),
            "tenant": stack.tenant_id,
            "revision": revision + 1,
            "settings": json.dumps({"memory": memory}),
        },
    )
    return revision + 1


async def _drop_platform_setting(stack: GatewayStack, revision: int) -> None:
    await _execute(
        "DELETE FROM control.platform_setting WHERE tenant_id = :tenant AND revision = :revision",
        {"tenant": stack.tenant_id, "revision": revision},
    )


async def _new_conversation(stack: GatewayStack) -> None:
    """/new 是真实命令：等真实出站文本（命令不产生 Run）。

    换会话**前**先等记忆后台静止：前序 Run 的 consolidation 收尾会向旧会话追加事件并
    bump `conversation.update_time`，赶在 `/new` 之后落地就会把新会话顶掉，后续推送落回
    旧会话（用例对"续接落进新会话"的断言随之错位）。
    """
    await wait_for_memory_background_idle(stack)
    replies_before = len(_replies(stack))
    await _push(stack, text="/new")
    await _wait_for(
        lambda: NEW_CONVERSATION_TEXT in "\n".join(_replies(stack)[replies_before:]),
        what="/new 未在超时内创建真实会话",
        timeout=REPLY_TIMEOUT_SEC,
    )


def _task_batch(*, title: str, goal: str, task_completed: bool = False) -> str:
    """提炼响应模板：`$root_memory_key`/`$root_conversation_id` 由探针按真实请求替换。"""
    return json.dumps(
        {
            "candidates": [
                {
                    "memory_key": "$root_memory_key",
                    "category": "TASK_STATE",
                    "content": {
                        "title": title,
                        "goal": goal,
                        "constraints": [],
                        "open_items": ["等待用户补充"],
                        "next_action": "核验真实状态与授权",
                        "root_conversation_id": "$root_conversation_id",
                        "source_conversation_id": "$source_conversation_id",
                    },
                }
            ],
            "task_completed": task_completed,
        },
        ensure_ascii=False,
    )


def _remember_arguments(*, key: str, value: str, quote: str) -> str:
    return json.dumps(
        {"memory_key": key, "category": "PREFERENCE", "value": value, "source_quote": quote},
        ensure_ascii=False,
    )


def _counters(stack: GatewayStack) -> dict[str, float]:
    """Runtime 真实 /metrics 计数（按「指标名+标签」取数）。"""
    response = httpx.get(f"{stack.runtime_url}/metrics", timeout=10.0)
    response.raise_for_status()
    counters: dict[str, float] = {}
    for line in response.text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, value = line.rpartition(" ")
        try:
            counters[name] = float(value)
        except ValueError:
            continue
    return counters


async def test_e02_memory_redesign_real_confirmation_replay_and_rejection(
    gateway_stack: GatewayStack,
) -> None:
    """自然语言候选真实展示 → 用户真实确认；重连/重复提交不重复保存；拒绝不产生 USER 事实。"""
    key = f"e02.preference.{uuid.uuid4().hex[:6]}"
    quote = "以后都用中文回答我"
    _set_script(
        gateway_stack,
        tool_name="remember",
        tool_arguments=_remember_arguments(key=key, value="中文", quote=quote),
        final_text="请确认是否保存这条偏好。",
        consolidation_text=EMPTY_BATCH,
        tool_once=True,
    )
    await _wait_for_gateway_ws(gateway_stack)
    replies_before = len(_replies(gateway_stack))
    run_id = await _started_run(gateway_stack, quote)

    await _wait_for(
        lambda: _scalar(
            "SELECT count(*) FROM runtime.run_interrupt WHERE run_id = :run_id"
            " AND interrupt_type = 'MEMORY_CONFIRMATION' AND status = 'WAITING'",
            {"run_id": uuid.UUID(run_id)},
        ),
        what="真实 Run 未停在待确认的记忆提案",
        timeout=REPLY_TIMEOUT_SEC,
    )
    proposal = await _waiting_proposal(run_id)
    prompt = str(proposal["prompt_text"])
    assert f"候选编号：{proposal['id']}" in prompt
    assert key in prompt and quote in prompt, "候选提示必须带键与原话来源"
    # 候选提示经真实 SSE → Gateway 渲染 → WS 帧：投递是异步的（回复体按节拍 flush），必须等它到
    await _wait_for(
        lambda: f"候选编号：{proposal['id']}" in "\n".join(_replies(gateway_stack)[replies_before:]),
        what="候选提示未经真实 SSE 渲染投递到 WS 帧",
        timeout=REPLY_TIMEOUT_SEC,
    )
    assert await _memory_rows(gateway_stack, key) == [], "未经确认不得产生事实"

    # 用户真实确认：新的一条真实入站消息（幂等键 = message_id）
    confirm_id = await _push(gateway_stack, text=f"确认 {proposal['id']}")
    await _wait_run_terminal(run_id)
    rows = await _memory_rows(gateway_stack, key)
    assert len(rows) == 1 and rows[0]["author"] == "USER" and rows[0]["version"] == 1, rows
    assert await _history_count(gateway_stack, key) == 1
    await _wait_for(
        lambda: "记忆已保存" in "\n".join(_replies(gateway_stack)[replies_before:]),
        what="确认结果未投递到 WS 帧",
        timeout=REPLY_TIMEOUT_SEC,
    )

    # 重复提交：同一幂等键（message_id）+ 同一内容 → Runtime 持久重放，不产生第二次效果
    payload = {
        "agent_id": str(gateway_stack.agent_id),
        "platform_user_id": str(gateway_stack.platform_user_id),
        # Gateway 的 create_run 不传 conversation_id（由 Runtime 解析最新会话）：重放必须同形
        "conversation_id": None,
        "channel": {
            "type": "WECOM",
            "bot_id": gateway_stack.bot_id,
            "external_user_id": BOUND_EXTERNAL_USER_ID,
            "external_conversation_id": CHAT_ID,
        },
        "message": {"id": confirm_id, "type": "text", "text": f"确认 {proposal['id']}"},
    }
    async with httpx.AsyncClient(base_url=gateway_stack.runtime_url, timeout=30.0) as client:
        replay = await client.post(
            "/v1/runs",
            json=payload,
            headers={"X-Tenant-Id": gateway_stack.tenant_id, "Idempotency-Key": confirm_id},
        )
    assert replay.status_code == 200, replay.text
    assert "run.created" in replay.text, replay.text
    submissions = await _scalar(
        "SELECT count(*) FROM runtime.run_submission WHERE tenant_id = :tenant"
        " AND idempotency_key = :key AND endpoint = 'create-run' AND is_deleted = false",
        {"tenant": gateway_stack.tenant_id, "key": confirm_id},
    )
    assert int(submissions or 0) == 1
    assert len(await _memory_rows(gateway_stack, key)) == 1
    assert await _history_count(gateway_stack, key) == 1

    # 重连：Gateway 真实进程重启后平台重投同一 msgid，事实不变（入站去重 + 持久重放）
    connections = len(getattr(gateway_stack.ws_probe, "connection_bots", {}))
    restart_process(gateway_stack.processes["gateway"])
    await _wait_for_gateway_ws(gateway_stack, minimum_connections=connections + 1)
    await _push(gateway_stack, text=f"确认 {proposal['id']}", message_id=confirm_id)
    await asyncio.sleep(2.0)
    assert len(await _memory_rows(gateway_stack, key)) == 1
    assert await _history_count(gateway_stack, key) == 1

    # 拒绝：真实用户拒绝后不产生 USER 事实（新会话：tool_once 下同会话已有工具结果就不再调工具）
    reject_key = f"e02.reject.{uuid.uuid4().hex[:6]}"
    reject_quote = "顺便记一下我喜欢深色模式"
    await _new_conversation(gateway_stack)
    _set_script(
        gateway_stack,
        tool_name="remember",
        tool_arguments=_remember_arguments(key=reject_key, value="深色模式", quote=reject_quote),
        final_text="请确认是否保存这条偏好。",
        consolidation_text=EMPTY_BATCH,
        tool_once=True,
    )
    reject_run = await _started_run(gateway_stack, reject_quote)
    await _wait_for(
        lambda: _scalar(
            "SELECT count(*) FROM runtime.run_interrupt WHERE run_id = :run_id"
            " AND interrupt_type = 'MEMORY_CONFIRMATION' AND status = 'WAITING'",
            {"run_id": uuid.UUID(reject_run)},
        ),
        what="第二个真实 Run 未停在待确认提案",
        timeout=REPLY_TIMEOUT_SEC,
    )
    reject_proposal = await _waiting_proposal(reject_run)
    await _push(gateway_stack, text=f"拒绝 {reject_proposal['id']}")
    await _wait_run_terminal(reject_run)
    assert await _memory_rows(gateway_stack, reject_key) == [], "拒绝不得产生 USER 事实"
    resolved = await _fetch(
        "SELECT resolution_json FROM runtime.run_interrupt WHERE id = :id",
        {"id": uuid.UUID(str(reject_proposal["id"]))},
    )
    assert resolved[0]["resolution_json"]["outcome"] == "REJECTED", resolved


async def _assert_preference_visible_after_new_conversation(
    stack: GatewayStack, *, key: str, value: str = "中文"
) -> None:
    """/new 后「你好」首轮请求必须带该偏好；断言不依赖 query 含偏好原文。"""
    _set_script(stack, final_text="好的。", consolidation_text=EMPTY_BATCH)
    await _wait_for_gateway_ws(stack)
    conversations_before = int(
        await _scalar(
            "SELECT count(*) FROM runtime.conversation WHERE tenant_id = :tenant",
            {"tenant": stack.tenant_id},
        )
        or 0
    )
    await _new_conversation(stack)
    conversations_after = int(
        await _scalar(
            "SELECT count(*) FROM runtime.conversation WHERE tenant_id = :tenant",
            {"tenant": stack.tenant_id},
        )
        or 0
    )
    assert conversations_after == conversations_before + 1

    run_id = await _started_run(stack, "你好")
    await _wait_run_terminal(run_id)
    body = _last_business_request(stack)
    block = _system_text(body)
    assert key in block and value in block, "已确认偏好在 /new 后的首轮请求必须可见"
    user_texts = [
        str(message.get("content") or "")
        for message in body.get("messages") or []
        if isinstance(message, dict) and message.get("role") == "user"
    ]
    assert any("你好" in item for item in user_texts)
    assert all(value not in item for item in user_texts), "断言不依赖 query 含偏好原文"


async def test_e03_memory_redesign_confirmed_user_preference_visible_after_new_conversation(
    gateway_stack: GatewayStack,
) -> None:
    """E-03：**真实用户确认**得到的 USER 偏好在 /new 后「你好」首轮可见（query 不含「中文」）。

    链路必须真的经过：候选展示（真实 SSE→WS 帧）→ 用户真实确认（真实入站消息 + 确认事件）
    → USER 事实（author/explicit_request/confirmed_count/confirmation_event_id 均为确认产物）
    → /new 首轮请求。评审条目 8：直接经管理 API 写 ADMIN 事实覆盖不到这条链路，任何一环损坏
    都必须红（ADMIN 通道另设独立对照用例）。
    """
    key = f"e03.preference.{uuid.uuid4().hex[:6]}"
    quote = "以后都用中文回答我"
    _set_script(
        gateway_stack,
        tool_name="remember",
        tool_arguments=_remember_arguments(key=key, value="中文", quote=quote),
        final_text="请确认是否保存这条偏好。",
        consolidation_text=EMPTY_BATCH,
        tool_once=True,
    )
    await _wait_for_gateway_ws(gateway_stack)
    # 新会话：`tool_once` 按「整段会话是否已有工具结果」判定，前序用例的会话里已有工具结果，
    # 留在旧会话会让探针直接给 final_text（实测：候选永远不会出现）。
    await _new_conversation(gateway_stack)
    replies_before = len(_replies(gateway_stack))
    run_id = await _started_run(gateway_stack, quote)

    await _wait_for(
        lambda: _scalar(
            "SELECT count(*) FROM runtime.run_interrupt WHERE run_id = :run_id"
            " AND interrupt_type = 'MEMORY_CONFIRMATION' AND status = 'WAITING'",
            {"run_id": uuid.UUID(run_id)},
        ),
        what="真实 Run 未停在待确认的记忆提案",
        timeout=REPLY_TIMEOUT_SEC,
    )
    proposal = await _waiting_proposal(run_id)
    await _wait_for(
        lambda: f"候选编号：{proposal['id']}" in "\n".join(_replies(gateway_stack)[replies_before:]),
        what="候选提示未经真实 SSE 渲染投递到 WS 帧",
        timeout=REPLY_TIMEOUT_SEC,
    )
    assert await _memory_rows(gateway_stack, key) == [], "未经确认不得产生事实"

    await _push(gateway_stack, text=f"确认 {proposal['id']}")
    await _wait_run_terminal(run_id)
    rows = await _memory_rows(gateway_stack, key)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["author"] == "USER" and row["version"] == 1, rows
    assert row["explicit_request"] is True and row["confirmed_count"] == 1, rows
    assert row["confirmation_event_id"] is not None and row["last_confirmed_at"] is not None, rows
    resolution = await _fetch(
        "SELECT resolution_json FROM runtime.run_interrupt WHERE id = :id",
        {"id": uuid.UUID(str(proposal["id"]))},
    )
    assert resolution[0]["resolution_json"]["outcome"] == "CONFIRMED", resolution
    assert (
        await _scalar(
            "SELECT count(*) FROM runtime.canonical_event WHERE tenant_id = :tenant"
            " AND event_type = 'MEMORY_CONFIRMATION_RESOLVED' AND source_event_id = :id"
            " AND is_deleted = false",
            {"tenant": gateway_stack.tenant_id, "id": uuid.UUID(str(proposal["id"]))},
        )
        == 1
    ), "真实确认必须有 MEMORY_CONFIRMATION_RESOLVED 事件"
    await _wait_for(
        lambda: "记忆已保存" in "\n".join(_replies(gateway_stack)[replies_before:]),
        what="确认结果未投递到 WS 帧",
        timeout=REPLY_TIMEOUT_SEC,
    )

    await _assert_preference_visible_after_new_conversation(gateway_stack, key=key)


async def test_e03_memory_redesign_admin_preference_visible_after_new_conversation(
    gateway_stack: GatewayStack,
) -> None:
    """E-03 对照：ADMIN 显式录入（管理 API）的偏好在 /new 后首轮同样可见，来源保持 ADMIN。

    与 USER 链路分开断言：ADMIN 通道不经过候选确认，不能用它代替真实用户确认的覆盖。
    """
    key = f"e03.admin.{uuid.uuid4().hex[:6]}"
    command_id = uuid.uuid4().hex
    # Runtime 管理命令契约（TASK-023）：一条命令一个 mutation，调用方不选作者。
    async with httpx.AsyncClient(timeout=30.0) as client:
        created = await client.post(
            f"{gateway_stack.runtime_url}/internal/admin/users/{gateway_stack.platform_user_id}"
            "/memory/commands",
            json={
                "actor_id": str(gateway_stack.platform_user_id),
                "mutation": {
                    "action": "UPSERT",
                    "memory_key": key,
                    "category": "PREFERENCE",
                    "value": "中文",
                },
            },
            headers={**gateway_stack.service_headers(), "Idempotency-Key": command_id},
        )
    assert created.status_code == 200, created.text
    rows = await _memory_rows(gateway_stack, key)
    assert len(rows) == 1 and rows[0]["author"] == "ADMIN", rows

    await _assert_preference_visible_after_new_conversation(gateway_stack, key=key)


async def test_e05_memory_redesign_default_consolidation_then_new_conversation_resume(
    gateway_stack: GatewayStack,
) -> None:
    """默认设置提炼未决项；/new 后自然语言续接进入真实模型请求。"""
    await _reset_memories(gateway_stack)
    title = f"季度报告{uuid.uuid4().hex[:4]}"
    _set_script(
        gateway_stack,
        final_text="已记录。",
        consolidation_text=_task_batch(title=title, goal="补齐季度数据后交付"),
    )
    await _wait_for_gateway_ws(gateway_stack)
    # 新会话：本用例的提炼 job 不与同会话的前序 job 争因果序（`_eligible` 的 predecessor）
    await _new_conversation(gateway_stack)
    run_a = await _started_run(gateway_stack, "帮我把季度报告收个尾")
    job = await _wait_job_terminal(run_a)
    assert job["status"] == "DONE", job
    conversation_a = await _conversation_of(run_a)
    original_key = f"task.{conversation_a.replace('-', '')}"
    rows = await _task_state_rows(gateway_stack)
    assert [row["memory_key"] for row in rows] == [original_key], rows
    assert rows[0]["author"] == "AGENT" and rows[0]["is_deleted"] is False, rows

    # 后续 job 不再写新线索：隔离本用例的断言面（真实 job 仍照常跑完）
    _set_script(gateway_stack, final_text="已记录。", consolidation_text=EMPTY_BATCH)
    await _new_conversation(gateway_stack)
    resumed = await _started_run(gateway_stack, "继续上次任务")
    await _wait_run_terminal(resumed)
    conversations = await _fetch(
        "SELECT id::text AS id, update_time FROM runtime.conversation WHERE tenant_id = :tenant"
        " ORDER BY update_time DESC",
        {"tenant": gateway_stack.tenant_id},
    )
    assert await _conversation_of(resumed) != conversation_a, (conversation_a, conversations)
    block = _system_text(_last_business_request(gateway_stack))
    assert original_key in block, "唯一未决线索必须装进真实模型请求"
    assert "task_state" in block and "未确认" in block
    assert rows[0]["content_json"]["title"] == title, rows[0]["content_json"]


async def test_e05_memory_redesign_second_option_selection_enters_real_request(
    gateway_stack: GatewayStack,
) -> None:
    """多条线索 → 用户「第2个」→ 目标（原任务根）实际进入请求。"""
    await _reset_memories(gateway_stack)
    first_title = f"季度报告{uuid.uuid4().hex[:4]}"
    _set_script(
        gateway_stack,
        final_text="已记录。",
        consolidation_text=_task_batch(title=first_title, goal="补齐季度数据后交付"),
    )
    await _wait_for_gateway_ws(gateway_stack)
    run_a = await _started_run(gateway_stack, "帮我把季度报告收个尾")
    job_a = await _wait_job_terminal(run_a)
    assert job_a["status"] == "DONE", (job_a["result_json"], job_a["error_code"])
    key_a = f"task.{(await _conversation_of(run_a)).replace('-', '')}"

    # 每个任务线索由各自会话的提炼产生（原任务根 = 该会话），故第二条先 /new
    second_title = f"发布计划{uuid.uuid4().hex[:4]}"
    _set_script(
        gateway_stack,
        final_text="已记录。",
        consolidation_text=_task_batch(title=second_title, goal="确认发布时间后发布"),
    )
    await _new_conversation(gateway_stack)
    run_b = await _started_run(gateway_stack, "发布计划也要盯一下")
    job_b = await _wait_job_terminal(run_b)
    assert job_b["status"] == "DONE", job_b
    key_b = f"task.{(await _conversation_of(run_b)).replace('-', '')}"
    assert key_a != key_b, (key_a, key_b)
    assert sorted(row["memory_key"] for row in await _task_state_rows(gateway_stack)) == sorted(
        [key_a, key_b]
    )

    _set_script(gateway_stack, final_text="已记录。", consolidation_text=EMPTY_BATCH)
    await _new_conversation(gateway_stack)
    replies_before = len(_replies(gateway_stack))
    run_c = await _started_run(gateway_stack, "继续上次任务")
    conversation_c = await _conversation_of(run_c)
    TENANT_HOLDER[0] = gateway_stack.tenant_id
    try:
        options = await _wait_rows(
            "SELECT payload_json FROM runtime.canonical_event WHERE tenant_id = :tenant"
            " AND conversation_id = :conversation AND event_type = 'MEMORY_RESUME_OPTIONS'"
            " AND is_deleted = false",
            {"tenant": gateway_stack.tenant_id, "conversation": uuid.UUID(conversation_c)},
            what="多条线索未落真实 MEMORY_RESUME_OPTIONS 事件",
        )
    except AssertionError as exc:
        raise AssertionError(
            f"{exc}: {await _conversation_events(conversation_c)}"
            f" | conversations={await _conversation_rows(gateway_stack)}"
        ) from exc
    items = options[0]["payload_json"]["items"]
    assert len(items) == 2, (items, await _conversation_events(conversation_c))
    target = next(item for item in items if item["number"] == 2)
    assert {item["memory_key"] for item in items} == {key_a, key_b}, items
    await _wait_run_terminal(run_c)
    # 候选清单属于「产生 options 的那一轮」的推送；必须在发起「第2个」之前观察，
    # 否则会把上一轮的辅助消息错当成选择后的新回复（E05 在 CI 上因此超时）。
    await _wait_for(
        lambda: "请回复完整编号" in "\n".join(_replies(gateway_stack)[replies_before:]),
        what="候选清单未经真实 SSE 渲染投递到 WS 帧",
        timeout=REPLY_TIMEOUT_SEC,
    )

    # 下一条真实用户消息先消费未消费的 options（单次消费）：编号选择须落真实结果事件
    second_run = await _started_run(gateway_stack, "第2个")
    await _wait_run_terminal(second_run)
    await _wait_for(
        lambda: _scalar(
            "SELECT count(*) FROM runtime.canonical_event WHERE tenant_id = :tenant"
            " AND conversation_id = :conversation AND event_type = 'MEMORY_RESUME_RESOLVED'"
            " AND is_deleted = false",
            {"tenant": gateway_stack.tenant_id, "conversation": uuid.UUID(conversation_c)},
        ),
        what="「第2个」未落真实 MEMORY_RESUME_RESOLVED 事件",
        timeout=REPLY_TIMEOUT_SEC,
    )
    resolved = await _fetch(
        "SELECT payload_json FROM runtime.canonical_event WHERE tenant_id = :tenant"
        " AND conversation_id = :conversation AND event_type = 'MEMORY_RESUME_RESOLVED'"
        " AND is_deleted = false ORDER BY seq DESC LIMIT 1",
        {"tenant": gateway_stack.tenant_id, "conversation": uuid.UUID(conversation_c)},
    )
    assert resolved[0]["payload_json"]["outcome"] == "SELECTED", resolved
    assert resolved[0]["payload_json"]["resume_ref"]["memory_key"] == target["memory_key"]
    block = _system_text(_last_business_request(gateway_stack))
    assert target["memory_key"] in block, "选中的原任务必须实际进入请求"
    assert target["title"] in block


async def test_e05_memory_redesign_completion_retires_original_and_large_checkpoint_reference(
    gateway_stack: GatewayStack,
) -> None:
    """A→B→C 仍唯一且完成后无旧未决；大 checkpoint 投递引用，引用超预算仅计丢弃。"""
    await _reset_memories(gateway_stack)
    title = f"季度报告{uuid.uuid4().hex[:4]}"
    _set_script(
        gateway_stack,
        final_text="已记录。",
        consolidation_text=_task_batch(title=title, goal="补齐季度数据后交付"),
    )
    await _wait_for_gateway_ws(gateway_stack)
    await _new_conversation(gateway_stack)
    run_a = await _started_run(gateway_stack, "帮我把季度报告收个尾")
    job_a = await _wait_job_terminal(run_a)
    assert job_a["status"] == "DONE", job_a
    root_key = f"task.{(await _conversation_of(run_a)).replace('-', '')}"

    # A→B→C：每次续接都固定原任务根，不新增 task 身份
    for _ in range(2):
        _set_script(gateway_stack, final_text="已记录。", consolidation_text=EMPTY_BATCH)
        await _new_conversation(gateway_stack)
        step = await _started_run(gateway_stack, "继续上次任务")
        await _wait_run_terminal(step)
        await _wait_job_terminal(step)
        rows = await _task_state_rows(gateway_stack)
        assert [row["memory_key"] for row in rows] == [root_key], rows

    # 完成：续接提炼声明 task_completed → 原 key 退休，无旧未决残留
    _set_script(gateway_stack, final_text="已记录。", consolidation_text=COMPLETED_BATCH)
    await _new_conversation(gateway_stack)
    completing = await _started_run(gateway_stack, "继续上次任务")
    await _wait_run_terminal(completing)
    await _wait_job_terminal(completing)
    rows = await _task_state_rows(gateway_stack)
    assert [row["memory_key"] for row in rows] == [root_key], rows
    assert rows[0]["is_deleted"] is True and rows[0]["retirement_reason"] == "TASK_COMPLETE", rows

    # 大 checkpoint：整条装不下时投递有界引用（omitted），不静默丢
    long_title = "长任务" + uuid.uuid4().hex[:4]
    #: goal 取契约上限（256 字符 = 768 字节）：整条渲染超出 1024 字节预算，但引用行仍装得下。
    long_goal = "补充" * 128
    _set_script(
        gateway_stack,
        final_text="已记录。",
        consolidation_text=_task_batch(title=long_title, goal=long_goal),
    )
    run_d = await _started_run(gateway_stack, "再给我记一条长线索")
    job_d = await _wait_job_terminal(run_d)
    assert job_d["status"] == "DONE", (job_d, await _task_state_rows(gateway_stack))
    long_key = f"task.{(await _conversation_of(run_d)).replace('-', '')}"
    long_rows = await _memory_rows(gateway_stack, long_key)
    assert len(long_rows) == 1 and long_rows[0]["is_deleted"] is False, (long_key, long_rows)
    reference_revision = await _override_memory_settings(gateway_stack, max_injected_bytes=1024)
    try:
        _set_script(gateway_stack, final_text="已记录。", consolidation_text=EMPTY_BATCH)
        await _new_conversation(gateway_stack)
        large = await _started_run(gateway_stack, "继续上次任务")
        await _wait_run_terminal(large)
        block = _system_text(_last_business_request(gateway_stack))
        assert '"omitted":true' in block, (block, await _task_state_rows(gateway_stack))
        assert long_title in block and long_goal not in block, block
    finally:
        await _drop_platform_setting(gateway_stack, reference_revision)

    # 引用也装不下（注入字节预算压到 256）：整块省略且 bytes dropped 计数可见
    dropped_before = _counters(gateway_stack).get('memory_inject_dropped_total{reason="bytes"}', 0.0)
    tiny_revision = await _override_memory_settings(gateway_stack, max_injected_bytes=256)
    try:
        _set_script(gateway_stack, final_text="已记录。", consolidation_text=EMPTY_BATCH)
        await _new_conversation(gateway_stack)
        tiny = await _started_run(gateway_stack, "继续上次任务")
        await _wait_run_terminal(tiny)
        block = _system_text(_last_business_request(gateway_stack))
        assert "task_state" not in block and long_title not in block, block
        dropped_after = _counters(gateway_stack).get(
            'memory_inject_dropped_total{reason="bytes"}', 0.0
        )
        assert dropped_after > dropped_before, (dropped_before, dropped_after)
    finally:
        await _drop_platform_setting(gateway_stack, tiny_revision)


async def test_e06_memory_redesign_sigkill_claim_owner_and_successor_takeover(
    gateway_stack: GatewayStack,
) -> None:
    """SIGKILL claim 执行者，另一实例接管；故障与成功可见；持久写入效果最多一次。"""
    await _reset_memories(gateway_stack)
    first = gateway_stack.processes["runtime"]
    second = gateway_stack.processes["runtime-2"]
    # 只留一个实例可 claim，让 owner 可确定（真实进程，不是 mock）
    second.stop()
    try:
        _set_script(
            gateway_stack,
            final_text="已记录。",
            consolidation_text=_task_batch(title="崩溃接管", goal="验证接管后效果最多一次"),
            delay_ms=15000,
        )
        await _wait_for_gateway_ws(gateway_stack)
        # 新会话：本 job 无同会话前序（因果序会挡住被前序 PENDING/RETRY job 挡住的接管场景）
        await _new_conversation(gateway_stack)
        run_id = await _started_run(gateway_stack, "记一条会被接管的任务线索")
        await _wait_run_terminal(run_id)
        await _wait_for(
            lambda: _scalar(
                "SELECT count(*) FROM runtime.memory_consolidation_job"
                " WHERE run_id = :run_id AND status = 'RUNNING' AND lease_owner LIKE :owner",
                {"run_id": uuid.UUID(run_id), "owner": f"{RUNTIME_ONE}%"},
            ),
            what=f"真实 job 未被 Runtime 实例 claim: {await _job_row(run_id)}",
        )
        claimed = await _job_row(run_id)
        assert claimed is not None and str(claimed["lease_owner"]).startswith(RUNTIME_ONE), claimed
        assert claimed["lease_until"] is not None

        # SIGKILL：claim 执行者在提炼模型调用期间被强杀（不是优雅停止）
        process = first._process  # noqa: SLF001 - 真实进程句柄，SIGKILL 是场景本体
        assert process is not None
        process.kill()
        process.wait(timeout=10)
        second.start()
        try:
            await _wait_for(
                lambda: _scalar(
                    "SELECT count(*) FROM runtime.memory_consolidation_job"
                    " WHERE run_id = :run_id AND lease_owner LIKE :owner",
                    {"run_id": uuid.UUID(run_id), "owner": f"{RUNTIME_TWO}%"},
                ),
                what="租约过期后另一实例未接管",
            )
            taken = await _job_row(run_id)
            assert taken is not None and taken["claim_epoch"] > claimed["claim_epoch"], (claimed, taken)
            done = await _wait_job_terminal(run_id)
            assert done["status"] == "DONE", done
            expected_key = f"task.{(await _conversation_of(run_id)).replace('-', '')}"
            rows = await _memory_rows(gateway_stack, expected_key)
            assert len(rows) == 1 and rows[0]["author"] == "AGENT", rows
            history = await _history_count(gateway_stack, expected_key)
            assert history == 1, f"接管后持久写入效果必须最多一次，实际 history={history}"
            assert int(
                await _scalar(
                    "SELECT count(*) FROM runtime.memory_consolidation_job WHERE run_id = :run_id",
                    {"run_id": uuid.UUID(run_id)},
                )
                or 0
            ) == 1
        finally:
            first.start()
    finally:
        if second._process is None:  # noqa: SLF001 - 恢复模块级栈的完整实例集合
            second.start()
        _set_script(gateway_stack, final_text="pong", consolidation_text="", delay_ms=0)
