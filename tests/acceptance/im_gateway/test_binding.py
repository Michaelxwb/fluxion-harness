"""B-123 / S-02 / E-02 / RULE-02: 绑定链路（真实 WS→Gateway 命令→Console bind HTTP→PostgreSQL→SDK 回复）。

不得 Mock 的真实边界：真实本地 WS 探针（真实 `wss://` + 官方 SDK）、真实 Gateway 进程、
真实 Console bind HTTP、真实 PostgreSQL（bind_code / channel_identity / agent_access_grant）。
验收类任务以 owner 实现任务已完成为前提，不制造 RED（见 Baseline）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from muad_common import SharedSettings
from muad_console_platform.application.channel_service import hash_bind_code
from muad_contracts import ChannelBindRequest
from muad_im_gateway.application.console_client import ConsoleClient
from muad_im_gateway.main import app as gateway_app
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    CHAT_ID,
    GatewayStack,
    count_tenant_rows,
    purge_tenant,
)

BIND_PATH = "/internal/channel/bind"
BOTS_PATH = "/internal/channel/bots"
REPLY_TIMEOUT_SEC = 45.0
BIND_SUCCESS_TEXT = "绑定成功"


async def _execute(statement: str, params: dict[str, object]) -> None:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.begin() as connection:
            await connection.execute(text(statement), params)
    finally:
        await engine.dispose()


async def _scalar(statement: str, params: dict[str, object]) -> object:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(statement), params)
    finally:
        await engine.dispose()


@pytest.fixture()
async def bind_codes(gateway_stack: GatewayStack) -> AsyncIterator[dict[str, str]]:
    """真实 PG：为栈内用户种入 有效 / 过期 / 已用 三个绑定码（栈清理会兜底删除）。"""
    suffix = uuid.uuid4().hex[:8]
    codes = {
        "valid": f"B123{suffix.upper()}",
        "expired": f"EXP{suffix.upper()}",
        "used": f"USD{suffix.upper()}",
    }
    rows = [
        ("valid", "ACTIVE", "now() + interval '10 minutes'"),
        ("expired", "ACTIVE", "now() - interval '1 minute'"),
        ("used", "USED", "now() + interval '10 minutes'"),
    ]
    for key, status, expires in rows:
        await _execute(
            "INSERT INTO control.bind_code "
            "(id, tenant_id, platform_user_id, code_hash, status, expires_at, created_by) "
            f"VALUES (:id, :tenant_id, :user_id, :code_hash, :status, {expires}, :created_by)",
            {
                "id": uuid.uuid4(),
                "tenant_id": gateway_stack.tenant_id,
                "user_id": gateway_stack.platform_user_id,
                "code_hash": hash_bind_code(codes[key]),
                "status": status,
                "created_by": gateway_stack.platform_user_id,
            },
        )
    yield codes


def _probe_texts(gateway_stack: GatewayStack) -> list[str]:
    probe = gateway_stack.ws_probe
    assert probe is not None, "WS 探针未接入栈"
    return [
        str(((item.frame.get("body") or {}).get("text") or {}).get("content") or "")
        for item in probe.received  # type: ignore[attr-defined]
        if item.frame.get("cmd") == "aibot_send_msg"
    ]


async def _wait_for_gateway_ws(gateway_stack: GatewayStack) -> None:
    """等真实 Gateway 进程在 WS 探针上完成认证（探针未连上时无法推送）。"""
    probe = gateway_stack.ws_probe
    assert probe is not None, "WS 探针未接入栈"
    deadline = time.monotonic() + REPLY_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if probe.frames_of("aibot_subscribe") and probe.connections:  # type: ignore[attr-defined]
            return
        await asyncio.sleep(0.2)
    raise AssertionError("Gateway 未在超时内连上真实 WS 探针")


async def _push_bind(
    gateway_stack: GatewayStack,
    *,
    text: str,
    message_id: str,
    external_user_id: str,
) -> None:
    probe = gateway_stack.ws_probe
    assert probe is not None
    await probe.push_message(  # type: ignore[attr-defined]
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id=external_user_id,
        text=text,
        reply_id=f"req-{message_id}",
        chat_id=CHAT_ID,
    )


async def _wait_for_reply(gateway_stack: GatewayStack, *, expected: str) -> str:
    deadline = time.monotonic() + REPLY_TIMEOUT_SEC
    while time.monotonic() < deadline:
        texts = _probe_texts(gateway_stack)
        if expected in texts:
            return expected
        await asyncio.sleep(0.2)
    raise AssertionError(f"未在超时内看到回复 {expected!r}；实际 {_probe_texts(gateway_stack)!r}")


def _catalog_message(code: str) -> str:
    return str(gateway_app.state.message_catalog.message(code, "zh-CN"))


async def test_b123_bind_over_real_ws_creates_identity_and_maps_catalog_errors(
    gateway_stack: GatewayStack, bind_codes: dict[str, str]
) -> None:
    external_user_id = f"b123-ext-{uuid.uuid4().hex[:8]}"
    await _wait_for_gateway_ws(gateway_stack)
    await _push_bind(
        gateway_stack,
        text=f"/bind {bind_codes['valid']}",
        message_id=f"b123-bind-{uuid.uuid4().hex[:8]}",
        external_user_id=external_user_id,
    )
    await _wait_for_reply(gateway_stack, expected=BIND_SUCCESS_TEXT)

    # 绑定成功与身份记录一致：真实 PG 里该外部用户已映射到栈内平台用户
    platform_user_id = await _scalar(
        "SELECT platform_user_id::text FROM control.channel_identity "
        "WHERE tenant_id = :t AND external_user_id = :u AND is_deleted = false",
        {"t": gateway_stack.tenant_id, "u": external_user_id},
    )
    assert platform_user_id == str(gateway_stack.platform_user_id)
    status = await _scalar(
        "SELECT status FROM control.bind_code WHERE tenant_id = :t AND code_hash = :h",
        {"t": gateway_stack.tenant_id, "h": hash_bind_code(bind_codes["valid"])},
    )
    assert status == "USED"
    # 无授权扩张：绑定不隐式授予 Agent 权限
    grants = await _scalar(
        "SELECT count(*) FROM control.agent_access_grant WHERE user_id = :u",
        {"u": gateway_stack.platform_user_id},
    )
    assert int(grants or 0) == 1  # 仅种子里的那一条授权

    # 错误码经 catalog 映射后回到 IM（无效码）
    await _push_bind(
        gateway_stack,
        text=f"/bind NOPE{uuid.uuid4().hex[:6].upper()}",
        message_id=f"b123-bad-{uuid.uuid4().hex[:8]}",
        external_user_id=external_user_id,
    )
    await _wait_for_reply(gateway_stack, expected=_catalog_message("BIND_CODE_INVALID"))


async def test_s02_bind_client_path_persists_identity_without_grant(
    gateway_stack: GatewayStack, bind_codes: dict[str, str]
) -> None:
    """S-02（迁移自 tests/e2e 的 ConsoleClient→Console→PG 口径）：有效码绑定成功、身份持久、不授予权限。"""
    external_user_id = f"s02-ext-{uuid.uuid4().hex[:8]}"
    client = ConsoleClient(gateway_stack.console_url)
    try:
        response = await client.bind(
            ChannelBindRequest(
                channel="WECOM",
                bot_id=gateway_stack.bot_id,
                external_user_id=external_user_id,
                bind_code=bind_codes["valid"],
            ),
            gateway_stack.tenant_id,
        )
    finally:
        await client.aclose()

    assert response.bound is True
    assert response.platform_user_id == gateway_stack.platform_user_id
    platform_user_id = await _scalar(
        "SELECT platform_user_id::text FROM control.channel_identity "
        "WHERE tenant_id = :t AND external_user_id = :u AND is_deleted = false",
        {"t": gateway_stack.tenant_id, "u": external_user_id},
    )
    assert platform_user_id == str(gateway_stack.platform_user_id)
    grants = await _scalar(
        "SELECT count(*) FROM control.agent_access_grant WHERE user_id = :u",
        {"u": gateway_stack.platform_user_id},
    )
    assert int(grants or 0) == 1


async def test_e02_invalid_expired_used_codes_have_no_side_effects(
    gateway_stack: GatewayStack, bind_codes: dict[str, str]
) -> None:
    async with httpx.AsyncClient(base_url=gateway_stack.console_url, timeout=10.0) as client:
        cases = [
            (f"NOPE{uuid.uuid4().hex[:6].upper()}", "BIND_CODE_INVALID"),
            (bind_codes["expired"], "BIND_CODE_EXPIRED"),
            (bind_codes["used"], "BIND_CODE_INVALID"),
        ]
        for code, expected_code in cases:
            external_user_id = f"e02-ext-{uuid.uuid4().hex[:8]}"
            response = await client.post(
                BIND_PATH,
                json={
                    "channel": "WECOM",
                    "bot_id": gateway_stack.bot_id,
                    "external_user_id": external_user_id,
                    "bind_code": code,
                },
                headers={"X-Tenant-Id": gateway_stack.tenant_id},
            )
            assert response.status_code >= 400, response.text
            body = response.json()
            assert body["code"] == expected_code, body
            # 统一封套：trace/request/timestamp 完整
            assert body["trace_id"] and body["request_id"] and body["timestamp"]
            # 事务无副作用：失败绑定不产生身份行
            identity = await _scalar(
                "SELECT count(*) FROM control.channel_identity "
                "WHERE tenant_id = :t AND external_user_id = :u",
                {"t": gateway_stack.tenant_id, "u": external_user_id},
            )
            assert int(identity or 0) == 0, (code, external_user_id)

        # 列表统一 items/page/page_size/total 且分页边界受校验
        ok_response = await client.get(
            BOTS_PATH,
            params={"page": 1, "page_size": 20},
            headers={"X-Tenant-Id": gateway_stack.tenant_id},
        )
        assert ok_response.status_code == 200, ok_response.text
        data = ok_response.json()["data"]
        for key in ("items", "page", "page_size", "total"):
            assert key in data, data

        for params in ({"page": 0, "page_size": 20}, {"page": 1, "page_size": 101}):
            invalid = await client.get(
                BOTS_PATH, params=params, headers={"X-Tenant-Id": gateway_stack.tenant_id}
            )
            assert invalid.status_code >= 400, (params, invalid.text)
            assert invalid.json()["code"] == "COMMON_VALIDATION_ERROR", invalid.text


async def test_rule02_cleanup_leaves_no_binding_residue(gateway_stack: GatewayStack) -> None:
    """最后执行：清理后绑定相关行无残留。"""
    assert gateway_stack.tenant_id.startswith("e2e-im-")

    await purge_tenant()
    for table in (
        "control.bind_code",
        "control.channel_identity",
        "control.bot_account",
        "control.platform_user",
    ):
        assert await count_tenant_rows(table) == 0, table
