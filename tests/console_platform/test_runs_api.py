"""API-18 `GET /api/v1/runs/{run_id}`：Console 侧的 Run 只读投影。

真实边界：真实 Console HTTP/session → 真实 PostgreSQL（`runtime.run_record` +
`runtime.canonical_event`）。本视图**不转调 Runtime**（它没有对外读接口），走的是
`harness-data.md` 允许的跨 Owner Schema 只读聚合投影。

两条硬约束在本文件里各有用例钉住：**租户谓词**（跨租户与不存在同码）与
**只投影结构、不投影内容**（`input_text` 与事件 payload 都不外传）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_engine, get_session_factory
from muad_console_platform.infrastructure.repositories.run_query_repository import TIMELINE_LIMIT
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from console_platform.conftest import (
    TenantContext,  # noqa: F401  (fixture re-export)
    )

#: 刻意取一个不可能出现在元数据里的串，用来证明它没被投影出去。
SECRET_INPUT = "用户原话-do-not-leak-9f2c"
SECRET_PAYLOAD = "payload-do-not-leak-71ab"

_INSERT_AGENT = text(
    """
    INSERT INTO control.agent_definition
        (id, tenant_id, key, name, description, instructions, model_id,
         runtime_config_json, revision, enabled)
    VALUES (:agent_id, :tenant_id, :key, :agent_name, NULL, '', :model_id,
            '{}'::jsonb, 1, true)
    """
)
_INSERT_PLATFORM_USER = text(
    """
    INSERT INTO control.platform_user (id, tenant_id, user_code, display_name, status)
    VALUES (:user_id, :tenant_id, :user_code, :user_name, 'ACTIVE')
    """
)
_INSERT_CONVERSATION = text(
    """
    INSERT INTO runtime.conversation (id, tenant_id, user_id, agent_id)
    VALUES (:conversation_id, :tenant_id, :user_id, :agent_id)
    """
)
_INSERT_RUN = text(
    """
    INSERT INTO runtime.run_record
        (id, tenant_id, conversation_id, user_id, agent_id, status, input_text,
         trace_id, start_time, end_time, cancel_requested, error_code, error_message, deadline_at)
    VALUES (:run_id, :tenant_id, :conversation_id, :user_id, :agent_id, :status,
            :input_text, :trace_id, now(), now(), false, :error_code, :error_message,
            now() + interval '5 minutes')
    """
)
_INSERT_EVENT = text(
    """
    INSERT INTO runtime.canonical_event
        (id, tenant_id, conversation_id, run_id, seq, stream_type, event_type, payload_json)
    VALUES (:event_id, :tenant_id, :conversation_id, :run_id, :seq, :stream_type,
            :event_type, cast(:payload AS jsonb))
    """
)
_INSERT_INTERRUPT = text(
    """
    INSERT INTO runtime.run_interrupt
        (id, tenant_id, run_id, conversation_id, interrupt_type, prompt_text,
         options_json, status)
    VALUES (:interrupt_id, :tenant_id, :run_id, :conversation_id, 'CONFIRM', 'continue?',
            '[]'::jsonb, 'WAITING')
    """
)
_INSERT_OPERATION = text(
    """
    INSERT INTO runtime.tool_operation
        (id, tenant_id, run_id, actor_user_id, source_tool_call_id, completion_mode, status,
         task_id, task_snapshot_hash, submission_json, input_hash, error_phase, error_code,
         submitted_at, completed_at)
    VALUES (:operation_id, :tenant_id, :run_id, :actor_user_id, :call_id, :mode, :status,
            :task_id, :snapshot_hash, cast(:submission AS jsonb), :input_hash,
            :error_phase, :error_code, now(), :completed_at)
    """
)
_SNAPSHOT_HASH = "sha256:" + "a" * 64
_INSERT_SNAPSHOT = text(
    """
    INSERT INTO runtime.runtime_snapshot
        (id, tenant_id, run_id, schema_version, agent_revision, model_revision, agent_json,
         model_json, skill_catalog_json, mcp_catalog_json, policy_json, prompt_template_version,
         content_hash)
    VALUES (:snapshot_id, :tenant_id, :run_id, 1, 1, 1, '{}'::jsonb, '{}'::jsonb,
            '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, '1', :content_hash)
    """
)
_INSERT_CONTINUATION = text(
    """
    INSERT INTO runtime.run_continuation
        (id, tenant_id, run_id, snapshot_id, phase, wait_generation, ready, context_upto_seq,
         consumed_event_seq, turns, tool_calls, checkpoint_schema_version, runner_state_json)
    VALUES (:continuation_id, :tenant_id, :run_id, :snapshot_id, 'BEFORE_MODEL',
            :wait_generation, :ready, 0, 0, 0, 0, 1, '{}'::jsonb)
    """
)
_INSERT_TASK = text(
    """
    INSERT INTO task.task_execution
        (id, tenant_id, source_run_id, agent_id, actor_user_id, intent_key, skill_id,
         skill_artifact_id, trigger_type, execution_mode, task_type, status, input_json,
         execution_snapshot_schema_version, execution_snapshot_json, snapshot_hash,
         idempotency_key, priority, attempt, max_attempts, not_before, delivery_mode,
         delivery_status, delivery_key, delivery_attempts)
    VALUES (:task_id, :tenant_id, :run_id, :agent_id, :actor_user_id, 'run-operation',
            :skill_id, :artifact_id, 'IMMEDIATE', 'ASYNC', 'SKILL', :status, '{}'::jsonb,
            1, '{}'::jsonb, :snapshot_hash, :idempotency_key, 100, 1, 1, now(), 'NONE',
            'NONE', :delivery_key, 0)
    """
)

_CLEANUP = tuple(
    statement.replace("tenant_id = :tenant_id", "tenant_id LIKE :tenant_prefix")
    for statement in (
        "DELETE FROM runtime.tool_control_outbox WHERE tenant_id = :tenant_id",
        "DELETE FROM runtime.tool_result_inbox WHERE tenant_id = :tenant_id",
        "DELETE FROM runtime.run_continuation WHERE tenant_id = :tenant_id",
        "DELETE FROM runtime.tool_operation WHERE tenant_id = :tenant_id",
        "DELETE FROM runtime.canonical_event WHERE tenant_id = :tenant_id",
        "DELETE FROM runtime.run_interrupt WHERE tenant_id = :tenant_id",
        "DELETE FROM runtime.runtime_snapshot WHERE tenant_id = :tenant_id",
        "DELETE FROM runtime.run_record WHERE tenant_id = :tenant_id",
        "DELETE FROM runtime.conversation WHERE tenant_id = :tenant_id",
        "DELETE FROM task.task_execution WHERE tenant_id = :tenant_id",
        "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
        "DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id",
    )
)


@dataclass(frozen=True)
class RunSeed:
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    agent_id: uuid.UUID
    actor_user_id: uuid.UUID
    agent_name: str
    user_name: str
    trace_id: str


@pytest.fixture(autouse=True)
async def cleanup(tenant: TenantContext) -> AsyncIterator[None]:
    """按 `tenant_id` 前缀清干净：跨租户用例会再落一份 `<本租户>-other` 的行。

    `test-<uuid>` 里没有 LIKE 通配符，前缀匹配是本文件自己造的租户的精确边界。
    """
    yield
    session_factory = get_session_factory()
    async with session_factory() as session:
        for statement in _CLEANUP:
            await session.execute(text(statement), {"tenant_prefix": f"{tenant.tenant_id}%"})
        await session.commit()


async def _seed_run(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    tenant_id: str | None = None,
    status: str = "COMPLETED",
    error_code: str | None = None,
    error_message: str | None = None,
) -> RunSeed:
    owner = tenant_id or tenant.tenant_id
    agent_id, user_id, conversation_id, run_id = (uuid.uuid4() for _ in range(4))
    seed = RunSeed(
        run_id=run_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        actor_user_id=user_id,
        agent_name=f"Run Agent {run_id.hex[:6]}",
        user_name=f"Run User {run_id.hex[:6]}",
        trace_id=f"trace-{run_id.hex[:12]}",
    )
    await session.execute(
        _INSERT_AGENT,
        {
            "agent_id": agent_id,
            "tenant_id": owner,
            "key": f"run-agent-{agent_id.hex[:8]}",
            "agent_name": seed.agent_name,
            "model_id": tenant.model_id,
        },
    )
    await session.execute(
        _INSERT_PLATFORM_USER,
        {
            "user_id": user_id,
            "tenant_id": owner,
            "user_code": f"run-user-{user_id.hex[:8]}",
            "user_name": seed.user_name,
        },
    )
    await session.execute(
        _INSERT_CONVERSATION,
        {
            "conversation_id": conversation_id,
            "tenant_id": owner,
            "user_id": user_id,
            "agent_id": agent_id,
        },
    )
    await session.execute(
        _INSERT_RUN,
        {
            "run_id": run_id,
            "tenant_id": owner,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "status": status,
            "input_text": SECRET_INPUT,
            "trace_id": seed.trace_id,
            "error_code": error_code,
            "error_message": error_message,
        },
    )
    return seed


async def _seed_event(
    session: AsyncSession,
    tenant: TenantContext,
    seed: RunSeed,
    *,
    seq: int,
    event_type: str,
    stream_type: str | None = None,
) -> None:
    await session.execute(
        _INSERT_EVENT,
        {
            "event_id": uuid.uuid4(),
            "tenant_id": tenant.tenant_id,
            "conversation_id": seed.conversation_id,
            "run_id": seed.run_id,
            "seq": seq,
            "stream_type": stream_type,
            "event_type": event_type,
            "payload": f'{{"text": "{SECRET_PAYLOAD}"}}',
        },
    )


async def _seed_interrupt(session: AsyncSession, tenant: TenantContext, seed: RunSeed) -> None:
    await session.execute(
        _INSERT_INTERRUPT,
        {
            "interrupt_id": uuid.uuid4(),
            "tenant_id": tenant.tenant_id,
            "run_id": seed.run_id,
            "conversation_id": seed.conversation_id,
        },
    )


async def _seed_operation(
    session: AsyncSession,
    tenant: TenantContext,
    seed: RunSeed,
    *,
    mode: str = "JOIN",
    status: str = "TASK_ACCEPTED",
    task_id: uuid.UUID | None = None,
    error_phase: str | None = None,
    error_code: str | None = None,
    submission: str = "{}",
    call_id: str | None = None,
    completed_at: bool = False,
) -> uuid.UUID:
    """种一条 operation；默认不含 input/result 的 submission JSON（可塞 canary 证明不外传）。"""
    operation_id = uuid.uuid4()
    await session.execute(
        _INSERT_OPERATION,
        {
            "operation_id": operation_id,
            "tenant_id": tenant.tenant_id,
            "run_id": seed.run_id,
            "actor_user_id": seed.actor_user_id,
            "call_id": call_id or f"call-{operation_id.hex[:8]}",
            "mode": mode,
            "status": status,
            "task_id": task_id,
            "snapshot_hash": _SNAPSHOT_HASH,
            "submission": submission,
            "input_hash": _SNAPSHOT_HASH,
            "error_phase": error_phase,
            "error_code": error_code,
            "completed_at": datetime.now(UTC) if completed_at else None,
        },
    )
    return operation_id


async def _seed_task(
    session: AsyncSession,
    tenant: TenantContext,
    seed: RunSeed,
    *,
    status: str = "RUNNING",
) -> uuid.UUID:
    task_id = uuid.uuid4()
    await session.execute(
        _INSERT_TASK,
        {
            "task_id": task_id,
            "tenant_id": tenant.tenant_id,
            "run_id": seed.run_id,
            "agent_id": seed.agent_id,
            "actor_user_id": seed.actor_user_id,
            "skill_id": uuid.uuid4(),
            "artifact_id": uuid.uuid4(),
            "status": status,
            "snapshot_hash": _SNAPSHOT_HASH,
            "idempotency_key": str(task_id),
            "delivery_key": f"task:{task_id}:final",
        },
    )
    return task_id


async def _seed_continuation(
    session: AsyncSession,
    tenant: TenantContext,
    seed: RunSeed,
    *,
    ready: bool,
    wait_generation: int,
) -> None:
    snapshot_id = uuid.uuid4()
    await session.execute(
        _INSERT_SNAPSHOT,
        {
            "snapshot_id": snapshot_id,
            "tenant_id": tenant.tenant_id,
            "run_id": seed.run_id,
            "content_hash": _SNAPSHOT_HASH,
        },
    )
    await session.execute(
        _INSERT_CONTINUATION,
        {
            "continuation_id": uuid.uuid4(),
            "tenant_id": tenant.tenant_id,
            "run_id": seed.run_id,
            "snapshot_id": snapshot_id,
            "wait_generation": wait_generation,
            "ready": ready,
        },
    )


async def _run(tenant: TenantContext, **kwargs: object) -> RunSeed:
    async with get_session_factory()() as session:
        async with session.begin():
            return await _seed_run(session, tenant, **kwargs)  # type: ignore[arg-type]


async def test_run_detail_projects_metadata_and_structural_outline(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seed = await _run(tenant)
    async with get_session_factory()() as session:
        async with session.begin():
            # 形状必须与生产同形：`event_type` 是落库后的**业务名**（RUN_CREATED/ASSISTANT_DELTA/
            # MODEL_CALL_STARTED…），SSE 名只出现在 `stream_type` 里。按 SSE 名播种会让
            # 「轮廓剔除」看起来生效，而线上照旧（`run_query_repository.STREAMING_EVENT_TYPES`）。
            for seq, (event_type, stream_type) in enumerate(
                (
                    ("RUN_CREATED", "run.created"),
                    ("ASSISTANT_DELTA", "message.delta"),
                    ("MODEL_CALL_STARTED", "model.started"),
                    ("TOOL_CALL_STARTED", "tool.started"),
                    ("ASSISTANT_DELTA", "message.delta"),
                    ("MODEL_CALL_COMPLETED", "model.completed"),
                    ("RUN_COMPLETED", "run.completed"),
                ),
                start=1,
            ):
                await _seed_event(
                    session, tenant, seed, seq=seq, event_type=event_type, stream_type=stream_type
                )

    response = await client.get(f"/api/v1/runs/{seed.run_id}")
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["run_id"] == str(seed.run_id)
    assert data["status"] == "COMPLETED"
    # 名称在同一 SQL 内 JOIN 补齐（前端不该拿裸 UUID 当标签）
    assert data["agent_name"] == seed.agent_name
    assert data["actor_name"] == seed.user_name
    assert data["trace_id"] == seed.trace_id
    assert data["timeline_truncated"] is False

    # 轮廓只剔流式增量（token 级，行数可达数千）；模型调用边界与工具调用一样**保留**
    assert [item["event_type"] for item in data["timeline"]] == [
        "RUN_CREATED",
        "MODEL_CALL_STARTED",
        "TOOL_CALL_STARTED",
        "MODEL_CALL_COMPLETED",
        "RUN_COMPLETED",
    ]
    assert [item["seq"] for item in data["timeline"]] == [1, 3, 4, 6, 7]


async def test_run_detail_does_not_leak_input_text_or_event_payload(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """Console 至今零暴露对话原文；这条把「不回退」钉住，而不是靠约定。"""
    seed = await _run(tenant)
    async with get_session_factory()() as session:
        async with session.begin():
            await _seed_event(
                session, tenant, seed, seq=1, event_type="run.created", stream_type=None
            )

    response = await client.get(f"/api/v1/runs/{seed.run_id}")
    assert response.status_code == 200
    assert SECRET_INPUT not in response.text
    assert SECRET_PAYLOAD not in response.text
    assert "input_text" not in response.text


async def test_run_detail_is_tenant_scoped(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """跨租户与不存在同码：不泄漏「这个 id 在别的租户里存在」。

    租户取自**登录账号**（`AccountTenantId`），改 `X-Tenant-Id` 请求头不起作用——所以这里
    把 Run 落成另一个租户的行，再以本租户的账号去查，这才真正走到租户谓词。
    """
    foreign = await _run(tenant, tenant_id=f"{tenant.tenant_id}-other")
    missing = await _run(tenant)

    other = await client.get(f"/api/v1/runs/{foreign.run_id}")
    assert other.status_code == 404
    assert other.json()["code"] == "COMMON_NOT_FOUND"
    # 同时确认：同一个请求打自己的 Run 是通的（否则上面的 404 可能只是别的原因）
    own = await client.get(f"/api/v1/runs/{missing.run_id}")
    assert own.status_code == 200, own.text

    absent = await client.get(f"/api/v1/runs/{uuid.uuid4()}")
    assert absent.status_code == 404
    assert absent.json()["code"] == "COMMON_NOT_FOUND"


async def test_soft_deleted_run_is_not_found(client: AsyncClient, tenant: TenantContext) -> None:
    seed = await _run(tenant)
    async with get_session_factory()() as session:
        async with session.begin():
            await session.execute(
                text("UPDATE runtime.run_record SET is_deleted = true WHERE id = :run_id"),
                {"run_id": seed.run_id},
            )

    response = await client.get(f"/api/v1/runs/{seed.run_id}")
    assert response.status_code == 404


async def test_run_outline_reports_truncation(client: AsyncClient, tenant: TenantContext) -> None:
    """超出上限时如实置 `truncated`，不假装完整。"""
    seed = await _run(tenant)
    async with get_session_factory()() as session:
        async with session.begin():
            for seq in range(1, TIMELINE_LIMIT + 6):
                await _seed_event(
                    session, tenant, seed, seq=seq, event_type="tool.completed", stream_type=None
                )

    response = await client.get(f"/api/v1/runs/{seed.run_id}")
    data = response.json()["data"]
    assert data["timeline_truncated"] is True
    assert len(data["timeline"]) == TIMELINE_LIMIT


async def test_run_detail_issues_a_bounded_number_of_queries(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """详情 + 轮廓 = 两条往返；名称靠 JOIN 补齐，不得逐行回查（N+1 守卫）。"""
    seed = await _run(tenant)
    async with get_session_factory()() as session:
        async with session.begin():
            for seq in range(1, 6):
                await _seed_event(
                    session, tenant, seed, seq=seq, event_type="tool.completed", stream_type=None
                )

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        response = await client.get(f"/api/v1/runs/{seed.run_id}")
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert response.status_code == 200
    touching = [s for s in statements if "runtime.run_record" in s or "canonical_event" in s]
    assert len(touching) == 2, statements


async def test_run_detail_reports_waiting_projection(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """API-05：等待起点/原因与 pending 计数；提交未受理 ⇒ SUBMISSION。"""
    seed = await _run(tenant, status="WAITING_TOOL")
    secret = "submission-secret-do-not-leak-77af"
    async with get_session_factory()() as session:
        async with session.begin():
            await _seed_event(
                session, tenant, seed, seq=1, event_type="RUN_WAITING_TOOL",
                stream_type="run.waiting_tool",
            )
            await _seed_continuation(session, tenant, seed, ready=False, wait_generation=2)
            await _seed_operation(
                session, tenant, seed, mode="JOIN", status="SUBMIT_PENDING",
                submission=f'{{"input": "{secret}"}}',
            )
            await _seed_operation(session, tenant, seed, mode="DETACH", status="SUBMIT_PENDING")
            await _seed_operation(session, tenant, seed, mode="JOIN", status="TASK_ACCEPTED")
            await _seed_operation(session, tenant, seed, mode="JOIN", status="COMPLETED")

    response = await client.get(f"/api/v1/runs/{seed.run_id}")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "WAITING_TOOL"
    assert data["waiting_reason"] == "SUBMISSION"
    # 未终态 JOIN：SUBMIT_PENDING + TASK_ACCEPTED；COMPLETED 不再算依赖
    assert data["pending_join_count"] == 2
    assert data["pending_submission_count"] == 2
    assert data["continuation_count"] == 2
    assert data["waiting_since"] is not None
    assert data["deadline_at"] is not None
    assert secret not in response.text
    assert "submission_json" not in response.text
    assert SECRET_INPUT not in response.text
    assert SECRET_PAYLOAD not in response.text


async def test_run_detail_waiting_reasons_resume_ready_and_task_result(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """结果已到但尚未 claim ⇒ RESUME_READY；只有待定 JOIN 任务 ⇒ TASK_RESULT。"""
    ready_seed = await _run(tenant, status="WAITING_TOOL")
    task_seed = await _run(tenant, status="WAITING_TOOL")
    async with get_session_factory()() as session:
        async with session.begin():
            await _seed_continuation(
                session, tenant, ready_seed, ready=True, wait_generation=1
            )
            await _seed_operation(
                session, tenant, ready_seed, mode="JOIN", status="RESULT_RECEIVED"
            )
            await _seed_continuation(
                session, tenant, task_seed, ready=False, wait_generation=1
            )
            await _seed_operation(session, tenant, task_seed, mode="JOIN", status="RUNNING")

    ready = (await client.get(f"/api/v1/runs/{ready_seed.run_id}")).json()["data"]
    assert ready["waiting_reason"] == "RESUME_READY"
    assert ready["pending_join_count"] == 1 and ready["pending_submission_count"] == 0

    pending = (await client.get(f"/api/v1/runs/{task_seed.run_id}")).json()["data"]
    assert pending["waiting_reason"] == "TASK_RESULT"
    assert pending["pending_join_count"] == 1 and pending["pending_submission_count"] == 0


async def test_run_detail_waiting_input_and_terminal_do_not_report_tool_reason(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """WAITING_INPUT 与终态：`waiting_reason` 为 null；等待起点取等待中断时间。"""
    waiting_input = await _run(tenant, status="WAITING_INPUT")
    completed = await _run(tenant, status="COMPLETED")
    bare_waiting = await _run(tenant, status="WAITING_TOOL")
    async with get_session_factory()() as session:
        async with session.begin():
            await _seed_interrupt(session, tenant, waiting_input)
            await _seed_event(
                session, tenant, bare_waiting, seq=1, event_type="RUN_WAITING_TOOL",
                stream_type="run.waiting_tool",
            )

    input_data = (await client.get(f"/api/v1/runs/{waiting_input.run_id}")).json()["data"]
    assert input_data["status"] == "WAITING_INPUT"
    assert input_data["waiting_reason"] is None
    assert input_data["waiting_since"] is not None
    assert input_data["pending_join_count"] == 0
    assert input_data["pending_submission_count"] == 0
    assert input_data["continuation_count"] == 0

    bare = (await client.get(f"/api/v1/runs/{bare_waiting.run_id}")).json()["data"]
    # WAITING_TOOL 但没有 operation 依赖（不应发生）：不编造“等待任务结果”
    assert bare["status"] == "WAITING_TOOL"
    assert bare["waiting_reason"] is None
    assert bare["waiting_since"] is not None
    assert bare["pending_join_count"] == 0 and bare["pending_submission_count"] == 0

    done = (await client.get(f"/api/v1/runs/{completed.run_id}")).json()["data"]
    assert done["waiting_reason"] is None and done["waiting_since"] is None


async def test_run_operations_paginates_with_default_and_boundaries(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """API-06：默认 15、上限 100；total 与 items 同条件（16 条跨页一致）。"""
    seed = await _run(tenant, status="WAITING_TOOL")
    async with get_session_factory()() as session:
        async with session.begin():
            for index in range(16):
                await _seed_operation(
                    session, tenant, seed, mode="JOIN", status="TASK_ACCEPTED",
                    call_id=f"call-{index:02d}",
                )

    first = (await client.get(f"/api/v1/runs/{seed.run_id}/operations")).json()["data"]
    assert first["page"] == 1 and first["page_size"] == 15 and first["total"] == 16
    assert len(first["items"]) == 15

    second = (
        await client.get(f"/api/v1/runs/{seed.run_id}/operations?page=2")
    ).json()["data"]
    assert second["total"] == 16 and len(second["items"]) == 1

    full = (
        await client.get(f"/api/v1/runs/{seed.run_id}/operations?page_size=100")
    ).json()["data"]
    assert full["page_size"] == 100 and len(full["items"]) == 16

    too_large = await client.get(f"/api/v1/runs/{seed.run_id}/operations?page_size=101")
    assert too_large.status_code == 422
    zero_size = await client.get(f"/api/v1/runs/{seed.run_id}/operations?page_size=0")
    assert zero_size.status_code == 422
    zero_page = await client.get(f"/api/v1/runs/{seed.run_id}/operations?page=0")
    assert zero_page.status_code == 422


async def test_run_operations_fields_whitelist_and_submit_failure_semantics(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """字段白名单（无 input/result/hash）；SUBMIT 失败不伪造成 Task 失败。"""
    seed = await _run(tenant, status="WAITING_TOOL")
    secret = "operation-result-secret-do-not-leak-31cd"
    async with get_session_factory()() as session:
        async with session.begin():
            running_task = await _seed_task(session, tenant, seed, status="RUNNING")
            failed_task = await _seed_task(session, tenant, seed, status="FAILED")
            submit_failed_with_task = await _seed_operation(
                session, tenant, seed, mode="JOIN", status="FAILED", task_id=running_task,
                error_phase="SUBMIT", error_code="SUBMIT_RETRY_EXHAUSTED", completed_at=True,
                submission=f'{{"result": "{secret}"}}',
            )
            submit_failed_no_task = await _seed_operation(
                session, tenant, seed, mode="DETACH", status="FAILED",
                error_phase="SUBMIT", error_code="SUBMIT_RETRY_EXHAUSTED", completed_at=True,
            )
            execute_failed = await _seed_operation(
                session, tenant, seed, mode="JOIN", status="FAILED", task_id=failed_task,
                error_phase="EXECUTE", error_code="SKILL_FAILED", completed_at=True,
            )

    response = await client.get(f"/api/v1/runs/{seed.run_id}/operations")
    assert response.status_code == 200, response.text
    assert secret not in response.text
    data = response.json()["data"]
    by_id = {item["operation_id"]: item for item in data["items"]}
    assert set(by_id) == {
        str(submit_failed_with_task), str(submit_failed_no_task), str(execute_failed)
    }
    assert set(by_id[str(submit_failed_with_task)]) == {
        "operation_id", "source_tool_call_id", "completion_mode", "status", "error_phase",
        "error_code", "task_id", "task_status", "submitted_at", "completed_at",
    }

    with_task = by_id[str(submit_failed_with_task)]
    assert with_task["status"] == "FAILED" and with_task["error_phase"] == "SUBMIT"
    # 提交失败 ≠ 任务失败：Task 真实状态照实投影
    assert with_task["task_status"] == "RUNNING"
    assert with_task["completed_at"] is not None

    no_task = by_id[str(submit_failed_no_task)]
    assert no_task["task_id"] is None and no_task["task_status"] is None
    assert no_task["completion_mode"] == "DETACH"

    task_failed = by_id[str(execute_failed)]
    assert task_failed["task_status"] == "FAILED" and task_failed["error_phase"] == "EXECUTE"


async def test_run_operations_is_tenant_scoped_and_soft_deleted_absent(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """跨租户/不存在/已软删同码 404；本租户同请求可通（404 不是别的原因）。"""
    foreign = await _run(tenant, tenant_id=f"{tenant.tenant_id}-other")
    own = await _run(tenant, status="WAITING_TOOL")
    async with get_session_factory()() as session:
        async with session.begin():
            await _seed_operation(session, tenant, own, mode="JOIN", status="TASK_ACCEPTED")

    other = await client.get(f"/api/v1/runs/{foreign.run_id}/operations")
    assert other.status_code == 404 and other.json()["code"] == "COMMON_NOT_FOUND"

    absent = await client.get(f"/api/v1/runs/{uuid.uuid4()}/operations")
    assert absent.status_code == 404 and absent.json()["code"] == "COMMON_NOT_FOUND"

    ok_response = await client.get(f"/api/v1/runs/{own.run_id}/operations")
    assert ok_response.status_code == 200, ok_response.text
    assert ok_response.json()["data"]["total"] == 1

    async with get_session_factory()() as session:
        async with session.begin():
            await session.execute(
                text("UPDATE runtime.run_record SET is_deleted = true WHERE id = :run_id"),
                {"run_id": own.run_id},
            )
    deleted = await client.get(f"/api/v1/runs/{own.run_id}/operations")
    assert deleted.status_code == 404
