"""B-125 / S-03 / E-04 / RULE-10: 流式回复、Resume、取消与 Snapshot（E2E）。

不得 Mock 的真实边界：真实 WeCom 协议 WS（官方 SDK）→ 真实 Gateway → 真实 Runtime
HTTP/SSE → 真实 PostgreSQL（canonical_event 序号、Run 终态、Snapshot）→ 真实出站回复。
验收类不制造 RED（Baseline）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from muad_common import SharedSettings
from muad_contracts import ChannelContext, MessageInput, RunRequest
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.main import app as gateway_app
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


def _catalog_message(code: str) -> str:
    return str(gateway_app.state.message_catalog.message(code, "zh-CN"))
WAIT_TIMEOUT_SEC = 150.0
REPLY_TIMEOUT_SEC = 120.0


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
        body = item.frame.get("body") or {}
        if item.frame.get("cmd") == "aibot_respond_msg":
            texts.append(str((body.get("stream") or {}).get("content") or ""))
        elif item.frame.get("cmd") == "aibot_send_msg":
            texts.append(str((body.get("text") or {}).get("content") or ""))
    return texts


async def _push(stack: GatewayStack, *, text: str, message_id: str | None = None) -> str:
    probe = stack.ws_probe
    assert probe is not None
    resolved_id = message_id or f"stream-{uuid.uuid4().hex[:8]}"
    await probe.push_message(  # type: ignore[attr-defined]
        bot_id=BOT_ID,
        message_id=resolved_id,
        external_user_id=BOUND_EXTERNAL_USER_ID,
        text=text,
        reply_id=f"req-{resolved_id}",
        chat_id=CHAT_ID,
    )
    return resolved_id


async def _seed_run(
    stack: GatewayStack, *, status: str, with_interrupt: bool = False
) -> tuple[str, str, str]:
    """真实 PG：为栈内用户种入指定状态的 Run（+ 等待中的 interrupt），返回 (run_id, snapshot_id)。"""
    run_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO runtime.conversation "
                    "(id, tenant_id, user_id, agent_id, status, last_seq) "
                    "VALUES (:id, :t, :u, :a, 'ACTIVE', 0)"
                ),
                {
                    "id": conversation_id,
                    "t": stack.tenant_id,
                    "u": stack.platform_user_id,
                    "a": stack.agent_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO runtime.run_record "
                    "(id, tenant_id, conversation_id, user_id, agent_id, status, input_text, "
                    "trace_id, cancel_requested) "
                    "VALUES (:id, :t, :c, :u, :a, :status, 'seeded', :trace, false)"
                ),
                {
                    "id": run_id,
                    "t": stack.tenant_id,
                    "c": conversation_id,
                    "u": stack.platform_user_id,
                    "a": stack.agent_id,
                    "status": status,
                    "trace": uuid.uuid4().hex,
                },
            )
            if with_interrupt:
                # resume 路径需要快照：复制最近一次真实 Run 的快照内容给本种子 Run
                await connection.execute(
                    text(
                        "INSERT INTO runtime.runtime_snapshot "
                        "(id, tenant_id, run_id, schema_version, agent_revision, model_revision, "
                        "agent_json, model_json, skill_catalog_json, mcp_catalog_json, policy_json, "
                        "prompt_template_version, content_hash) "
                        "SELECT gen_random_uuid(), tenant_id, :r, schema_version, agent_revision, "
                        "model_revision, agent_json, model_json, skill_catalog_json, mcp_catalog_json, "
                        "policy_json, prompt_template_version, content_hash "
                        "FROM runtime.runtime_snapshot WHERE tenant_id = :t "
                        "ORDER BY create_time DESC LIMIT 1"
                    ),
                    {"r": run_id, "t": stack.tenant_id},
                )
                await connection.execute(
                    text(
                        "UPDATE runtime.run_record SET snapshot_id = ("
                        "SELECT id FROM runtime.runtime_snapshot WHERE run_id = :r) WHERE id = :r"
                    ),
                    {"r": run_id},
                )
                await connection.execute(
                    text(
                        "INSERT INTO runtime.run_interrupt "
                        "(tenant_id, run_id, conversation_id, interrupt_type, prompt_text, "
                        "options_json, status) "
                        "VALUES (:t, :r, :c, 'CONFIRM', 'continue?', '[]'::jsonb, 'WAITING')"
                    ),
                    {"t": stack.tenant_id, "r": run_id, "c": conversation_id},
                )
    finally:
        await engine.dispose()
    snapshot_id = await _scalar(
        "SELECT snapshot_id::text FROM runtime.run_record WHERE id = :id", {"id": run_id}
    )
    return str(run_id), str(snapshot_id or ""), str(conversation_id)


async def _wait_gateway_ws(stack: GatewayStack) -> None:
    probe = stack.ws_probe
    assert probe is not None
    await _wait_for(
        lambda: probe.frames_of("aibot_subscribe"),  # type: ignore[attr-defined]
        what="Gateway 未在超时内连上真实 WS 探针",
    )


async def test_s03_stream_reply_has_monotonic_seq_and_completes(
    gateway_stack: GatewayStack,
) -> None:
    await _wait_gateway_ws(gateway_stack)
    before = len(_replies(gateway_stack))
    await _push(gateway_stack, text="S-03 流式回复")
    await _wait_for(
        lambda: len(_replies(gateway_stack)) > before, what="未收到流式回复", timeout=REPLY_TIMEOUT_SEC
    )
    # 授权消息创建了 Run；canonical_event 的 seq 严格单调；run.completed 收尾
    rows = await _rows(
        "SELECT seq, stream_type FROM runtime.canonical_event WHERE run_id = ("
        "SELECT id FROM runtime.run_record WHERE tenant_id = :t "
        "ORDER BY create_time DESC LIMIT 1) ORDER BY seq",
        {"t": gateway_stack.tenant_id},
    )
    assert rows, "未记录 canonical_event"
    seqs = [int(row[0]) for row in rows]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), f"seq 非严格单调：{seqs}"
    stream_types = {str(row[1]) for row in rows if row[1]}
    assert "run.completed" in stream_types, stream_types
    status = await _scalar(
        "SELECT status FROM runtime.run_record WHERE tenant_id = :t ORDER BY create_time DESC LIMIT 1",
        {"t": gateway_stack.tenant_id},
    )
    assert status == "COMPLETED", status


async def test_b125_waiting_input_run_is_auto_resumed_and_snapshot_frozen(
    gateway_stack: GatewayStack,
) -> None:
    await _wait_gateway_ws(gateway_stack)
    # 先跑一次真实 Run：种子的 WAITING_INPUT Run 需要拷贝真实 Snapshot（resume 路径前置）
    await _push(gateway_stack, text="为 resume 用例准备真实快照")
    await _wait_for(
        lambda: _snapshot_count_at_least(gateway_stack, 1),
        what="未产生可供拷贝的真实快照",
        timeout=REPLY_TIMEOUT_SEC,
    )
    run_id, snapshot_id, conversation_id = await _seed_run(
        gateway_stack, status="WAITING_INPUT", with_interrupt=True
    )
    # 真实 Runtime HTTP：显式指向种子会话，验证"存在 WAITING_INPUT Run 即自动 resume"
    client = RuntimeClient(gateway_stack.runtime_url)
    try:
        request = RunRequest(
            agent_id=gateway_stack.agent_id,
            platform_user_id=gateway_stack.platform_user_id,
            conversation_id=uuid.UUID(conversation_id),
            channel=ChannelContext(type="WECOM", bot_id=BOT_ID, external_conversation_id=CHAT_ID),
            message=MessageInput(
                id=f"resume-{uuid.uuid4().hex[:8]}", type="text", text="恢复等待中的运行"
            ),
        )
        seen_run_ids: list[str] = []
        async for event in client.create_run(
            request, tenant_id=gateway_stack.tenant_id, trace_id="trace-b125"
        ):
            if event.type == "run.created":
                seen_run_ids.append(str(event.run_id or (event.data or {}).get("run_id") or ""))
    finally:
        await client.aclose()
    await _wait_for(
        lambda: _is_terminal(run_id),
        what="等待中的 Run 未被自动 resume 到终态",
        timeout=REPLY_TIMEOUT_SEC,
    )
    assert run_id in seen_run_ids, f"resume 未沿用原 Run：{seen_run_ids}"
    runs = await _scalar(
        "SELECT count(*) FROM runtime.run_record WHERE tenant_id = :t AND conversation_id = "
        "(SELECT conversation_id FROM runtime.run_record WHERE id = :id)",
        {"t": gateway_stack.tenant_id, "id": uuid.UUID(run_id)},
    )
    assert int(runs or 0) == 1, "resume 不得新建 Run"
    # Snapshot 沿用（冻结）：resume 不改变该 Run 的 snapshot_id
    snapshot_after = await _scalar(
        "SELECT snapshot_id::text FROM runtime.run_record WHERE id = :id", {"id": uuid.UUID(run_id)}
    )
    assert str(snapshot_after or "") == snapshot_id, "resume 不得更换快照"


async def _run_status(run_id: str) -> str | None:
    value = await _scalar(
        "SELECT status FROM runtime.run_record WHERE id = :id", {"id": uuid.UUID(run_id)}
    )
    return str(value) if value is not None else None


async def _is_terminal(run_id: str) -> bool:
    status = await _run_status(run_id)
    return status in ("COMPLETED", "FAILED", "CANCELLED")


async def _run_ids(stack: GatewayStack) -> set[str]:
    rows = await _rows(
        "SELECT id::text FROM runtime.run_record WHERE tenant_id = :t", {"t": stack.tenant_id}
    )
    return {str(row[0]) for row in rows}


async def _seed_conversation(stack: GatewayStack) -> str:
    """种入一条全新的 (agent,user) 会话并使其成为最新。

    WS 推送不带 conversation_id，Runtime 按 `update_time` 最新会话解析
    （agent-runtime `_resolve_conversation`）；模块级共享租户里前序用例种下的会话
    可能仍挂着活跃 Run，故本用例自备会话以保证推送落在自己的会话内。
    """
    conversation_id = uuid.uuid4()
    await _execute(
        "INSERT INTO runtime.conversation "
        "(id, tenant_id, user_id, agent_id, status, last_seq) "
        "VALUES (:id, :t, :u, :a, 'ACTIVE', 0)",
        {
            "id": conversation_id,
            "t": stack.tenant_id,
            "u": stack.platform_user_id,
            "a": stack.agent_id,
        },
    )
    return str(conversation_id)


async def _run_conversation_id(run_id: str) -> str:
    value = await _scalar(
        "SELECT conversation_id::text FROM runtime.run_record WHERE id = :id",
        {"id": uuid.UUID(run_id)},
    )
    return str(value or "")


async def _new_run_id(stack: GatewayStack, before: set[str]) -> str | None:
    """本次推送新建的唯一 Run（依赖模块级共享租户，故按集合差而非租户聚合判定）。"""
    fresh = sorted(await _run_ids(stack) - before)
    if len(fresh) > 1:
        raise AssertionError(f"本次推送新建了多个 Run：{fresh}")
    return fresh[0] if fresh else None


async def _snapshot_hash_of(run_id: str) -> str | None:
    value = await _scalar(
        "SELECT content_hash FROM runtime.runtime_snapshot WHERE run_id = :id",
        {"id": uuid.UUID(run_id)},
    )
    return str(value) if value is not None else None


async def test_e04_busy_run_is_rejected_without_new_run(gateway_stack: GatewayStack) -> None:
    await _wait_gateway_ws(gateway_stack)
    run_id, _snapshot, _conversation = await _seed_run(gateway_stack, status="RUNNING")
    before_runs = await _scalar(
        "SELECT count(*) FROM runtime.run_record WHERE tenant_id = :t", {"t": gateway_stack.tenant_id}
    )
    before = len(_replies(gateway_stack))
    await _push(gateway_stack, text="忙碌时的普通消息")
    expected = _catalog_message("RUN_BUSY")
    await _wait_for(
        lambda: any(expected in text for text in _replies(gateway_stack)[before:]),
        what="忙碌未被拒绝为 RUN_BUSY 文案",
        timeout=REPLY_TIMEOUT_SEC,
    )
    replies = "".join(_replies(gateway_stack)[before:])
    assert "/stop" in replies, replies  # 文案提示可用 /stop
    after_runs = await _scalar(
        "SELECT count(*) FROM runtime.run_record WHERE tenant_id = :t", {"t": gateway_stack.tenant_id}
    )
    assert int(after_runs or 0) == int(before_runs or 0), "RUN_BUSY 不得新建 Run"
    assert await _run_status(run_id) == "RUNNING", "原 Run 状态不得被改变"


async def test_b125_new_run_uses_new_configuration_snapshot(gateway_stack: GatewayStack) -> None:
    await _wait_gateway_ws(gateway_stack)
    conversation_id = await _seed_conversation(gateway_stack)
    before_runs = await _run_ids(gateway_stack)
    await _push(gateway_stack, text="配置变更前的运行")
    await _wait_for(
        lambda: _new_run_id(gateway_stack, before_runs),
        what="配置变更前的推送未创建 Run",
        timeout=REPLY_TIMEOUT_SEC,
    )
    first_run_id = await _new_run_id(gateway_stack, before_runs)
    assert first_run_id is not None
    assert await _run_conversation_id(first_run_id) == conversation_id, "推送未落在本用例会话"
    await _wait_for(
        lambda: _is_terminal(first_run_id),
        what="配置变更前的 Run 未到达终态",
        timeout=REPLY_TIMEOUT_SEC,
    )
    first_hash = await _snapshot_hash_of(first_run_id)
    assert first_hash, "配置变更前的 Run 未冻结 Snapshot"
    # 配置变更：Agent revision +1（真实 PG）
    await _execute(
        "UPDATE control.agent_definition SET revision = revision + 1 WHERE id = :id",
        {"id": gateway_stack.agent_id},
    )
    before_second = await _run_ids(gateway_stack)
    await _push(gateway_stack, text="配置变更后的运行")
    await _wait_for(
        lambda: _new_run_id(gateway_stack, before_second),
        what="配置变更后的推送未创建新 Run",
        timeout=REPLY_TIMEOUT_SEC,
    )
    second_run_id = await _new_run_id(gateway_stack, before_second)
    assert second_run_id is not None
    assert await _run_conversation_id(second_run_id) == conversation_id, "新 Run 未沿用本用例会话"
    second_hash = await _snapshot_hash_of(second_run_id)
    assert first_hash != second_hash, (first_hash, second_hash)


async def _snapshot_count_at_least(stack: GatewayStack, expected: int) -> bool:
    return await _snapshot_count(stack) >= expected


async def _snapshot_count(stack: GatewayStack) -> int:
    value = await _scalar(
        "SELECT count(*) FROM runtime.runtime_snapshot WHERE tenant_id = :t", {"t": stack.tenant_id}
    )
    return int(value or 0)


async def test_b125_cleanup_leaves_no_stream_residue(gateway_stack: GatewayStack) -> None:
    await purge_tenant()
    for table in ("runtime.canonical_event", "runtime.run_record", "runtime.runtime_snapshot"):
        assert await count_tenant_rows(table) == 0, table
