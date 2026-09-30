"""[S-05][S-06][S-09] 黄金旅程验收（E2E）：绑定、流式与中断恢复/取消。

真实边界（不 mock、不拦截 HTTP）：**真实 WeCom WS 渠道探针（真实 wss://）→ 真实 IM Gateway
子进程 → 真实 Console/Runtime/Worker 子进程 → 真实 PostgreSQL/Redis**。栈由
`start_gateway_stack` 起（与 `tests/acceptance/im_gateway` 同一套真实链路），本模块只按黄金
旅程的断言口径驱动它；收尾逆序停进程、清租户、停 WS 探针。

覆盖：
- **S-05 绑定**：未绑定 external_user 经 WS 推 `/bind <code>` → 真实回复「绑定成功」；
  `control.channel_identity` 稳定映射到种子 `platform_user_id`（`is_deleted=false`）；
  `control.bind_code.status='USED'`；`control.agent_access_grant` 行数**不变**（不自动授予 Agent）；
  同一 external_user 再次发消息后，新建 Run 的 `user_id` 仍是同一 PlatformUser（身份稳定）。
- **S-06 流式**：已绑定用户发普通对话 → 真实 WS 出站收到流式增量帧；`runtime.canonical_event`
  首事件 `run.created`、`message.delta` 按 `seq` 严格递增、终态 `run.completed`；
  封套 `{run_id,seq,timestamp,type,data}` 由**落库值**重建核对（seq/timestamp 取持久值，
  不按连接自增）。`: heartbeat` 是注释帧、不落 `canonical_event`，因此不占 seq。
- **S-09 中断/恢复/取消**：`WAITING_INPUT` Run 经同会话再次发言恢复（`run.created.data.resumed
  is True`、interrupt `RESOLVED`、`seq > 1` 即不重排）；运行中 Run `cancel-active` 返回
  `CANCELLING` 并协作走到终态 `CANCELLED`；`WAITING_INPUT` 上 `cancel-active` 走**直接 CAS**
  返回 `CANCELLED`；无活跃 Run 返回 `NO_ACTIVE_RUN`(404)。

如实登记的边界（不冒充覆盖）：
- **「Browser」臂在本需求内不可执行**：本仓前端没有 SSE 消费面（自建流式页面属 design
  Out of Scope 的业务功能）。本模块链路终点是**真实 WS 渠道探针**（Gateway 出站
  `aibot_respond_msg` 流式帧）与**落库的 canonical_event**；浏览器渲染不在此覆盖。
- `STREAM_TIMEOUT_SEC` 的「有界失败」分支：该常量是 Gateway 模块常量
  （`muad_im_gateway/application/runtime_client.py`），**无 env 覆盖**，子进程形态无法注入小值
  ⇒ 由 `tests/gateway/test_runtime_client.py`（monkeypatch）承接，本模块不覆盖。
- 企业微信真机重连（E-10）为 manual，不在本模块。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from muad_common import SharedSettings
from muad_console_platform.application.channel_service import hash_bind_code
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    BOT_SECRET,
    CHAT_ID,
    GatewayStack,
    clear_engine_caches,
    purge_tenant,
    require,
    restart_process,
    start_gateway_stack,
    stop_gateway_stack,
)
from tests.e2e.wecom_probe_app import WeComProbe, frame_text

pytestmark = pytest.mark.e2e

REPLY_TIMEOUT_SEC = 45.0
STREAM_TIMEOUT_SEC = 90.0
BIND_SUCCESS_TEXT = "绑定成功"
RUNS_PATH = "/v1/runs"
CANCEL_ACTIVE_PATH = "/v1/runs/cancel-active"
LLM_DELAY_ENV = "OPENAI_PROBE_DELAY_MS"
LLM_SLOW_MS = "3000"


# --------------------------------------------------------------------------- #
# 真实 PostgreSQL 读写（与兄弟套件同口径：不走 ORM，直接回读盘面）
# --------------------------------------------------------------------------- #


async def _scalar(statement: str, params: dict[str, Any]) -> Any:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(statement), params)
    finally:
        await engine.dispose()


async def _rows(statement: str, params: dict[str, Any]) -> list[Any]:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text(statement), params)
            return list(result.all())
    finally:
        await engine.dispose()


async def _execute(statement: str, params: dict[str, Any]) -> None:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.begin() as connection:
            await connection.execute(text(statement), params)
    finally:
        await engine.dispose()


async def _await_value(
    producer: Callable[[], Any],
    *,
    what: str,
    timeout: float = STREAM_TIMEOUT_SEC,
) -> Any:
    """有界等待真实盘面达到条件（不做无界 sleep；协程与同步取值都支持）。"""
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = producer()
        if inspect.isawaitable(last):
            last = await last
        if last:
            return last
        await asyncio.sleep(0.2)
    raise AssertionError(f"{what}（最后观测：{last!r}）")


# --------------------------------------------------------------------------- #
# 真实链路栈：WS 探针 + Gateway/Console/Runtime×2/Worker/LLM 探针
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
async def gateway_stack(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[GatewayStack]:
    settings = SharedSettings()
    require("DATABASE_URL", settings.database_url)
    require("REDIS_URL", settings.redis_url)

    clear_engine_caches()
    root = tmp_path_factory.mktemp("dfx-journeys")
    probe = WeComProbe(expected_bots={BOT_ID: BOT_SECRET})
    await probe.start()
    stack, processes = start_gateway_stack(
        root, ws_probe_url=probe.ws_url, ws_ca_file=str(probe.cert_path)
    )
    stack.ws_probe = probe
    try:
        yield stack
    finally:
        stop_gateway_stack(processes)
        await purge_tenant()
        await probe.stop()
        clear_engine_caches()


@pytest.fixture()
async def bind_code(gateway_stack: GatewayStack) -> str:
    """真实 PG：种一个**有效**绑定码（明文只在本用例内使用，库内只存哈希）。"""
    code = f"DFXJ{uuid.uuid4().hex[:8].upper()}"
    await _execute(
        "INSERT INTO control.bind_code "
        "(id, tenant_id, platform_user_id, code_hash, status, expires_at, created_by) "
        "VALUES (:id, :t, :u, :h, 'ACTIVE', now() + interval '10 minutes', :u)",
        {
            "id": uuid.uuid4(),
            "t": gateway_stack.tenant_id,
            "u": gateway_stack.platform_user_id,
            "h": hash_bind_code(code),
        },
    )
    return code


# --------------------------------------------------------------------------- #
# 真实 WS 驱动与回读
# --------------------------------------------------------------------------- #


async def _wait_gateway_ws(stack: GatewayStack) -> None:
    probe = stack.ws_probe
    assert probe is not None, "WS 探针未接入栈"
    await _await_value(
        lambda: bool(probe.frames_of("aibot_subscribe")) and bool(probe.connections),
        what="Gateway 未在超时内连上真实 WS 探针",
        timeout=REPLY_TIMEOUT_SEC,
    )


async def _push(
    stack: GatewayStack,
    *,
    text: str,
    external_user_id: str | None = None,
) -> str:
    probe = stack.ws_probe
    assert probe is not None
    message_id = f"msg-{uuid.uuid4().hex[:10]}"
    await probe.push_message(
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id=external_user_id or stack.bound_external_user_id,
        text=text,
        reply_id=f"req-{message_id}",
        chat_id=CHAT_ID,
    )
    return message_id


def _frames(stack: GatewayStack, cmd: str) -> list[dict[str, Any]]:
    probe = stack.ws_probe
    assert probe is not None
    return [item.frame for item in probe.received if item.frame.get("cmd") == cmd]


def _reply_texts(stack: GatewayStack) -> list[str]:
    """出站**文本**：会话内回复走 `aibot_respond_msg`（可能是流式体、也可能是纯文本体），
    主动投递走 `aibot_send_msg`（纯文本体）——两种载体都要读，否则会漏掉文本回执。"""
    return [
        frame_text(frame)
        for cmd in ("aibot_respond_msg", "aibot_send_msg")
        for frame in _frames(stack, cmd)
    ]


def _stream_chunks(stack: GatewayStack) -> list[str]:
    """真实 WS 出站流式帧的增量文本（Gateway 把 Runtime SSE 增量转发给渠道）。"""
    chunks: list[str] = []
    for frame in _frames(stack, "aibot_respond_msg"):
        stream = (frame.get("body") or {}).get("stream") or {}
        content = stream.get("content")
        if isinstance(content, str) and content:
            chunks.append(content)
    return chunks


async def _canonical_events(run_id: str) -> list[dict[str, Any]]:
    """按落库 seq 升序回读 canonical_event（封套的持久来源）。"""
    rows = await _rows(
        "SELECT run_id, seq, stream_type, payload_json, create_time "
        "FROM runtime.canonical_event WHERE run_id = :r ORDER BY seq",
        {"r": uuid.UUID(run_id)},
    )
    return [
        {
            "run_id": str(row[0]),
            "seq": int(row[1]),
            "stream_type": row[2],
            "payload": row[3] or {},
            "timestamp": row[4],
        }
        for row in rows
    ]


async def _completed_events(run_id: str) -> list[dict[str, Any]]:
    events = await _canonical_events(run_id)
    if events and events[-1]["stream_type"] == "run.completed":
        return events
    return []


async def _latest_run_id(tenant_id: str) -> str:
    value = await _scalar(
        "SELECT id::text FROM runtime.run_record WHERE tenant_id = :t "
        "ORDER BY create_time DESC LIMIT 1",
        {"t": tenant_id},
    )
    assert value, "本租户没有真实 Run"
    return str(value)


def _payload(stack: GatewayStack, text: str, *, conversation_id: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agent_id": str(stack.agent_id),
        "platform_user_id": str(stack.platform_user_id),
        "channel": {
            "type": "WECOM",
            "bot_id": stack.bot_id,
            "external_conversation_id": stack.chat_id,
        },
        "message": {"id": f"msg-{uuid.uuid4().hex[:10]}", "type": "text", "text": text},
    }
    if conversation_id is not None:
        payload["conversation_id"] = str(conversation_id)
    return payload


def _runtime_headers(stack: GatewayStack) -> dict[str, str]:
    return {"X-Tenant-Id": stack.tenant_id}


def _parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        events.append(json.loads(line[len("data:") :].strip()))
    return events


async def _seed_waiting_run(stack: GatewayStack) -> tuple[str, str]:
    """真实 PG 播种 `WAITING_INPUT` Run + 等待中的 interrupt，并复制真实快照。

    快照内容取自本租户最近一次**真实执行**留下的 `runtime_snapshot`（恢复路径需要快照，
    与兄弟套件同口径），因此恢复仍走真实快照解析而非替身。
    """
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
                    "VALUES (:id, :t, :c, :u, :a, 'WAITING_INPUT', 'seeded', :trace, false)"
                ),
                {
                    "id": run_id,
                    "t": stack.tenant_id,
                    "c": conversation_id,
                    "u": stack.platform_user_id,
                    "a": stack.agent_id,
                    "trace": uuid.uuid4().hex,
                },
            )
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
    return str(run_id), str(conversation_id)


async def _warm_up_real_run(stack: GatewayStack) -> str:
    """先跑一次**真实**执行，使本租户存在可供恢复路径复制的真实快照。"""
    await _wait_gateway_ws(stack)
    await _push(stack, text="S-09 预热：制造一次真实执行")
    return str(
        await _await_value(
            lambda: _terminal_run_id(stack.tenant_id), what="预热 Run 未在超时内到达终态"
        )
    )


async def _terminal_run_id(tenant_id: str) -> str:
    rows = await _rows(
        "SELECT id::text, status FROM runtime.run_record WHERE tenant_id = :t "
        "ORDER BY create_time DESC LIMIT 1",
        {"t": tenant_id},
    )
    if rows and str(rows[0][1]) in {"COMPLETED", "FAILED"}:
        return str(rows[0][0])
    return ""


# --------------------------------------------------------------------------- #
# S-05：绑定
# --------------------------------------------------------------------------- #


async def test_s05_bind_maps_identity_stably_without_granting_agent(
    gateway_stack: GatewayStack, bind_code: str
) -> None:
    stack = gateway_stack
    await _wait_gateway_ws(stack)
    grants_before = await _scalar(
        "SELECT count(*) FROM control.agent_access_grant WHERE user_id = :u",
        {"u": stack.platform_user_id},
    )
    # 种子里已有「已绑定用户」的身份行，故按**增量**断言本次绑定新增的那一行
    identities_before = await _scalar(
        "SELECT count(*) FROM control.channel_identity WHERE tenant_id = :t",
        {"t": stack.tenant_id},
    )

    await _push(stack, text=f"/bind {bind_code}", external_user_id=stack.unbound_external_user_id)
    await _await_value(
        lambda: BIND_SUCCESS_TEXT in _reply_texts(stack),
        what=f"未在超时内看到绑定回复；实际 {_reply_texts(stack)!r}",
        timeout=REPLY_TIMEOUT_SEC,
    )

    # 真实库回读：身份映射 + 绑定码被消费 + 授权表零新增（不自动授予 Agent）
    identities_after = await _scalar(
        "SELECT count(*) FROM control.channel_identity WHERE tenant_id = :t",
        {"t": stack.tenant_id},
    )
    assert identities_after == int(identities_before or 0) + 1, (
        f"本次绑定未新增唯一身份行：{identities_before} → {identities_after}"
    )
    newest = await _rows(
        "SELECT platform_user_id::text, is_deleted FROM control.channel_identity "
        "WHERE tenant_id = :t ORDER BY create_time DESC LIMIT 1",
        {"t": stack.tenant_id},
    )
    assert newest[0][0] == str(stack.platform_user_id), "新身份未映射到种子 PlatformUser"
    assert newest[0][1] is False, "新身份行不应是软删行"
    used = await _scalar(
        "SELECT count(*) FROM control.bind_code WHERE tenant_id = :t AND status = 'USED'",
        {"t": stack.tenant_id},
    )
    assert used and int(used) >= 1, "绑定码未被消费（status 仍非 USED）"
    grants_after = await _scalar(
        "SELECT count(*) FROM control.agent_access_grant WHERE user_id = :u",
        {"u": stack.platform_user_id},
    )
    assert grants_after == grants_before, "绑定不得自动授予 Agent（授权表出现新增行）"

    # 「再次对话」：同一 external_user 发消息后，新建 Run 的 user_id 仍是同一 PlatformUser
    await _push(stack, text="绑定后继续对话", external_user_id=stack.unbound_external_user_id)
    run_user = await _await_value(
        lambda: _latest_run_user(stack.tenant_id), what="再次对话未创建真实 Run"
    )
    assert run_user == str(stack.platform_user_id), "身份映射不稳定（Run 归属了别的用户）"


async def _latest_run_user(tenant_id: str) -> str:
    value = await _scalar(
        "SELECT user_id::text FROM runtime.run_record WHERE tenant_id = :t "
        "ORDER BY create_time DESC LIMIT 1",
        {"t": tenant_id},
    )
    return str(value or "")


# --------------------------------------------------------------------------- #
# S-06：流式
# --------------------------------------------------------------------------- #


async def test_s06_stream_reply_envelope_and_monotonic_seq(gateway_stack: GatewayStack) -> None:
    stack = gateway_stack
    await _wait_gateway_ws(stack)
    chunks_before = len(_stream_chunks(stack))

    await _push(stack, text="S-06 普通会话流式")
    await _await_value(
        lambda: len(_stream_chunks(stack)) > chunks_before,
        what="未在超时内收到真实 WS 流式增量帧",
        timeout=REPLY_TIMEOUT_SEC,
    )

    run_id = await _latest_run_id(stack.tenant_id)
    events = await _await_value(
        lambda: _completed_events(run_id), what="Run 未落终态 run.completed"
    )
    # `canonical_event` 同时承载业务事件（`stream_type IS NULL`，如 USER_MESSAGE/CANCEL）
    # 与流式事件；封套断言只对后者，seq 单调性对整条事件流成立。
    streamed = [item for item in events if item["stream_type"]]

    # 首事件与终态（按落库 seq 排序）
    assert streamed[0]["stream_type"] == "run.created", streamed[0]
    assert streamed[-1]["stream_type"] == "run.completed", streamed[-1]

    # seq 严格单调；message.delta 的 seq 递增（顺序到达）
    seqs = [item["seq"] for item in events]
    assert seqs == sorted(set(seqs)), f"seq 非严格单调：{seqs}"
    deltas = [item for item in streamed if item["stream_type"] == "message.delta"]
    assert deltas, "没有任何 message.delta 落库"
    assert [item["seq"] for item in deltas] == sorted(item["seq"] for item in deltas)

    # 封套四要素 + data 均由落库值重建（禁按连接自增）
    for item in streamed:
        assert item["run_id"], item
        assert isinstance(item["seq"], int)
        assert item["timestamp"] is not None, f"持久时间戳缺失：{item}"
        assert isinstance(item["payload"], dict)
    assert streamed[-1]["payload"].get("status") in {"COMPLETED", "FAILED"}, streamed[-1]
    final_status = await _scalar(
        "SELECT status FROM runtime.run_record WHERE id = :r", {"r": uuid.UUID(run_id)}
    )
    assert final_status in {"COMPLETED", "FAILED"}, final_status


# --------------------------------------------------------------------------- #
# S-09：中断 / 恢复 / 取消
# --------------------------------------------------------------------------- #


async def test_s09_waiting_input_resume_continues_without_seq_reset(
    gateway_stack: GatewayStack,
) -> None:
    stack = gateway_stack
    await _warm_up_real_run(stack)
    run_id, conversation_id = await _seed_waiting_run(stack)
    status = await _scalar(
        "SELECT status FROM runtime.run_record WHERE id = :r", {"r": uuid.UUID(run_id)}
    )
    assert status == "WAITING_INPUT", status

    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT_SEC) as client:
        resumed = await client.post(
            f"{stack.runtime_url}{RUNS_PATH}",
            json=_payload(stack, "继续", conversation_id=conversation_id),
            headers=_runtime_headers(stack),
        )
    assert resumed.status_code == 200, resumed.text
    events = _parse_sse(resumed.text)
    assert events and events[0]["type"] == "run.created", events[:1]
    assert events[0]["data"].get("resumed") is True, events[0]
    assert events[0]["run_id"] == run_id, "恢复必须续同一 Run"
    assert int(events[0]["seq"]) > 1, f"seq 从 1 重排了：{events[0]['seq']}"
    assert events[-1]["type"] == "run.completed", events[-1]

    interrupt = await _scalar(
        "SELECT status FROM runtime.run_interrupt WHERE run_id = :r", {"r": uuid.UUID(run_id)}
    )
    assert interrupt == "RESOLVED", f"interrupt 未随恢复解除：{interrupt}"

    persisted = await _canonical_events(run_id)
    seqs = [item["seq"] for item in persisted]
    assert seqs and seqs == sorted(set(seqs)), f"恢复后 seq 重排：{seqs}"


async def test_s09_cancel_running_is_cooperative_and_terminal(
    gateway_stack: GatewayStack,
) -> None:
    """运行中的 Run：`cancel-active` 返回 `CANCELLING`，随后协作走到终态 `CANCELLED`。

    为了让 Run 在执行中可被取消，把真实 LLM 探针的响应延迟调大（改进程 env 后重启该探针，
    仍是真实 HTTP 探针，不是替身）；用例结束按原 env 重启还原。
    """
    stack = gateway_stack
    probe_process = stack.processes["llm-probe"]
    original_delay = probe_process.env.get(LLM_DELAY_ENV)
    probe_process.env[LLM_DELAY_ENV] = LLM_SLOW_MS
    restart_process(probe_process)
    try:
        async with httpx.AsyncClient(timeout=STREAM_TIMEOUT_SEC) as client:
            streamed: list[dict[str, Any]] = []
            async with client.stream(
                "POST",
                f"{stack.runtime_url}{RUNS_PATH}",
                json=_payload(stack, "需要一点时间的任务"),
                headers=_runtime_headers(stack),
            ) as response:
                assert response.status_code == 200, await response.aread()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    streamed.append(json.loads(line[len("data:") :].strip()))
                    if len(streamed) == 1:
                        assert streamed[0]["type"] == "run.created", streamed[0]
                        cancelled = await client.post(
                            f"{stack.runtime_url}{CANCEL_ACTIVE_PATH}",
                            json={
                                "agent_id": str(stack.agent_id),
                                "platform_user_id": str(stack.platform_user_id),
                            },
                            headers=_runtime_headers(stack),
                        )
                        assert cancelled.status_code == 200, cancelled.text
                        assert cancelled.json()["data"]["status"] == "CANCELLING", cancelled.text
    finally:
        if original_delay is None:
            probe_process.env.pop(LLM_DELAY_ENV, None)
        else:
            probe_process.env[LLM_DELAY_ENV] = original_delay
        restart_process(probe_process)

    assert streamed, "未收到任何流式事件"
    assert streamed[-1]["type"] == "run.completed", streamed[-1]
    assert streamed[-1]["data"]["status"] == "CANCELLED", streamed[-1]

    run_id = str(streamed[0]["run_id"])
    row = await _rows(
        "SELECT status, cancel_requested FROM runtime.run_record WHERE id = :r",
        {"r": uuid.UUID(run_id)},
    )
    assert row[0][0] == "CANCELLED" and row[0][1] is True, row

    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        SharedSettings().require_redis_url(), decode_responses=True
    )
    try:
        assert await client.exists(f"run:cancel:{run_id}") == 1, "取消未留下 Redis hint"
    finally:
        await client.aclose()


async def test_s09_cancel_waiting_input_is_cas_cancelled(gateway_stack: GatewayStack) -> None:
    stack = gateway_stack
    await _warm_up_real_run(stack)
    run_id, _ = await _seed_waiting_run(stack)

    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT_SEC) as client:
        cancelled = await client.post(
            f"{stack.runtime_url}{CANCEL_ACTIVE_PATH}",
            json={
                "agent_id": str(stack.agent_id),
                "platform_user_id": str(stack.platform_user_id),
            },
            headers=_runtime_headers(stack),
        )
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()["data"]
    assert body["run_id"] == run_id
    # WAITING_INPUT 直接 CAS 取消：返回终态而非 CANCELLING
    assert body["status"] == "CANCELLED", body

    # 该路径一次写成终态，不经过「请求取消 → 执行者协作」两段：实测 `cancel_requested`
    # 保持 false（与运行中协作取消不同），故此处不断言该列。
    row = await _rows(
        "SELECT status FROM runtime.run_record WHERE id = :r", {"r": uuid.UUID(run_id)}
    )
    assert row[0][0] == "CANCELLED", row
    interrupt = await _scalar(
        "SELECT status FROM runtime.run_interrupt WHERE run_id = :r", {"r": uuid.UUID(run_id)}
    )
    assert interrupt == "CANCELLED", interrupt
    business = await _rows(
        "SELECT event_type FROM runtime.canonical_event "
        "WHERE run_id = :r AND stream_type IS NULL ORDER BY seq",
        {"r": uuid.UUID(run_id)},
    )
    assert "CANCEL" in {str(item[0]) for item in business}, business
    events = await _await_value(
        lambda: _completed_events(run_id), what="取消后未落终态 run.completed"
    )
    assert events[-1]["payload"].get("status") == "CANCELLED", events[-1]


async def test_s09_cancel_active_without_active_run_returns_no_active_run(
    gateway_stack: GatewayStack,
) -> None:
    stack = gateway_stack
    # 前置用例已把该用户的 Run 全部置终态
    await _await_value(
        lambda: _no_active_run(stack), what="无活跃 Run 的前置条件未满足"
    )


async def _no_active_run(stack: GatewayStack) -> bool:
    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT_SEC) as client:
        response = await client.post(
            f"{stack.runtime_url}{CANCEL_ACTIVE_PATH}",
            json={
                "agent_id": str(stack.agent_id),
                "platform_user_id": str(stack.platform_user_id),
            },
            headers=_runtime_headers(stack),
        )
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "NO_ACTIVE_RUN", response.text
    return True
