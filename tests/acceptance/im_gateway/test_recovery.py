"""E-03 / RULE-06 / RULE-snapshot-001: 断流回收、Snapshot 冻结与终态 CAS（integration/E2E）。

不得 Mock 的真实边界：真实 Runtime 进程（Reaper 循环）+ 真实 PostgreSQL（run_record /
runtime_snapshot / canonical_event 逐行回读）+ 真实 Gateway 进程与真实 WS 探针。
验收类不制造 RED（Baseline）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    BOUND_EXTERNAL_USER_ID,
    CHAT_ID,
    GatewayStack,
    ServiceProcess,
    count_tenant_rows,
    purge_tenant,
    restart_process,
)

WAIT_TIMEOUT_SEC = 120.0
REAPER_WAIT_SEC = 100.0


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


async def _wait_for(predicate: Any, *, what: str, timeout: float = WAIT_TIMEOUT_SEC) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.5)
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


async def _push(stack: GatewayStack, *, text: str) -> None:
    probe = stack.ws_probe
    assert probe is not None
    message_id = f"recovery-{uuid.uuid4().hex[:8]}"
    await probe.push_message(  # type: ignore[attr-defined]
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id=BOUND_EXTERNAL_USER_ID,
        text=text,
        reply_id=f"req-{message_id}",
        chat_id=CHAT_ID,
    )


async def _seed_run(stack: GatewayStack, *, status: str, lease_expired: bool) -> str:
    """真实 PG：种入指定状态的 Run；`lease_expired` 时把租约置为已过期（Reaper 候选）。"""
    run_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    await _execute(
        "INSERT INTO runtime.conversation (id, tenant_id, user_id, agent_id, status, last_seq) "
        "VALUES (:id, :t, :u, :a, 'ACTIVE', 0)",
        {
            "id": conversation_id,
            "t": stack.tenant_id,
            "u": stack.platform_user_id,
            "a": stack.agent_id,
        },
    )
    lease = "now() - interval '5 minutes'" if lease_expired else "NULL"
    await _execute(
        "INSERT INTO runtime.run_record "
        "(id, tenant_id, conversation_id, user_id, agent_id, status, input_text, trace_id, "
        "cancel_requested, lease_owner, lease_until) "
        f"VALUES (:id, :t, :c, :u, :a, :status, 'seeded', :trace, false, 'seeded-owner', {lease})",
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
    return str(run_id)


async def _run_row(run_id: str, *columns: str) -> tuple[Any, ...]:
    fields = ", ".join(columns)
    values = await _scalar_row(
        f"SELECT {fields} FROM runtime.run_record WHERE id = :id", {"id": uuid.UUID(run_id)}
    )
    return values


async def _scalar_row(statement: str, params: dict[str, object]) -> tuple[Any, ...]:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            row = (await connection.execute(text(statement), params)).one()
        return tuple(row)
    finally:
        await engine.dispose()


async def test_e03_expired_lease_run_is_reaped_to_failed_abandoned(
    gateway_stack: GatewayStack,
) -> None:
    run_id = await _seed_run(gateway_stack, status="RUNNING", lease_expired=True)
    # 真实 Runtime 进程的 Reaper 循环（默认 30s 间隔）回收租约过期的 RUNNING Run
    await _wait_for(
        lambda: _reaped(run_id),
        what="租约过期的 RUNNING Run 未被 Reaper 回收为 FAILED/RUN_ABANDONED",
        timeout=REAPER_WAIT_SEC,
    )
    status, error_code, end_time = await _run_row(run_id, "status", "error_code", "end_time")
    assert status == "FAILED", status
    assert error_code == "RUN_ABANDONED", error_code
    assert end_time is not None, "回收必须写终态时间"


async def _reaped(run_id: str) -> bool:
    status = await _scalar(
        "SELECT status FROM runtime.run_record WHERE id = :id", {"id": uuid.UUID(run_id)}
    )
    return str(status) == "FAILED"


async def test_e03_terminal_run_and_snapshot_are_not_overwritten(
    gateway_stack: GatewayStack,
) -> None:
    # 先跑一次真实 Run 以产生真实 Snapshot（后续拷贝其内容）
    await _push(gateway_stack, text="为冻结断言准备真实快照")
    await _wait_for(
        lambda: _snapshot_exists(gateway_stack), what="未产生真实快照", timeout=WAIT_TIMEOUT_SEC
    )
    run_id = await _seed_run(gateway_stack, status="COMPLETED", lease_expired=True)
    # 终态 Run + 其 Snapshot（拷贝真实快照内容）
    await _execute(
        "INSERT INTO runtime.runtime_snapshot "
        "(id, tenant_id, run_id, schema_version, agent_revision, model_revision, agent_json, "
        "model_json, skill_catalog_json, mcp_catalog_json, policy_json, prompt_template_version, "
        "content_hash) "
        "SELECT gen_random_uuid(), tenant_id, :r, schema_version, agent_revision, model_revision, "
        "agent_json, model_json, skill_catalog_json, mcp_catalog_json, policy_json, "
        "prompt_template_version, content_hash FROM runtime.runtime_snapshot WHERE tenant_id = :t "
        "ORDER BY create_time DESC LIMIT 1",
        {"r": uuid.UUID(run_id), "t": gateway_stack.tenant_id},
    )
    await _execute(
        "UPDATE runtime.run_record SET snapshot_id = (SELECT id FROM runtime.runtime_snapshot "
        "WHERE run_id = :r) WHERE id = :r",
        {"r": uuid.UUID(run_id)},
    )
    before = await _run_row(run_id, "status", "error_code", "snapshot_id")
    hash_before = await _scalar(
        "SELECT content_hash FROM runtime.runtime_snapshot WHERE run_id = :r", {"r": uuid.UUID(run_id)}
    )

    # 等至少一个 Reaper 周期：终态 Run 即使租约过期也不得被覆盖（终态 CAS 的守卫）
    await asyncio.sleep(35.0)
    after = await _run_row(run_id, "status", "error_code", "snapshot_id")
    hash_after = await _scalar(
        "SELECT content_hash FROM runtime.runtime_snapshot WHERE run_id = :r", {"r": uuid.UUID(run_id)}
    )
    assert after == before, f"终态 Run 被覆盖：{before} -> {after}"
    assert str(hash_after or "") == str(hash_before or "") and hash_before, "Snapshot 必须冻结"


async def _snapshot_exists(stack: GatewayStack) -> bool:
    value = await _scalar(
        "SELECT count(*) FROM runtime.runtime_snapshot WHERE tenant_id = :t", {"t": stack.tenant_id}
    )
    return int(value or 0) >= 1


async def test_rule06_gateway_reports_error_without_creating_run_when_runtime_down(
    gateway_stack: GatewayStack,
) -> None:
    """Runtime 不可达（SSE 未收到任何终态）时：给出可理解文案且不新建 Run。

    说明：纯"流中途断开"的文案由 B-115 的真实 WS 用例覆盖（生产 renderer），
    本用例补充"整条链路不可用"分支的真实行为。
    """
    runtime: ServiceProcess = gateway_stack.processes["runtime"]
    runtime.stop()
    try:
        before = len(_replies(gateway_stack))
        runs_before = await _scalar(
            "SELECT count(*) FROM runtime.run_record WHERE tenant_id = :t",
            {"t": gateway_stack.tenant_id},
        )
        await _push(gateway_stack, text="运行时不可达时的消息")
        await _wait_for(
            lambda: len(_replies(gateway_stack)) > before,
            what="Runtime 不可达时未给出任何回复",
            timeout=WAIT_TIMEOUT_SEC,
        )
        runs_after = await _scalar(
            "SELECT count(*) FROM runtime.run_record WHERE tenant_id = :t",
            {"t": gateway_stack.tenant_id},
        )
        assert int(runs_after or 0) == int(runs_before or 0), "不可达时不得新建 Run"
    finally:
        restart_process(runtime)
        await _wait_for(lambda: _runtime_healthy(gateway_stack), what="Runtime 未恢复", timeout=60.0)


async def _runtime_healthy(stack: GatewayStack) -> bool:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            return (await client.get(f"{stack.runtime_url}/healthz")).status_code == 200
    except httpx.HTTPError:
        return False


async def test_e03_cleanup_leaves_no_recovery_residue(gateway_stack: GatewayStack) -> None:
    await purge_tenant()
    for table in ("runtime.run_record", "runtime.runtime_snapshot", "runtime.canonical_event"):
        assert await count_tenant_rows(table) == 0, table
