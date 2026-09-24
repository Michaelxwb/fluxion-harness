"""B-122 / S-01 / RULE-01 / RULE-04 / RISK-01: 多 Bot 路由与任意 Runtime 实例（E2E）。

不得 Mock 的真实边界：真实本地 WS 探针（官方 SDK 认证与收发）、真实 Gateway/Console/
双 Runtime/Worker 进程、真实 PostgreSQL（bot/identity/run 逐行回读）。验收类不制造 RED。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx
import pytest
from muad_common import SharedSettings
from muad_console_platform.application.channel_service import build_identity_key
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.channel import BotAccount, ChannelIdentity
from muad_contracts import ChannelContext, MessageInput, RunRequest
from muad_im_gateway.application.runtime_client import RuntimeClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    CHAT_ID,
    BOUND_EXTERNAL_USER_ID,
    GatewayStack,
    count_tenant_rows,
    purge_tenant,
)

BOT2_ID = "e2e-im-bot-2"
BOT2_SECRET = "e2e-im-bot-2-secret"
BOT2_EXTERNAL_USER = "e2e-im-ext-bound-2"
DISABLED_BOT_ID = "e2e-im-bot-disabled"
RESOLVE_PATH = "/internal/channel/resolve"
WAIT_TIMEOUT_SEC = 90.0
SNAPSHOT_POLL_WAIT_SEC = 45.0
PUSH_REPLY_TIMEOUT_SEC = 60.0


async def _scalar(statement: str, params: dict[str, object]) -> object:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(statement), params)
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


@pytest.fixture(scope="module")
async def extra_bots(gateway_stack: GatewayStack) -> Any:
    """真实 PG：第二个 enabled bot（同 Agent、同平台用户）与一个 disabled bot。"""
    suffix = uuid.uuid4().hex[:6]
    async with get_session_factory()() as session:
        enabled = BotAccount(
            tenant_id=gateway_stack.tenant_id,
            channel="WECOM",
            name="IM E2E Bot 2",
            bot_id=BOT2_ID,
            secret=BOT2_SECRET,
            agent_id=gateway_stack.agent_id,
            enabled=True,
        )
        disabled = BotAccount(
            tenant_id=gateway_stack.tenant_id,
            channel="WECOM",
            name="IM E2E Disabled Bot",
            bot_id=DISABLED_BOT_ID,
            secret=f"disabled-{suffix}",
            agent_id=gateway_stack.agent_id,
            enabled=False,
        )
        session.add_all([enabled, disabled])
        await session.flush()
        session.add(
            ChannelIdentity(
                tenant_id=gateway_stack.tenant_id,
                channel="WECOM",
                identity_key=build_identity_key("WECOM", BOT2_ID, BOT2_EXTERNAL_USER),
                external_user_id=BOT2_EXTERNAL_USER,
                bot_account_id=enabled.id,
                platform_user_id=gateway_stack.platform_user_id,
            )
        )
        await session.commit()
    yield {"bot2": BOT2_ID, "disabled": DISABLED_BOT_ID}


def _frames_for_bot(stack: GatewayStack, bot_id: str) -> list[str]:
    """该 bot 连接上的出站内容（有 reply_id 时回复走流式帧 aibot_respond_msg）。"""
    probe = stack.ws_probe
    assert probe is not None
    texts: list[str] = []
    for item in probe.received:  # type: ignore[attr-defined]
        if probe.connection_bots.get(item.connection) != bot_id:
            continue
        body = item.frame.get("body") or {}
        if item.frame.get("cmd") == "aibot_respond_msg":
            texts.append(str((body.get("stream") or {}).get("content") or ""))
        elif item.frame.get("cmd") == "aibot_send_msg":
            texts.append(str((body.get("text") or {}).get("content") or ""))
    return texts


async def _push(stack: GatewayStack, *, bot_id: str, external_user_id: str, text: str) -> str:
    probe = stack.ws_probe
    assert probe is not None
    message_id = f"routing-{uuid.uuid4().hex[:8]}"
    await probe.push_message(  # type: ignore[attr-defined]
        bot_id=bot_id,
        message_id=message_id,
        external_user_id=external_user_id,
        text=text,
        reply_id=f"req-{message_id}",
        chat_id=CHAT_ID,
    )
    return message_id


async def _run_directly(
    runtime_url: str, stack: GatewayStack, *, message_id: str
) -> dict[str, str]:
    client = RuntimeClient(runtime_url)
    try:
        request = RunRequest(
            agent_id=stack.agent_id,
            platform_user_id=stack.platform_user_id,
            conversation_id=None,
            channel=ChannelContext(type="WECOM", bot_id=BOT_ID, external_conversation_id=CHAT_ID),
            message=MessageInput(id=message_id, type="text", text="B-122 直连"),
        )
        conversation_id = ""
        status = ""
        async for event in client.create_run(
            request, tenant_id=stack.tenant_id, trace_id="trace-b122"
        ):
            if event.type == "run.created":
                conversation_id = str((event.data or {}).get("conversation_id") or "")
            if event.type == "run.completed":
                status = str((event.data or {}).get("status") or "")
        return {"conversation_id": conversation_id, "status": status}
    finally:
        await client.aclose()


async def test_s01_two_bots_share_one_agent_and_any_runtime_serves(
    gateway_stack: GatewayStack, extra_bots: dict[str, str]
) -> None:
    probe = gateway_stack.ws_probe
    assert probe is not None
    # 新 bot 的下一次快照轮询需要它在探针侧被接受
    probe.expected_bots[BOT2_ID] = BOT2_SECRET  # type: ignore[attr-defined]
    await _wait_for(
        lambda: any(
            (frame.get("body") or {}).get("bot_id") == BOT2_ID
            for frame in probe.frames_of("aibot_subscribe")  # type: ignore[attr-defined]
        ),
        what="第二个 bot 未在快照轮询后完成真实 WS 认证",
        timeout=SNAPSHOT_POLL_WAIT_SEC,
    )

    # 两个 bot（同 Agent、同平台用户）各自经真实 WS 进入 Gateway 并得到回复
    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in (gateway_stack.console_url, gateway_stack.runtime_url, gateway_stack.gateway_url):
            await _wait_for(
                lambda url=url: client.get(f"{url}/healthz"),
                what=f"{url} 未就绪",
                timeout=PUSH_REPLY_TIMEOUT_SEC,
            )
    await _push(gateway_stack, bot_id=BOT_ID, external_user_id=BOUND_EXTERNAL_USER_ID, text="bot1 路由")
    await _wait_for(
        lambda: _frames_for_bot(gateway_stack, BOT_ID),
        what="第一个 bot 的入站未得到回复",
        timeout=PUSH_REPLY_TIMEOUT_SEC,
    )
    await _push(gateway_stack, bot_id=BOT2_ID, external_user_id=BOT2_EXTERNAL_USER, text="bot2 路由")
    await _wait_for(
        lambda: _frames_for_bot(gateway_stack, BOT2_ID),
        what="第二个 bot 的入站未得到回复",
        timeout=PUSH_REPLY_TIMEOUT_SEC,
    )

    # 两个 bot 的 Run 落到同一逻辑 Agent，且属于同一平台用户的同一会话（无 bot→Pod 绑定）
    runs = await _scalar(
        "SELECT count(DISTINCT agent_id) FROM runtime.run_record "
        "WHERE tenant_id = :t AND is_deleted = false",
        {"t": gateway_stack.tenant_id},
    )
    assert int(runs or 0) == 1, "两个 bot 必须路由到同一逻辑 Agent"
    conversations = await _scalar(
        "SELECT count(DISTINCT conversation_id) FROM runtime.run_record "
        "WHERE tenant_id = :t AND is_deleted = false",
        {"t": gateway_stack.tenant_id},
    )
    assert int(conversations or 0) == 1, "同一平台用户的多 bot 消息应落在同一会话"

    # 任意 Runtime 实例都能承载同一逻辑 Agent 的会话（实例可替换、无 Pod 亲和）
    first = await _run_directly(
        gateway_stack.runtime_url, gateway_stack, message_id=f"b122-1-{uuid.uuid4().hex[:6]}"
    )
    second = await _run_directly(
        gateway_stack.runtime2_url, gateway_stack, message_id=f"b122-2-{uuid.uuid4().hex[:6]}"
    )
    assert first["status"] == "COMPLETED" and second["status"] == "COMPLETED"
    assert first["conversation_id"] == second["conversation_id"] != ""


async def test_b122_disabled_bot_is_not_found_and_creates_no_run(
    gateway_stack: GatewayStack, extra_bots: dict[str, str]
) -> None:
    async with httpx.AsyncClient(base_url=gateway_stack.console_url, timeout=10.0) as client:
        response = await client.post(
            RESOLVE_PATH,
            json={
                "channel": "WECOM",
                "bot_id": DISABLED_BOT_ID,
                "external_user_id": BOT2_EXTERNAL_USER,
            },
            headers={"X-Tenant-Id": gateway_stack.tenant_id},
        )
    assert response.status_code in (403, 404), response.text
    assert response.json()["code"] == "BOT_NOT_FOUND", response.text

    runs = await _scalar(
        "SELECT count(*) FROM runtime.run_record WHERE tenant_id = :t "
        "AND channel_json->>'bot_id' = :bot",
        {"t": gateway_stack.tenant_id, "bot": DISABLED_BOT_ID},
    )
    assert int(runs or 0) == 0, "禁用 bot 不得产生 Run"


async def test_b122_four_units_healthy_and_no_pod_binding_in_facts(
    gateway_stack: GatewayStack, extra_bots: dict[str, str]
) -> None:
    for name in ("console", "runtime", "runtime-2", "worker", "gateway"):
        process = gateway_stack.processes[name]
        assert process._process is not None and process._process.poll() is None, name
    for url in (
        gateway_stack.console_url,
        gateway_stack.runtime_url,
        gateway_stack.runtime2_url,
        gateway_stack.gateway_url,
    ):
        async with httpx.AsyncClient(timeout=10.0) as client:
            assert (await client.get(f"{url}/healthz")).status_code == 200, url

    # Agent 0..N bot：同一 Agent 下存在多个 enabled bot，且每个 bot 只路由到一个 Agent
    enabled_bots = await _scalar(
        "SELECT count(DISTINCT bot_id) FROM control.bot_account "
        "WHERE tenant_id = :t AND enabled = true AND is_deleted = false",
        {"t": gateway_stack.tenant_id},
    )
    assert int(enabled_bots or 0) >= 2, "同一 Agent 应可挂多个 bot"
    agents_for_bots = await _scalar(
        "SELECT count(DISTINCT agent_id) FROM control.bot_account "
        "WHERE tenant_id = :t AND enabled = true AND is_deleted = false",
        {"t": gateway_stack.tenant_id},
    )
    assert int(agents_for_bots or 0) == 1, "bot 只能路由到一个 Agent"

    # 事实层无 Pod 绑定：库中不存在 pod 相关列，且已落库的 Run 渠道上下文中不含 pod 字段
    pod_columns = await _scalar(
        "SELECT count(*) FROM information_schema.columns WHERE column_name ILIKE '%pod%'", {}
    )
    assert int(pod_columns or 0) == 0, "不得存在 Pod 绑定列"
    pod_runs = await _scalar(
        "SELECT count(*) FROM runtime.run_record WHERE channel_json::text ILIKE '%pod%'", {}
    )
    assert int(pod_runs or 0) == 0, "Run 的渠道上下文不得含 pod 字段"


async def test_b122_cleanup_leaves_no_routing_residue(gateway_stack: GatewayStack) -> None:
    await purge_tenant()
    for table in ("control.bot_account", "control.channel_identity", "runtime.run_record"):
        assert await count_tenant_rows(table) == 0, table
