"""B-128 / E-07 / RULE-03 / RISK-03: Secret 不泄露与单 Bot 隔离
（真实 PG bot secret → Console 内部快照 HTTP → Gateway/SDK → 日志/审计/快照/IM 输出）。

不得 Mock 的真实边界：真实 PostgreSQL（bot secret 与运行事实）、真实 Console 内部快照 HTTP、
真实 Gateway 进程 + 生产 WeComAdapter → 官方 SDK → 真实本地 WS 探针；验收类不制造 RED。
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
from muad_console_platform.application.channel_service import build_identity_key
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.channel import BotAccount, ChannelIdentity
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

BAD_BOT_ID = "e2e-im-bot-badsecret"
BAD_BOT_EXTERNAL_USER = "e2e-im-ext-badsecret"
CANARY_SECRET = "e2e-canary-secret-do-not-leak-9f3a1c"
BOTS_PATH = "/internal/channel/bots"
WAIT_TIMEOUT_SEC = 90.0
SNAPSHOT_POLL_WAIT_SEC = 45.0


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
async def bad_secret_bot(gateway_stack: GatewayStack) -> Any:
    """真实 PG：第二个 bot 使用**错误 secret**（canary），同 Agent、同平台用户。"""
    async with get_session_factory()() as session:
        bot = BotAccount(
            tenant_id=gateway_stack.tenant_id,
            channel="WECOM",
            name="IM E2E Bad Secret Bot",
            bot_id=BAD_BOT_ID,
            secret=CANARY_SECRET,
            agent_id=gateway_stack.agent_id,
            enabled=True,
        )
        session.add(bot)
        await session.flush()
        session.add(
            ChannelIdentity(
                tenant_id=gateway_stack.tenant_id,
                channel="WECOM",
                identity_key=build_identity_key("WECOM", BAD_BOT_ID, BAD_BOT_EXTERNAL_USER),
                external_user_id=BAD_BOT_EXTERNAL_USER,
                bot_account_id=bot.id,
                platform_user_id=gateway_stack.platform_user_id,
            )
        )
        await session.commit()
    yield {"bot_id": BAD_BOT_ID}


async def _readyz(stack: GatewayStack) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{stack.gateway_url}/readyz")
    return {"status_code": response.status_code, **(response.json().get("data") or {})}


def _gateway_log(stack: GatewayStack) -> str:
    process = stack.processes["gateway"]
    path = Path(process.log_path)
    return path.read_text(errors="replace") if path.exists() else ""


def _probe_texts(stack: GatewayStack) -> list[str]:
    probe = stack.ws_probe
    assert probe is not None
    return [
        str(((item.frame.get("body") or {}).get("text") or {}).get("content") or "")
        for item in probe.received  # type: ignore[attr-defined]
        if item.frame.get("cmd") in ("aibot_send_msg", "aibot_respond_msg")
    ]


async def test_e07_bad_secret_isolates_target_bot_and_readyz_stays_ready(
    gateway_stack: GatewayStack, bad_secret_bot: dict[str, str]
) -> None:
    probe = gateway_stack.ws_probe
    assert probe is not None
    # 坏 secret 的 bot 会尝试认证并被探针拒绝（40101），好 bot 不受影响
    await _wait_for(
        lambda: any(
            (frame.get("body") or {}).get("bot_id") == BAD_BOT_ID
            for frame in probe.frames_of("aibot_subscribe")  # type: ignore[attr-defined]
        ),
        what="坏 secret 的 bot 未尝试在真实 WS 上认证",
        timeout=SNAPSHOT_POLL_WAIT_SEC,
    )
    await _wait_for(
        lambda: any(
            (frame.get("body") or {}).get("bot_id") == BAD_BOT_ID
            for frame in probe.auth_failures  # type: ignore[attr-defined]
        ),
        what="探针未记录坏 secret 的认证失败",
        timeout=SNAPSHOT_POLL_WAIT_SEC,
    )

    data = await _readyz(gateway_stack)
    # 缺/坏 secret 不停止全部 bot：就绪依据 manager + 完整快照，而非"全部 CONNECTED"
    assert data["status_code"] == 200, data
    assert data["status"] == "ready", data
    assert data["adapters"] == {"WECOM": True}, data
    assert BAD_BOT_ID in (data.get("degraded_bots") or {}), data
    assert BOT_ID not in (data.get("degraded_bots") or {}), data

    # 坏 bot 的 secret 不得出现在 Gateway 进程日志里
    assert CANARY_SECRET not in _gateway_log(gateway_stack), "secret 进入 Gateway 日志"


async def test_b128_canary_never_reaches_logs_outputs_or_facts(
    gateway_stack: GatewayStack, bad_secret_bot: dict[str, str]
) -> None:
    # 设计指定的最小凭据边界：内部 bot 快照允许携带 secret（受 X-Internal-Service 保护）
    async with httpx.AsyncClient(base_url=gateway_stack.console_url, timeout=10.0) as client:
        internal = await client.get(
            BOTS_PATH,
            params={"page": 1, "page_size": 50},
            headers=gateway_stack.service_headers(),
        )
        assert internal.status_code == 200, internal.text
        assert CANARY_SECRET in internal.text, "内部快照应携带 secret（最小凭据边界）"

        # 公开/无服务身份的访问不得拿到内部快照（强制服务身份）
        anonymous = await client.get(BOTS_PATH, params={"page": 1, "page_size": 50})
        assert anonymous.status_code == 403, anonymous.text
        assert anonymous.json()["code"] == "FORBIDDEN", anonymous.text
        assert CANARY_SECRET not in anonymous.text

    # IM 出站不得携带 secret
    assert all(CANARY_SECRET not in text for text in _probe_texts(gateway_stack))

    # 运行事实（Snapshot / 审计）不得携带 secret
    tables = (
        "runtime.runtime_snapshot",
        "runtime.egress_audit",
        "runtime.tool_call_audit",
        "runtime.model_invocation_audit",
    )
    for table in tables:
        leaked_rows = await _scalar(
            f"SELECT count(*) FROM {table} AS row_source WHERE row_source.tenant_id = :t "
            f"AND to_jsonb(row_source.*)::text ILIKE :canary",
            {"t": gateway_stack.tenant_id, "canary": f"%{CANARY_SECRET}%"},
        )
        assert int(leaked_rows or 0) == 0, f"{table} 出现 secret canary"

    # SecretProvider 无新增：Gateway 只从内部快照取 secret，不引入独立 secret 设施
    gateway_sources = (Path(__file__).resolve().parents[3] / "apps/im-gateway/src").rglob("*.py")
    offenders = [
        str(path)
        for path in gateway_sources
        if "SecretProvider" in path.read_text(encoding="utf-8")
        or "vault" in path.read_text(encoding="utf-8").lower()
    ]
    assert offenders == [], f"不应引入新的 secret provider：{offenders}"


async def test_rule03_bot_secret_stored_and_served_only_via_internal_boundary(
    gateway_stack: GatewayStack, bad_secret_bot: dict[str, str]
) -> None:
    stored = await _scalar(
        "SELECT secret FROM control.bot_account WHERE tenant_id = :t AND bot_id = :b",
        {"t": gateway_stack.tenant_id, "b": BAD_BOT_ID},
    )
    assert stored == CANARY_SECRET, "Owner 表按设计保存 bot secret"
    # 内部快照返回该 bot 且带 secret；公开路径不暴露（上例已断言）
    async with httpx.AsyncClient(base_url=gateway_stack.console_url, timeout=10.0) as client:
        internal = await client.get(
            BOTS_PATH,
            params={"page": 1, "page_size": 50},
            headers=gateway_stack.service_headers(),
        )
    assert internal.status_code == 200
    items = internal.json()["data"]["items"]
    assert any(item["bot_id"] == BAD_BOT_ID and item.get("secret") == CANARY_SECRET for item in items)


async def test_e07_cleanup_leaves_no_secret_residue(gateway_stack: GatewayStack) -> None:
    await purge_tenant()
    for table in ("control.bot_account", "control.channel_identity", "runtime.runtime_snapshot"):
        assert await count_tenant_rows(table) == 0, table
