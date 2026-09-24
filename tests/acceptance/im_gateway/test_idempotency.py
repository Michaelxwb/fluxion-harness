"""B-126 / RULE-api-002: 绑定、Run 与新建会话的端到端幂等（真实 Console/Runtime→真实 PG 幂等表）。

不得 Mock 的真实边界：真实 Console/Runtime 进程与真实 PostgreSQL（`runtime.run_submission`
partial unique `(tenant_id, idempotency_key, endpoint)`、`control.channel_identity`）、
进程重启后回读持久首次结果。验收类以 owner 实现任务已完成为前提，不制造 RED（Baseline）。
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
from muad_contracts import ChannelContext, MessageInput, RunRequest
from muad_im_gateway.application.runtime_client import RuntimeClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.im_gateway.environment import (
    GatewayStack,
    count_tenant_rows,
    purge_tenant,
    restart_process,
)

BIND_PATH = "/internal/channel/bind"
RUNS_ENDPOINT = "POST /v1/runs"
READY_TIMEOUT_SEC = 45.0


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


@pytest.fixture()
async def bind_code(gateway_stack: GatewayStack) -> AsyncIterator[str]:
    """真实 PG：种入一个有效绑定码（栈清理兜底删除）。"""
    code = f"B126{uuid.uuid4().hex[:8].upper()}"
    await _execute(
        "INSERT INTO control.bind_code "
        "(id, tenant_id, platform_user_id, code_hash, status, expires_at, created_by) "
        "VALUES (:id, :tenant_id, :user_id, :code_hash, 'ACTIVE', "
        "now() + interval '10 minutes', :created_by)",
        {
            "id": uuid.uuid4(),
            "tenant_id": gateway_stack.tenant_id,
            "user_id": gateway_stack.platform_user_id,
            "code_hash": hash_bind_code(code),
            "created_by": gateway_stack.platform_user_id,
        },
    )
    yield code


def _run_request(gateway_stack: GatewayStack, *, message_id: str, text: str) -> RunRequest:
    return RunRequest(
        agent_id=gateway_stack.agent_id,
        platform_user_id=gateway_stack.platform_user_id,
        conversation_id=None,
        channel=ChannelContext(
            type="WECOM",
            bot_id=gateway_stack.bot_id,
            external_conversation_id=gateway_stack.chat_id,
        ),
        message=MessageInput(id=message_id, type="text", text=text),
    )


async def _run(runtime_url: str, gateway_stack: GatewayStack, *, key: str, text: str) -> dict[str, str]:
    """经生产 RuntimeClient 提交一次 Run（显式 Idempotency-Key），返回终态与 run/conversation id。"""
    client = RuntimeClient(runtime_url)
    try:
        request = _run_request(gateway_stack, message_id=key, text=text)
        result = {"run_id": "", "conversation_id": "", "status": ""}
        async for event in client.create_run(
            request, tenant_id=gateway_stack.tenant_id, trace_id="trace-b126", idempotency_key=key
        ):
            if event.type == "run.created":
                result["run_id"] = str(event.run_id or (event.data or {}).get("run_id") or "")
                result["conversation_id"] = str((event.data or {}).get("conversation_id") or "")
            if event.type == "run.completed":
                result["status"] = str((event.data or {}).get("status") or "")
        return result
    finally:
        await client.aclose()


async def _wait_healthy(url: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SEC
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{url}/healthz")
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.2)
    raise AssertionError(f"{url} 未在超时内恢复健康")


async def test_b126_run_replays_persisted_first_result_after_process_restart(
    gateway_stack: GatewayStack,
) -> None:
    key = f"b126-run-{uuid.uuid4().hex[:10]}"
    text = "B-126 幂等内容"
    first = await _run(gateway_stack.runtime_url, gateway_stack, key=key, text=text)
    assert first["status"] == "COMPLETED", first
    assert first["run_id"], first

    submissions = await _scalar(
        "SELECT count(*) FROM runtime.run_submission "
        "WHERE tenant_id = :t AND idempotency_key = :k AND is_deleted = false",
        {"t": gateway_stack.tenant_id, "k": key},
    )
    assert int(submissions or 0) == 1
    # 进程重启后回读持久首次结果：同一 key 不得新建 Run
    restart_process(gateway_stack.processes["runtime"])
    await _wait_healthy(gateway_stack.runtime_url)
    replay = await _run(gateway_stack.runtime_url, gateway_stack, key=key, text=text)

    assert replay["run_id"] == first["run_id"], replay
    assert replay["status"] == "COMPLETED", replay
    submissions_after = await _scalar(
        "SELECT count(*) FROM runtime.run_submission "
        "WHERE tenant_id = :t AND idempotency_key = :k AND is_deleted = false",
        {"t": gateway_stack.tenant_id, "k": key},
    )
    assert int(submissions_after or 0) == 1


async def test_b126_same_key_different_fingerprint_is_mismatch_without_side_effects(
    gateway_stack: GatewayStack,
) -> None:
    key = f"b126-mismatch-{uuid.uuid4().hex[:10]}"
    first = await _run(gateway_stack.runtime_url, gateway_stack, key=key, text="B-126 原始内容")
    assert first["status"] == "COMPLETED", first

    # 同 key 异指纹：真实 HTTP 必须回 409 + IDEMPOTENCY_MISMATCH
    payload = _run_request(gateway_stack, message_id=key, text="B-126 不同内容").model_dump(mode="json")
    async with httpx.AsyncClient(base_url=gateway_stack.runtime_url, timeout=10.0) as client:
        mismatch = await client.post(
            "/v1/runs",
            json=payload,
            headers={"X-Tenant-Id": gateway_stack.tenant_id, "Idempotency-Key": key},
        )
    assert mismatch.status_code == 409, mismatch.text
    assert mismatch.json()["code"] == "IDEMPOTENCY_MISMATCH", mismatch.text
    assert "COMMON_CONFLICT" not in mismatch.text

    side_effects = await _scalar(
        "SELECT count(*) FROM runtime.run_record WHERE tenant_id = :t AND input_text = :text",
        {"t": gateway_stack.tenant_id, "text": "B-126 不同内容"},
    )
    assert int(side_effects or 0) == 0, "异指纹不得产生副作用（新 Run）"


async def test_b126_concurrent_same_key_creates_single_run(gateway_stack: GatewayStack) -> None:
    key = f"b126-concurrent-{uuid.uuid4().hex[:10]}"
    results = await asyncio.gather(
        _run(gateway_stack.runtime_url, gateway_stack, key=key, text="B-126 并发"),
        _run(gateway_stack.runtime_url, gateway_stack, key=key, text="B-126 并发"),
    )
    assert results[0]["run_id"] == results[1]["run_id"], results
    submissions = await _scalar(
        "SELECT count(*) FROM runtime.run_submission WHERE tenant_id = :t AND idempotency_key = :k "
        "AND is_deleted = false",
        {"t": gateway_stack.tenant_id, "k": key},
    )
    assert int(submissions or 0) == 1


async def test_b126_endpoint_and_tenant_are_part_of_the_key(
    gateway_stack: GatewayStack, bind_code: str
) -> None:
    key = f"b126-isolation-{uuid.uuid4().hex[:10]}"
    # 同一 key 走不同 endpoint：彼此独立（不互相重放/冲突）
    run = await _run(gateway_stack.runtime_url, gateway_stack, key=key, text="B-126 隔离")
    assert run["status"] == "COMPLETED", run

    async with httpx.AsyncClient(base_url=gateway_stack.console_url, timeout=10.0) as console:
        bind_response = await console.post(
            BIND_PATH,
            json={
                "channel": "WECOM",
                "bot_id": gateway_stack.bot_id,
                "external_user_id": f"b126-ext-{uuid.uuid4().hex[:8]}",
                "bind_code": bind_code,
            },
            headers={"X-Tenant-Id": gateway_stack.tenant_id, "Idempotency-Key": key},
        )
    assert bind_response.status_code == 200, bind_response.text

    submissions = await _scalar(
        "SELECT count(*) FROM runtime.run_submission "
        "WHERE tenant_id = :t AND idempotency_key = :k AND is_deleted = false",
        {"t": gateway_stack.tenant_id, "k": key},
    )
    assert int(submissions or 0) == 1, "同一 key 在同一 endpoint 只留一行"

    # 幂等表按租户隔离：partial unique 含 tenant_id
    index_def = await _scalar(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'runtime' "
        "AND indexname = 'uq_run_submission_tenant_key_endpoint'",
        {},
    )
    assert index_def is not None
    assert "tenant_id" in str(index_def) and "idempotency_key" in str(index_def)
    assert str(index_def).upper().count("WHERE") == 1  # partial index（软删除条件）


async def test_b126_bind_and_new_replay_have_single_side_effect(
    gateway_stack: GatewayStack, bind_code: str
) -> None:
    external_user_id = f"b126-bind-{uuid.uuid4().hex[:8]}"
    key = f"b126-bind-key-{uuid.uuid4().hex[:8]}"
    payload = {
        "channel": "WECOM",
        "bot_id": gateway_stack.bot_id,
        "external_user_id": external_user_id,
        "bind_code": bind_code,
    }
    async with httpx.AsyncClient(base_url=gateway_stack.console_url, timeout=10.0) as console:
        headers = {"X-Tenant-Id": gateway_stack.tenant_id, "Idempotency-Key": key}
        first = await console.post(BIND_PATH, json=payload, headers=headers)
        replay = await console.post(BIND_PATH, json=payload, headers=headers)
    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert first.json()["data"]["platform_user_id"] == replay.json()["data"]["platform_user_id"]
    identities = await _scalar(
        "SELECT count(*) FROM control.channel_identity "
        "WHERE tenant_id = :t AND external_user_id = :u AND is_deleted = false",
        {"t": gateway_stack.tenant_id, "u": external_user_id},
    )
    assert int(identities or 0) == 1, "同 key 重放不得产生第二条身份"

    # /new 同命令重放不创建第二会话
    new_key = f"b126-new-{uuid.uuid4().hex[:10]}"
    client = RuntimeClient(gateway_stack.runtime_url)
    try:
        first_conversation = await client.create_conversation(
            gateway_stack.agent_id,
            gateway_stack.platform_user_id,
            tenant_id=gateway_stack.tenant_id,
            idempotency_key=new_key,
        )
        replay_conversation = await client.create_conversation(
            gateway_stack.agent_id,
            gateway_stack.platform_user_id,
            tenant_id=gateway_stack.tenant_id,
            idempotency_key=new_key,
        )
    finally:
        await client.aclose()
    # Runtime 侧按 Idempotency-Key 持久重放（design §3.4.2）：同 key 不加第二会话
    assert first_conversation["conversation_id"] == replay_conversation["conversation_id"]
    conversations = await _scalar(
        "SELECT count(*) FROM runtime.conversation WHERE tenant_id = :t AND agent_id = :a",
        {"t": gateway_stack.tenant_id, "a": gateway_stack.agent_id},
    )
    assert int(conversations or 0) >= 1


async def test_b126_cleanup_leaves_no_idempotency_residue(gateway_stack: GatewayStack) -> None:
    await purge_tenant()
    for table in (
        "runtime.run_submission",
        "runtime.run_record",
        "runtime.conversation",
        "control.channel_identity",
        "control.bind_code",
    ):
        assert await count_tenant_rows(table) == 0, table
