"""S-01/S-02 新增存储与 Prompt 出口安全（真实 Gateway/Runtime/Console/PG/模型探针）。

不得 Mock 的真实边界
- S-01：真实 WS 探针 → 真实 Gateway → 真实 Runtime HTTP（`/v1/runs`、`/v1/runs/{id}/resume`、
  `/internal/admin/users/{id}/memory/commands`）→ 真实 Console HTTP（会话 + CSRF + RBAC）→ 真实 PG。
- S-02：真实 Runtime 装配（凭据 canary = 模型 api_key，来自真实 `resolve-credentials`）→
  真实 PG 新增表/历史/回执/提案/job → 真实模型探针请求体（consolidation Prompt）→ 真实服务日志文件。

E2E 场景在编码阶段只编写 + collect，登记 `e2e_deferred`（manifest `kind=e2e`）；
执行归 TASK-030 / 需求终验：`code-flow acceptance run --include-e2e`。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from muad_common import SharedSettings
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ROLE_BUILDER, ConsoleAccount
from muad_console_platform.infrastructure.models.control import PlatformUser
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    BOUND_EXTERNAL_USER_ID,
    CHAT_ID,
    INTERNAL_TOKEN,
    GatewayStack,
    restart_process,
)
from tests.e2e.wecom_probe_app import frame_text

pytestmark = pytest.mark.e2e
#: 本次执行已知凭据 canary = 真实模型 api_key（`tests/e2e/seed_im_gateway.py` 种下）。
CANARY = "e2e-model-key"
CONSOLIDATION_MARKER = "Consolidate this frozen conversation input"
WAIT_TIMEOUT_SEC = 150.0
REPLY_TIMEOUT_SEC = 120.0
#: canary 反查面：新增表 + 历史/回执/提案/job/工具审计 + Console 审计。
#: `runtime.canonical_event` / `runtime.run_record.input_text` 保存会话本身（用户自己的输入），
#: 不在"系统把已知凭据写进存储"的反查面内；`runtime.model_invocation_audit` 只存计数与状态。
STORE_TABLES = (
    "runtime.user_memory",
    "runtime.user_memory_history",
    "runtime.memory_command_receipt",
    "runtime.memory_scope",
    "runtime.memory_consolidation_job",
    "runtime.run_interrupt",
    "runtime.tool_call_audit",
    "runtime.model_invocation_audit",
    "control.config_audit_log",
)


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


async def _push(stack: GatewayStack, *, text: str) -> str:
    probe = stack.ws_probe
    assert probe is not None
    message_id = f"security-{uuid.uuid4().hex[:8]}"
    await probe.push_message(  # type: ignore[attr-defined]
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id=BOUND_EXTERNAL_USER_ID,
        text=text,
        reply_id=f"req-{message_id}",
        chat_id=CHAT_ID,
    )
    return message_id


async def _wait_for_gateway_ws(stack: GatewayStack) -> None:
    probe = stack.ws_probe
    assert probe is not None, "WS 探针未接入栈"
    await _wait_for(
        lambda: BOT_ID in getattr(probe, "connection_bots", {}).values(),
        what=f"Gateway 未在超时内连上真实 WS 探针（bot {BOT_ID}）",
        timeout=REPLY_TIMEOUT_SEC,
    )


def _configure_probe(stack: GatewayStack, *, arguments: dict[str, Any], final_text: str) -> None:
    """把真实模型探针切成「首轮调用 remember → 收到工具结果后给最终文本」。"""
    probe = stack.processes["llm-probe"]
    probe.env["OPENAI_PROBE_TOOL_NAME"] = "remember"
    probe.env["OPENAI_PROBE_TOOL_ARGUMENTS"] = json.dumps(arguments, ensure_ascii=False)
    probe.env["OPENAI_PROBE_FINAL_TEXT"] = final_text
    restart_process(probe)


def _probe_requests(stack: GatewayStack) -> list[dict[str, Any]]:
    """真实模型探针记录的请求体（实际发给模型的 Prompt）。"""
    response = httpx.get(f"{stack.llm_url}/requests", timeout=10.0)
    response.raise_for_status()
    return [body for body in (response.json().get("requests") or []) if isinstance(body, dict)]


def _consolidation_prompts(stack: GatewayStack) -> list[str]:
    return [
        json.dumps(body, ensure_ascii=False)
        for body in _probe_requests(stack)
        if any(
            isinstance(message, dict) and CONSOLIDATION_MARKER in str(message.get("content") or "")
            for message in body.get("messages") or []
        )
    ]


def _stack_log_text(stack: GatewayStack) -> str:
    chunks = []
    for process in stack.processes.values():
        path = Path(process.log_path)
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _canary_log_leaks(stack: GatewayStack) -> list[str]:
    """含 canary 的日志行，排除渠道 SDK 对**原始入站帧**的 DEBUG 回显。

    原始入站帧就是用户自己的输入（与 `canonical_event` / `run_record.input_text` 同类，
    见上方 `STORE_TABLES` 的边界说明）：渠道 SDK 在 Runtime 处理之前就把它写进自己的
    DEBUG 日志。本断言的反查面是**记忆特性自己**的持久出口（新增表/历史/回执/提案/job/
    工具审计/服务日志/提炼 Prompt），不把用户原话在渠道侧的既有回显算成记忆泄漏。
    """
    return [
        line
        for line in _stack_log_text(stack).splitlines()
        if CANARY in line and "Received push message" not in line
    ]


async def _tenant_store_text(tenant: str) -> str:
    chunks = []
    for table in STORE_TABLES:
        rows = await _fetch(
            f"SELECT to_jsonb(t) AS payload FROM {table} t WHERE tenant_id = :tenant", {"tenant": tenant}
        )
        chunks.extend(str(row["payload"]) for row in rows)
    return "\n".join(chunks)


async def _memory_rows(tenant: str, key: str) -> list[dict[str, Any]]:
    return await _fetch(
        "SELECT memory_key,author,content_json,source_quote FROM runtime.user_memory"
        " WHERE tenant_id = :tenant AND memory_key = :key",
        {"tenant": tenant, "key": key},
    )


async def _pending_proposals(run_id: str) -> int:
    return int(
        await _scalar(
            "SELECT count(*) FROM runtime.run_interrupt WHERE run_id = :run_id"
            " AND interrupt_type = 'MEMORY_CONFIRMATION' AND status = 'WAITING'",
            {"run_id": uuid.UUID(run_id)},
        )
        or 0
    )


async def _run_status(run_id: str) -> str | None:
    value = await _scalar(
        "SELECT status FROM runtime.run_record WHERE id = :run_id", {"run_id": uuid.UUID(run_id)}
    )
    return None if value is None else str(value)


async def _cancel_active_runs(stack: GatewayStack) -> None:
    """取消本租户的活跃 Run，保证下一条消息**新建** Run 而不是续跑上一条。

    真实 Runtime 的 `create_run` 对同会话 WAITING_INPUT 会做恢复而不是新建（run_service.py），
    所以上一个用例留下的待确认 Run 会让本用例拿不到新 Run。取消走真实 HTTP API（WAITING 直接
    CAS 为 CANCELLED），不直接改库。
    """
    rows = await _fetch(
        "SELECT id::text AS id FROM runtime.run_record WHERE tenant_id = :tenant"
        " AND status IN ('CREATED','RUNNING','WAITING_INPUT','WAITING_TOOL')"
        " ORDER BY create_time",
        {"tenant": stack.tenant_id},
    )
    for row in rows:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{stack.runtime_url}/v1/runs/{row['id']}/cancel",
                headers={"X-Tenant-Id": stack.tenant_id},
            )
            assert response.status_code == 200, response.text


async def _started_run(stack: GatewayStack, text: str) -> str:
    """推送一条真实消息并返回本次新建的 Run id（不假设终态：确认类 Run 会停在 WAITING_INPUT）。"""
    await _cancel_active_runs(stack)
    previous = await _scalar(
        "SELECT id::text FROM runtime.run_record WHERE tenant_id = :tenant"
        " ORDER BY create_time DESC LIMIT 1",
        {"tenant": stack.tenant_id},
    )
    await _push(stack, text=text)
    await _wait_for(
        lambda: _scalar(
            "SELECT count(*) FROM runtime.run_record WHERE tenant_id = :tenant AND id::text <> :previous",
            {"tenant": stack.tenant_id, "previous": str(previous or "")},
        ),
        what="推送后未创建 Run",
        timeout=REPLY_TIMEOUT_SEC,
    )
    return str(
        await _scalar(
            "SELECT id::text FROM runtime.run_record WHERE tenant_id = :tenant"
            " ORDER BY create_time DESC LIMIT 1",
            {"tenant": stack.tenant_id},
        )
    )


async def _seed_platform_user(tenant_id: str, user_id: uuid.UUID) -> None:
    async with get_session_factory()() as session:
        session.add(
            PlatformUser(
                id=user_id, tenant_id=tenant_id, user_code=f"security-{user_id.hex[:8]}",
                display_name="Security Other", status="ACTIVE",
            )
        )
        await session.commit()


async def _seed_console_accounts(tenant_id: str) -> dict[str, str]:
    suffix = uuid.uuid4().hex[:8]
    accounts = {
        "admin": f"security-admin-{suffix}",
        "builder": f"security-builder-{suffix}",
        "password": "security-fixture-password",
    }
    async with get_session_factory()() as session:
        for username, role in ((accounts["admin"], ROLE_ADMIN), (accounts["builder"], ROLE_BUILDER)):
            session.add(
                ConsoleAccount(
                    tenant_id=tenant_id, username=username, display_name=username,
                    password_hash=hash_password(accounts["password"]), role=role,
                )
            )
        await session.commit()
    return accounts


async def _login(client: httpx.AsyncClient, tenant_id: str, username: str, password: str) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert response.status_code == 200, response.text
    assert client.cookies.get("muad_csrf"), "登录未下发 CSRF cookie"


async def test_s01_memory_redesign_internal_calls_require_real_service_identity(
    gateway_stack: GatewayStack,
) -> None:
    """伪造 user/tenant/author 与无服务身份内部调用均拒绝；tenant 边界以真实 PG 回读为准。"""
    target = gateway_stack.platform_user_id
    forged_tenant = f"e2e-im-forged-{uuid.uuid4().hex[:8]}"
    url = f"{gateway_stack.runtime_url}/internal/admin/users/{target}/memory/commands"
    mutation = {
        "action": "UPSERT", "memory_key": "security.forged", "category": "EXPLICIT", "value": "blue",
    }
    # TASK-023 契约：`{actor_id, mutation}`；command_id 只从 Idempotency-Key 头取，author 由服务端派生
    command = {"actor_id": str(uuid.uuid4()), "mutation": mutation}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            missing = await client.post(
                url, json=command,
                headers={"X-Tenant-Id": gateway_stack.tenant_id, "Idempotency-Key": uuid.uuid4().hex},
            )
            assert missing.status_code == 403, missing.text
            forged = await client.post(
                url, json=command,
                headers={**gateway_stack.service_headers(), "X-Internal-Service": "forged",
                         "Idempotency-Key": uuid.uuid4().hex},
            )
            assert forged.status_code == 403, forged.text
            # 调用方不得选择 authorship（author 只由 Runtime 按服务身份派生）
            author = await client.post(
                url, json={**command, "mutation": {**mutation, "author": "USER"}},
                headers={**gateway_stack.service_headers(), "Idempotency-Key": uuid.uuid4().hex},
            )
            assert author.status_code == 422, author.text
            # 伪造 tenant：写只能落在声称的租户，不得落到目标租户
            other = await client.post(
                url, json=command,
                headers={"X-Tenant-Id": forged_tenant, "X-Internal-Service": INTERNAL_TOKEN,
                         "Idempotency-Key": uuid.uuid4().hex},
            )
            assert other.status_code == 200, other.text
        assert await _memory_rows(gateway_stack.tenant_id, "security.forged") == []
        assert await _memory_rows(forged_tenant, "security.forged") != []
    finally:
        # 0020 表带 FK：history 引用 memory，引用方先行
        await _execute(
            "DELETE FROM runtime.user_memory_history WHERE tenant_id = :tenant", {"tenant": forged_tenant}
        )
        await _execute("DELETE FROM runtime.user_memory WHERE tenant_id = :tenant", {"tenant": forged_tenant})
        await _execute(
            "DELETE FROM runtime.memory_command_receipt WHERE tenant_id = :tenant", {"tenant": forged_tenant}
        )
        await _execute(
            "DELETE FROM runtime.memory_scope WHERE tenant_id = :tenant", {"tenant": forged_tenant}
        )


async def test_s01_memory_redesign_cross_user_confirmation_is_refused(
    gateway_stack: GatewayStack,
) -> None:
    """跨用户确认拒绝：真实提案停在 WAITING_INPUT，他人/他租户 resume 均 404 且不产生 USER 事实。"""
    key = f"security.confirm.{uuid.uuid4().hex[:6]}"
    other_user = uuid.uuid4()
    await _seed_platform_user(gateway_stack.tenant_id, other_user)
    _configure_probe(
        gateway_stack,
        arguments={"memory_key": key, "category": "PREFERENCE", "value": "中文",
                   "source_quote": "以后都用中文回答我"},
        final_text="请确认是否保存这条偏好。",
    )
    await _wait_for_gateway_ws(gateway_stack)
    run_id = await _started_run(gateway_stack, "记住：以后都用中文回答我")
    await _wait_for(
        lambda: _pending_proposals(run_id),
        what="真实 Run 未停在待确认的记忆提案",
        timeout=REPLY_TIMEOUT_SEC,
    )
    body = {
        "platform_user_id": str(other_user),
        "input": {"id": f"cross-{uuid.uuid4().hex[:8]}", "type": "text", "text": "确认"},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        cross_user = await client.post(
            f"{gateway_stack.runtime_url}/v1/runs/{run_id}/resume", json=body,
            headers={"X-Tenant-Id": gateway_stack.tenant_id, "Idempotency-Key": uuid.uuid4().hex},
        )
        assert cross_user.status_code == 404, cross_user.text
        cross_tenant = await client.post(
            f"{gateway_stack.runtime_url}/v1/runs/{run_id}/resume", json=body,
            headers={"X-Tenant-Id": f"e2e-im-other-{uuid.uuid4().hex[:8]}",
                     "Idempotency-Key": uuid.uuid4().hex},
        )
        assert cross_tenant.status_code == 404, cross_tenant.text
    assert await _run_status(run_id) == "WAITING_INPUT"
    assert await _memory_rows(gateway_stack.tenant_id, key) == [], "未经本人确认不得产生 USER 事实"


async def test_s01_memory_redesign_unauthorized_console_admin_does_not_write(
    gateway_stack: GatewayStack,
) -> None:
    """未授权（非 ADMIN）Console 账号不写：真实会话 + CSRF + RBAC；ADMIN 同请求为阳性对照。"""
    key = f"security.console.{uuid.uuid4().hex[:6]}"
    accounts = await _seed_console_accounts(gateway_stack.tenant_id)
    url = f"/api/v1/users/{gateway_stack.platform_user_id}/memory"
    body = {"memory_key": key, "category": "PREFERENCE", "value": "blue"}
    try:
        async with httpx.AsyncClient(base_url=gateway_stack.console_url, timeout=30.0) as client:
            await _login(client, gateway_stack.tenant_id, accounts["builder"], accounts["password"])
            denied = await client.post(
                url, json=body,
                headers={"X-CSRF-Token": client.cookies.get("muad_csrf") or "",
                         "Idempotency-Key": uuid.uuid4().hex},
            )
            assert denied.status_code == 403, denied.text
            assert await _memory_rows(gateway_stack.tenant_id, key) == []
            await _login(client, gateway_stack.tenant_id, accounts["admin"], accounts["password"])
            allowed = await client.post(
                url, json=body,
                headers={"X-CSRF-Token": client.cookies.get("muad_csrf") or "",
                         "Idempotency-Key": uuid.uuid4().hex},
            )
            assert allowed.status_code == 200, allowed.text
            rows = await _memory_rows(gateway_stack.tenant_id, key)
            assert len(rows) == 1 and rows[0]["author"] == "ADMIN"
    finally:
        # 0020 表带 FK：history 引用 memory，引用方先行
        await _execute(
            "DELETE FROM runtime.user_memory_history WHERE tenant_id = :tenant AND memory_key = :key",
            {"tenant": gateway_stack.tenant_id, "key": key},
        )
        await _execute(
            "DELETE FROM runtime.user_memory WHERE tenant_id = :tenant AND memory_key = :key",
            {"tenant": gateway_stack.tenant_id, "key": key},
        )
        await _execute(
            "DELETE FROM control.console_session WHERE account_id IN ("
            " SELECT id FROM control.console_account WHERE tenant_id = :tenant"
            " AND username IN (:admin, :builder))",
            {"tenant": gateway_stack.tenant_id, "admin": accounts["admin"], "builder": accounts["builder"]},
        )
        await _execute(
            "DELETE FROM control.console_account WHERE tenant_id = :tenant"
            " AND username IN (:admin, :builder)",
            {"tenant": gateway_stack.tenant_id, "admin": accounts["admin"], "builder": accounts["builder"]},
        )


async def test_s02_memory_redesign_unconfirmed_candidate_stays_annotated_and_unsaved(
    gateway_stack: GatewayStack,
) -> None:
    """标注引用与未经确认：提案带真实原话引用，未确认前不落 USER 事实。

    原话证据按新契约要求**整条消息**（`evidence.verify_quote`），故推送文本与 `source_quote` 一致；
    被截取的片段不会成为来源（那是另一条独立契约）。
    """
    key = f"security.annotated.{uuid.uuid4().hex[:6]}"
    quote = "以后都用中文回答我"
    _configure_probe(
        gateway_stack,
        arguments={"memory_key": key, "category": "PREFERENCE", "value": "中文", "source_quote": quote},
        final_text="请确认是否保存这条偏好。",
    )
    await _wait_for_gateway_ws(gateway_stack)
    run_id = await _started_run(gateway_stack, quote)
    await _wait_for(
        lambda: _pending_proposals(run_id), what="真实 Run 未停在待确认的记忆提案", timeout=REPLY_TIMEOUT_SEC
    )
    proposals = await _fetch(
        "SELECT proposal_json FROM runtime.run_interrupt WHERE run_id = :run_id"
        " AND interrupt_type = 'MEMORY_CONFIRMATION' AND status = 'WAITING'",
        {"run_id": uuid.UUID(run_id)},
    )
    assert len(proposals) == 1
    proposal = proposals[0]["proposal_json"]
    assert proposal["candidate"]["memory_key"] == key
    assert proposal["candidate"]["value"] == "中文"
    assert proposal["evidence"]["source_quote"] == quote
    assert await _memory_rows(gateway_stack.tenant_id, key) == [], "未经确认的候选不得成为事实"


async def test_s02_memory_redesign_canary_never_reaches_stores_logs_or_prompt(
    gateway_stack: GatewayStack,
) -> None:
    """密钥不进入新增表、历史、回执、日志、模型 consolidation Prompt（真实装配 + canary 反查）。"""
    key = f"security.canary.{uuid.uuid4().hex[:6]}"
    user_text = f"我的模型配置是 api_key={CANARY}，请记住"
    _configure_probe(
        gateway_stack,
        arguments={"memory_key": key, "category": "EXPLICIT", "value": f"api_key={CANARY}",
                   "source_quote": user_text},
        final_text="该内容含凭据，已拒绝保存。",
    )
    await _wait_for_gateway_ws(gateway_stack)
    replies_before = len(_replies(gateway_stack))
    await _started_run(gateway_stack, user_text)
    await _wait_for(
        lambda: len(_replies(gateway_stack)) > replies_before,
        what="未在超时内收到出站回复",
        timeout=REPLY_TIMEOUT_SEC,
    )
    await _wait_for(
        lambda: _scalar(
            "SELECT count(*) FROM runtime.memory_consolidation_job WHERE tenant_id = :tenant"
            " AND status IN ('DONE','SKIPPED','FAILED')",
            {"tenant": gateway_stack.tenant_id},
        ),
        what="consolidation job 未在超时内终结",
        timeout=REPLY_TIMEOUT_SEC,
    )
    # ① 被拒绝的候选不落任何记忆事实/历史
    assert await _memory_rows(gateway_stack.tenant_id, key) == []
    # ② 工具审计只留 key/category/hash/状态，且不含 canary
    audits = await _fetch(
        "SELECT args_preview_json FROM runtime.tool_call_audit"
        " WHERE tenant_id = :tenant AND tool_name = 'remember'",
        {"tenant": gateway_stack.tenant_id},
    )
    assert audits, "缺 remember 工具审计行"
    for row in audits:
        assert CANARY not in json.dumps(row["args_preview_json"], ensure_ascii=False)
    # ③ 新增表/历史/回执/提案/job/审计：canary 反查
    assert CANARY not in await _tenant_store_text(gateway_stack.tenant_id)
    # ④ 真实服务日志文件（排除渠道 SDK 对原始入站帧的既有回显，见 `_canary_log_leaks`）
    assert _canary_log_leaks(gateway_stack) == []
    # ⑤ 模型 consolidation Prompt：真实探针请求体里不得出现 canary
    prompts = _consolidation_prompts(gateway_stack)
    assert prompts, "未捕获到 consolidation 请求"
    assert all(CANARY not in prompt for prompt in prompts)


async def test_s02_memory_redesign_canary_absent_from_receipts_and_probe_tool_messages(
    gateway_stack: GatewayStack,
) -> None:
    """回执与模型侧工具消息不含 canary：真实工具回执经真实链路交付。"""
    receipts = await _fetch(
        "SELECT status,response_json FROM runtime.memory_command_receipt WHERE tenant_id = :tenant",
        {"tenant": gateway_stack.tenant_id},
    )
    for row in receipts:
        assert CANARY not in json.dumps(row["response_json"], ensure_ascii=False)
    tool_messages = [
        str(message.get("content") or "")
        for body in _probe_requests(gateway_stack)
        for message in body.get("messages") or []
        if isinstance(message, dict) and message.get("role") == "tool"
    ]
    assert all(CANARY not in item for item in tool_messages)
