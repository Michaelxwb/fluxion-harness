"""Run 可观测性浏览器 E2E 的种子（S-20..S-24 / E-20..E-22 / B-20 / B-21）。

真实 Console 只读聚合投影 `runtime.run_record` / `runtime.canonical_event` /
`runtime.tool_operation` 与 `task.task_execution`；本种子按真实列写入（含 `input_text` /
`result_json` 的原文标记，供 S-24 反查泄漏），不 mock 业务响应。S-20 的任务状态变化与 B-20
的分页收缩由本 CLI 的 `ready`/`resume`/`shrink` 在真实 PG 上完成，浏览器只观察投影。

租户由 `E2E_RUN_OBS_TENANT` 指定，须与 Console 的 `DEFAULT_TENANT_ID` 一致
（浏览器请求不带 X-Tenant-Id，Console 回落默认租户）。

CLI：`create`（清空重播全量种子） / `ready` / `resume` / `shrink` / `restore` / `cleanup` /
`counts`。`ready`/`resume`/`shrink`/`restore` 都读 `--out` 的状态文件（`create` 写入），
只改业务行，不动账号会话。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from muad_agent_runtime.infrastructure.models.async_tools import RunContinuation, ToolOperation
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunRecord,
    RuntimeSnapshot,
    ToolCallAudit,
)
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import (
    ROLE_ADMIN,
    ROLE_BUILDER,
    ConsoleAccount,
)
from muad_console_platform.infrastructure.models.control import (
    AgentDefinition,
    ModelDefinition,
    PlatformUser,
)
from muad_contracts import (
    CompletionMode,
    OperationErrorPhase,
    OperationStatus,
    RunStatus,
)
from sqlalchemy import text

TENANT = os.environ.get("E2E_RUN_OBS_TENANT", "run-obs-browser")
OTHER_TENANT = os.environ.get("E2E_RUN_OBS_OTHER_TENANT", f"{TENANT}-other")
USERNAME = os.environ.get("E2E_RUN_OBS_USERNAME", "run-obs-admin")
PASSWORD = os.environ.get("E2E_RUN_OBS_PASSWORD", "run-obs-password")
VIEWER_USERNAME = os.environ.get("E2E_RUN_OBS_VIEWER_USERNAME", "run-obs-viewer")
VIEWER_PASSWORD = os.environ.get("E2E_RUN_OBS_VIEWER_PASSWORD", "run-obs-viewer-password")

# S-23：等待起点的 UTC 时间固定，浏览器（timezoneId=Asia/Shanghai）应渲染为 +8 的 11:04:05。
WAITING_SINCE = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
DEADLINE_AT = datetime(2026, 1, 3, 3, 4, 5, tzinfo=UTC)
WAITING_SINCE_SHANGHAI = "2026-01-02 11:04:05"

# S-24：只做「原文/结果/凭据不出现在投影」的反查标记（非真实执行凭据）。
INPUT_MARKER = "RUN-OBS-SECRET-INPUT"
RESULT_MARKER = "RUN-OBS-SECRET-RESULT"
CREDENTIAL_MARKER = "RUN-OBS-SECRET-CREDENTIAL"

# S-24：时间线 > 200 触发后端 truncated。
CONTENT_EVENT_COUNT = 205

CLEANUP_STATEMENTS = (
    "DELETE FROM runtime.tool_call_audit WHERE tenant_id = :t",
    "DELETE FROM runtime.tool_operation WHERE tenant_id = :t",
    "DELETE FROM runtime.run_continuation WHERE tenant_id = :t",
    "DELETE FROM runtime.canonical_event WHERE tenant_id = :t",
    "DELETE FROM runtime.runtime_snapshot WHERE tenant_id = :t",
    "DELETE FROM runtime.run_record WHERE tenant_id = :t",
    "DELETE FROM runtime.conversation WHERE tenant_id = :t",
    "DELETE FROM task.task_execution WHERE tenant_id = :t",
    "DELETE FROM control.console_session WHERE account_id IN "
    "(SELECT id FROM control.console_account WHERE tenant_id = :t)",
    "DELETE FROM control.console_account WHERE tenant_id = :t",
    "DELETE FROM control.agent_definition WHERE tenant_id = :t",
    "DELETE FROM control.model_definition WHERE tenant_id = :t",
    "DELETE FROM control.platform_user WHERE tenant_id = :t",
)

TABLES = (
    "runtime.run_record",
    "runtime.canonical_event",
    "runtime.tool_operation",
    "runtime.run_continuation",
    "runtime.tool_call_audit",
    "task.task_execution",
    "control.console_account",
)


def _load_state(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _save_state(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _uid(state: dict[str, Any], key: str) -> uuid.UUID:
    """状态文件里的 id 是字符串；raw SQL 参数显式转 UUID，避免驱动端类型推断。"""
    return uuid.UUID(state[key])


def _task(
    *,
    tenant: str,
    agent_id: uuid.UUID,
    actor_id: uuid.UUID,
    intent: str,
    status: str,
    source_run_id: uuid.UUID | None = None,
    source_operation_id: uuid.UUID | None = None,
    completion_mode: CompletionMode | None = None,
    input_json: dict[str, Any] | None = None,
    result_json: dict[str, Any] | None = None,
    error_code: str | None = None,
    started: bool = True,
    finished: bool = False,
) -> TaskExecution:
    now = datetime.now(UTC)
    task_id = uuid.uuid4()
    return TaskExecution(
        id=task_id,
        tenant_id=tenant,
        agent_id=agent_id,
        actor_user_id=actor_id,
        intent_key=intent,
        skill_id=uuid.uuid4(),
        skill_artifact_id=uuid.uuid4(),
        trigger_type="IMMEDIATE",
        execution_mode="ASYNC",
        task_type="SKILL",
        status=status,
        input_json=input_json or {},
        result_json=result_json,
        error_code=error_code,
        source_run_id=source_run_id,
        source_operation_id=source_operation_id,
        completion_mode=completion_mode,
        execution_snapshot_schema_version=1,
        execution_snapshot_json={"schema_version": 1},
        snapshot_hash="sha256:" + "f" * 64,
        idempotency_key=f"e2e-run-obs-{task_id}",
        priority=100,
        attempt=1,
        max_attempts=3,
        not_before=now - timedelta(minutes=5),
        deadline_at=now + timedelta(hours=24),
        started_at=now - timedelta(minutes=5) if started else None,
        finished_at=now if finished else None,
        delivery_mode="NONE",
        delivery_status="NONE",
        delivery_key=f"task:{task_id}:final",
        delivery_attempts=0,
    )


def _operation(
    *,
    tenant: str,
    run_id: uuid.UUID,
    actor_id: uuid.UUID,
    call_id: str,
    mode: CompletionMode,
    status: OperationStatus,
    task_id: uuid.UUID | None = None,
    error_phase: OperationErrorPhase | None = None,
    error_code: str | None = None,
    submitted_at: datetime | None = None,
    completed_at: datetime | None = None,
    submission_json: dict[str, Any] | None = None,
) -> ToolOperation:
    return ToolOperation(
        id=uuid.uuid4(),
        tenant_id=tenant,
        run_id=run_id,
        actor_user_id=actor_id,
        source_tool_call_id=call_id,
        completion_mode=mode,
        status=status,
        task_id=task_id,
        task_snapshot_hash="sha256:" + "a" * 64,
        submission_json=submission_json or {},
        input_hash="sha256:" + "b" * 64,
        error_phase=error_phase,
        error_code=error_code,
        submitted_at=submitted_at,
        completed_at=completed_at,
    )


def _event(
    *,
    tenant: str,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    seq: int,
    event_type: str,
    create_time: datetime,
    stream_type: str | None = None,
    artifact_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> CanonicalEvent:
    return CanonicalEvent(
        id=uuid.uuid4(),
        tenant_id=tenant,
        conversation_id=conversation_id,
        run_id=run_id,
        seq=seq,
        event_type=event_type,
        stream_type=stream_type,
        artifact_id=artifact_id,
        payload_json=payload or {},
        create_time=create_time,
    )


def _conversation(*, tenant: str, user_id: uuid.UUID, agent_id: uuid.UUID, title: str) -> Conversation:
    return Conversation(
        id=uuid.uuid4(),
        tenant_id=tenant,
        user_id=user_id,
        agent_id=agent_id,
        title=title,
        status="ACTIVE",
    )


def _tool_audit(
    *,
    tenant: str,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    call_id: str,
    tool_name: str,
    start_time: datetime,
) -> ToolCallAudit:
    """浏览器从运行审计进入 Run 详情的真实关联行（TOOL 审计）。"""
    return ToolCallAudit(
        id=uuid.uuid4(),
        tenant_id=tenant,
        run_id=run_id,
        conversation_id=conversation_id,
        tool_call_id=call_id,
        tool_name=tool_name,
        tool_kind="ASYNC",
        prepared_args_hash="sha256:" + "e" * 64,
        args_preview_json={},
        status="OK",
        start_time=start_time,
        end_time=start_time + timedelta(minutes=1),
        latency_ms=60_000,
    )


async def _create(state_path: str) -> None:
    now = datetime.now(UTC)
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            model = ModelDefinition(
                tenant_id=TENANT,
                key=f"e2e-run-obs-model-{uuid.uuid4().hex[:8]}",
                name="Run Obs E2E Model",
                model_id="gpt-4o-mini",
                base_url="https://api.example.com/v1",
                enabled=True,
            )
            session.add(model)
            await session.flush()
            agent = AgentDefinition(
                tenant_id=TENANT,
                key=f"e2e-run-obs-agent-{uuid.uuid4().hex[:8]}",
                name="Run 观测浏览器助手",
                instructions="run observability e2e",
                model_id=model.id,
                revision=1,
                enabled=True,
            )
            session.add(agent)
            user = PlatformUser(
                tenant_id=TENANT,
                user_code=f"e2e-run-obs-u-{uuid.uuid4().hex[:8]}",
                display_name="Run Obs Actor",
            )
            session.add(user)
            await session.flush()

            session.add_all(
                [
                    ConsoleAccount(
                        tenant_id=TENANT,
                        username=USERNAME,
                        display_name="Run Obs Admin",
                        password_hash=hash_password(PASSWORD),
                        role=ROLE_ADMIN,
                    ),
                    ConsoleAccount(
                        tenant_id=TENANT,
                        username=VIEWER_USERNAME,
                        display_name="Run Obs Viewer",
                        password_hash=hash_password(VIEWER_PASSWORD),
                        role=ROLE_BUILDER,
                    ),
                ]
            )

            # ---- S-20：真实 JOIN 等待 Run（初始 TASK_RESULT，`ready`/`resume` 推进） ----
            wait_conversation = _conversation(
                tenant=TENANT, user_id=user.id, agent_id=agent.id, title="waiting run"
            )
            session.add(wait_conversation)
            wait_run_id = uuid.uuid4()
            session.add(
                RunRecord(
                    id=wait_run_id,
                    tenant_id=TENANT,
                    conversation_id=wait_conversation.id,
                    user_id=user.id,
                    agent_id=agent.id,
                    status=RunStatus.WAITING_TOOL,
                    input_text="run observability browser seed (waiting)",
                    trace_id=f"trace-run-obs-wait-{uuid.uuid4().hex[:8]}",
                    start_time=WAITING_SINCE - timedelta(minutes=10),
                    deadline_at=DEADLINE_AT,
                )
            )
            wait_snapshot = RuntimeSnapshot(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                run_id=wait_run_id,
                agent_revision=1,
                model_revision=1,
                agent_json={},
                model_json={},
                skill_catalog_json=[],
                mcp_catalog_json=[],
                prompt_template_version="v1",
                content_hash="sha256:" + "c" * 64,
            )
            session.add(wait_snapshot)
            # 先落 run_record/runtime_snapshot，再建引用它们的 run_continuation：
            # 无 relationship 的普通 FK 列不参与 SQLAlchemy 的插入排序，不显式 flush
            # 会被按 mapper 名排在 run_record 之前插入而触发外键违例。
            await session.flush()
            wait_task = _task(
                tenant=TENANT,
                agent_id=agent.id,
                actor_id=user.id,
                intent="run_obs_wait_task",
                status="RUNNING",
                source_run_id=wait_run_id,
                completion_mode=CompletionMode.JOIN,
            )
            session.add(wait_task)
            session.add(
                RunContinuation(
                    tenant_id=TENANT,
                    run_id=wait_run_id,
                    snapshot_id=wait_snapshot.id,
                    wait_generation=1,
                    ready=False,
                    context_upto_seq=5,
                    runner_state_json={},
                )
            )
            wait_operation = _operation(
                tenant=TENANT,
                run_id=wait_run_id,
                actor_id=user.id,
                call_id="call_wait_join",
                mode=CompletionMode.JOIN,
                status=OperationStatus.SUBMITTED,
                task_id=wait_task.id,
                submitted_at=WAITING_SINCE - timedelta(minutes=5),
            )
            session.add(wait_operation)
            wait_events = (
                ("RUN_CREATED", WAITING_SINCE - timedelta(minutes=10), "run.created"),
                ("TOOL_CALL_STARTED", WAITING_SINCE - timedelta(minutes=6), "tool.started"),
                ("TOOL_SUBMISSION_PENDING", WAITING_SINCE - timedelta(minutes=5), "tool.submission.pending"),
                ("TOOL_TASK_ACCEPTED", WAITING_SINCE - timedelta(minutes=4), "tool.task.accepted"),
                ("RUN_WAITING_TOOL", WAITING_SINCE, "run.waiting.tool"),
            )
            for seq, (event_type, create_time, stream_type) in enumerate(wait_events, start=1):
                session.add(
                    _event(
                        tenant=TENANT,
                        conversation_id=wait_conversation.id,
                        run_id=wait_run_id,
                        seq=seq,
                        event_type=event_type,
                        stream_type=stream_type,
                        create_time=create_time,
                    )
                )
            # S-20 的浏览器入口：运行审计的 TOOL 行关联本 Run（审计列表真实投影）。
            wait_audit = _tool_audit(
                tenant=TENANT,
                run_id=wait_run_id,
                conversation_id=wait_conversation.id,
                call_id="call_wait_join",
                tool_name="run_obs_async_tool",
                start_time=WAITING_SINCE - timedelta(minutes=6),
            )
            wait_audit_id = wait_audit.id
            session.add(wait_audit)

            # ---- S-22 / B-20：16 条 operation（15/16 分页边界；含 SUBMIT_PENDING 与 DETACH） ----
            page_conversation = _conversation(
                tenant=TENANT, user_id=user.id, agent_id=agent.id, title="paged operations run"
            )
            session.add(page_conversation)
            page_run_id = uuid.uuid4()
            session.add(
                RunRecord(
                    id=page_run_id,
                    tenant_id=TENANT,
                    conversation_id=page_conversation.id,
                    user_id=user.id,
                    agent_id=agent.id,
                    status=RunStatus.RUNNING,
                    input_text="run observability browser seed (paged)",
                    trace_id=f"trace-run-obs-page-{uuid.uuid4().hex[:8]}",
                    start_time=now - timedelta(minutes=30),
                )
            )
            await session.flush()
            session.add(
                _event(
                    tenant=TENANT,
                    conversation_id=page_conversation.id,
                    run_id=page_run_id,
                    seq=1,
                    event_type="RUN_CREATED",
                    stream_type="run.created",
                    create_time=now - timedelta(minutes=30),
                )
            )
            page_audit = _tool_audit(
                tenant=TENANT,
                run_id=page_run_id,
                conversation_id=page_conversation.id,
                call_id="call_page_audit",
                tool_name="run_obs_page_tool",
                start_time=now - timedelta(minutes=29),
            )
            page_audit_id = page_audit.id
            session.add(page_audit)

            # p2：已受理 DETACH（S-22「独立后台任务」）
            page_detach_task = _task(
                tenant=TENANT,
                agent_id=agent.id,
                actor_id=user.id,
                intent="run_obs_page_detach",
                status="COMPLETED",
                source_run_id=page_run_id,
                completion_mode=CompletionMode.DETACH,
                finished=True,
            )
            session.add(page_detach_task)
            # p3：未知 task_status（B-20：不得显示成「任务失败」）
            page_unknown_task = _task(
                tenant=TENANT,
                agent_id=agent.id,
                actor_id=user.id,
                intent="run_obs_page_unknown",
                status="PAUSED",
                source_run_id=page_run_id,
                completion_mode=CompletionMode.JOIN,
            )
            session.add(page_unknown_task)
            # p5：跨租户 task_id：本租户投影里 task_status 为 null，点击请求 404（E-21）
            other_task = _task(
                tenant=OTHER_TENANT,
                agent_id=agent.id,
                actor_id=user.id,
                intent="run_obs_other_task",
                status="RUNNING",
            )
            session.add(other_task)

            page_operations: list[ToolOperation] = [
                _operation(
                    tenant=TENANT,
                    run_id=page_run_id,
                    actor_id=user.id,
                    call_id="call_page_01",
                    mode=CompletionMode.JOIN,
                    status=OperationStatus.SUBMIT_PENDING,
                ),
                _operation(
                    tenant=TENANT,
                    run_id=page_run_id,
                    actor_id=user.id,
                    call_id="call_page_02",
                    mode=CompletionMode.DETACH,
                    status=OperationStatus.COMPLETED,
                    task_id=page_detach_task.id,
                    submitted_at=now - timedelta(minutes=20),
                    completed_at=now - timedelta(minutes=10),
                ),
                _operation(
                    tenant=TENANT,
                    run_id=page_run_id,
                    actor_id=user.id,
                    call_id="call_page_03",
                    mode=CompletionMode.JOIN,
                    status=OperationStatus.TASK_ACCEPTED,
                    task_id=page_unknown_task.id,
                    submitted_at=now - timedelta(minutes=19),
                ),
                _operation(
                    tenant=TENANT,
                    run_id=page_run_id,
                    actor_id=user.id,
                    call_id="call_page_04",
                    mode=CompletionMode.JOIN,
                    status=OperationStatus.FAILED,
                    error_phase=OperationErrorPhase.SUBMIT,
                    error_code="SUBMISSION_TIMEOUT",
                    completed_at=now - timedelta(minutes=18),
                ),
                _operation(
                    tenant=TENANT,
                    run_id=page_run_id,
                    actor_id=user.id,
                    call_id="call_page_05",
                    mode=CompletionMode.JOIN,
                    status=OperationStatus.SUBMITTED,
                    task_id=other_task.id,
                    submitted_at=now - timedelta(minutes=17),
                ),
            ]
            for index in range(6, 17):
                terminal_task = _task(
                    tenant=TENANT,
                    agent_id=agent.id,
                    actor_id=user.id,
                    intent=f"run_obs_page_task_{index}",
                    status="COMPLETED" if index % 3 else "FAILED",
                    source_run_id=page_run_id,
                    completion_mode=CompletionMode.JOIN,
                    finished=True,
                    error_code=None if index % 3 else "TOOL_EXECUTION_FAILED",
                )
                session.add(terminal_task)
                page_operations.append(
                    _operation(
                        tenant=TENANT,
                        run_id=page_run_id,
                        actor_id=user.id,
                        call_id=f"call_page_{index:02d}",
                        mode=CompletionMode.JOIN,
                        status=OperationStatus.COMPLETED if index % 3 else OperationStatus.FAILED,
                        task_id=terminal_task.id,
                        error_phase=None if index % 3 else OperationErrorPhase.EXECUTE,
                        error_code=None if index % 3 else "TOOL_EXECUTION_FAILED",
                        submitted_at=now - timedelta(minutes=16 - index % 5),
                        completed_at=now - timedelta(minutes=5),
                    )
                )
            session.add_all(page_operations)

            # ---- B-20：无异步操作空态 ----
            empty_conversation = _conversation(
                tenant=TENANT, user_id=user.id, agent_id=agent.id, title="empty operations run"
            )
            session.add(empty_conversation)
            empty_run_id = uuid.uuid4()
            session.add(
                RunRecord(
                    id=empty_run_id,
                    tenant_id=TENANT,
                    conversation_id=empty_conversation.id,
                    user_id=user.id,
                    agent_id=agent.id,
                    status=RunStatus.RUNNING,
                    input_text="run observability browser seed (empty)",
                    trace_id=f"trace-run-obs-empty-{uuid.uuid4().hex[:8]}",
                    start_time=now - timedelta(minutes=20),
                )
            )
            await session.flush()
            empty_audit = _tool_audit(
                tenant=TENANT,
                run_id=empty_run_id,
                conversation_id=empty_conversation.id,
                call_id="call_empty_audit",
                tool_name="run_obs_empty_tool",
                start_time=now - timedelta(minutes=19),
            )
            empty_audit_id = empty_audit.id
            session.add(empty_audit)

            # ---- S-24：原文/结果/凭据标记 + 205 条时间线（truncated） ----
            content_conversation = _conversation(
                tenant=TENANT, user_id=user.id, agent_id=agent.id, title="content markers run"
            )
            session.add(content_conversation)
            content_run_id = uuid.uuid4()
            content_task = _task(
                tenant=TENANT,
                agent_id=agent.id,
                actor_id=user.id,
                intent="run_obs_content_task",
                status="COMPLETED",
                source_run_id=content_run_id,
                completion_mode=CompletionMode.JOIN,
                input_json={"text": f"{INPUT_MARKER}-task-input"},
                result_json={"text": f"{RESULT_MARKER}-task-result"},
                finished=True,
            )
            session.add(content_task)
            session.add(
                RunRecord(
                    id=content_run_id,
                    tenant_id=TENANT,
                    conversation_id=content_conversation.id,
                    user_id=user.id,
                    agent_id=agent.id,
                    status=RunStatus.WAITING_TOOL,
                    input_text=f"{INPUT_MARKER}-run-input",
                    trace_id=f"trace-run-obs-content-{uuid.uuid4().hex[:8]}",
                    start_time=now - timedelta(minutes=40),
                    deadline_at=now + timedelta(hours=2),
                )
            )
            await session.flush()
            content_audit = _tool_audit(
                tenant=TENANT,
                run_id=content_run_id,
                conversation_id=content_conversation.id,
                call_id="call_content_audit",
                tool_name="run_obs_content_tool",
                start_time=now - timedelta(minutes=39),
            )
            content_audit_id = content_audit.id
            session.add(content_audit)
            content_snapshot = RuntimeSnapshot(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                run_id=content_run_id,
                agent_revision=1,
                model_revision=1,
                agent_json={},
                model_json={},
                skill_catalog_json=[],
                mcp_catalog_json=[],
                prompt_template_version="v1",
                content_hash="sha256:" + "d" * 64,
            )
            session.add(content_snapshot)
            # 同 wait 模式：run_continuation 引用 run_record/runtime_snapshot，先显式 flush。
            await session.flush()
            session.add(
                RunContinuation(
                    tenant_id=TENANT,
                    run_id=content_run_id,
                    snapshot_id=content_snapshot.id,
                    wait_generation=1,
                    ready=False,
                    context_upto_seq=CONTENT_EVENT_COUNT,
                    runner_state_json={},
                )
            )
            session.add(
                _operation(
                    tenant=TENANT,
                    run_id=content_run_id,
                    actor_id=user.id,
                    call_id="call_content_join",
                    mode=CompletionMode.JOIN,
                    status=OperationStatus.SUBMITTED,
                    task_id=content_task.id,
                    submitted_at=now - timedelta(minutes=35),
                    submission_json={
                        "args": f"{INPUT_MARKER}-args",
                        "credential": f"{CREDENTIAL_MARKER}",
                    },
                )
            )
            event_cycle = (
                ("ASSISTANT_TURN", "assistant.turn"),
                ("MODEL_CALL_STARTED", "model.started"),
                ("MODEL_CALL_COMPLETED", "model.completed"),
                ("TOOL_CALL_STARTED", "tool.started"),
                ("TOOL_CALL", "tool.completed"),
            )
            for seq in range(1, CONTENT_EVENT_COUNT + 1):
                event_type, stream_type = event_cycle[(seq - 1) % len(event_cycle)]
                if seq == 1:
                    event_type, stream_type = "RUN_CREATED", "run.created"
                elif seq == CONTENT_EVENT_COUNT:
                    event_type, stream_type = "RUN_WAITING_TOOL", "run.waiting.tool"
                session.add(
                    _event(
                        tenant=TENANT,
                        conversation_id=content_conversation.id,
                        run_id=content_run_id,
                        seq=seq,
                        event_type=event_type,
                        stream_type=stream_type,
                        create_time=now - timedelta(minutes=CONTENT_EVENT_COUNT - seq),
                        artifact_id=uuid.uuid4() if seq == 2 else None,
                    )
                )

            # ---- S-21：DETACH 已受理 + 来源 Run 反向链接 ----
            detach_conversation = _conversation(
                tenant=TENANT, user_id=user.id, agent_id=agent.id, title="detach run"
            )
            session.add(detach_conversation)
            detach_run_id = uuid.uuid4()
            detach_task = _task(
                tenant=TENANT,
                agent_id=agent.id,
                actor_id=user.id,
                intent="run_obs_detach_task",
                status="RUNNING",
                source_run_id=detach_run_id,
                completion_mode=CompletionMode.DETACH,
            )
            session.add(detach_task)
            session.add(
                RunRecord(
                    id=detach_run_id,
                    tenant_id=TENANT,
                    conversation_id=detach_conversation.id,
                    user_id=user.id,
                    agent_id=agent.id,
                    status=RunStatus.RUNNING,
                    input_text="run observability browser seed (detach)",
                    trace_id=f"trace-run-obs-detach-{uuid.uuid4().hex[:8]}",
                    start_time=now - timedelta(minutes=15),
                )
            )
            await session.flush()
            session.add(
                _operation(
                    tenant=TENANT,
                    run_id=detach_run_id,
                    actor_id=user.id,
                    call_id="call_detach_01",
                    mode=CompletionMode.DETACH,
                    status=OperationStatus.SUBMITTED,
                    task_id=detach_task.id,
                    submitted_at=now - timedelta(minutes=12),
                )
            )
            session.add_all(
                [
                    _event(
                        tenant=TENANT,
                        conversation_id=detach_conversation.id,
                        run_id=detach_run_id,
                        seq=1,
                        event_type="RUN_CREATED",
                        stream_type="run.created",
                        create_time=now - timedelta(minutes=15),
                    ),
                    _event(
                        tenant=TENANT,
                        conversation_id=detach_conversation.id,
                        run_id=detach_run_id,
                        seq=2,
                        event_type="TOOL_TASK_ACCEPTED",
                        stream_type="tool.task.accepted",
                        create_time=now - timedelta(minutes=12),
                    ),
                ]
            )
            detach_audit = _tool_audit(
                tenant=TENANT,
                run_id=detach_run_id,
                conversation_id=detach_conversation.id,
                call_id="call_detach_01",
                tool_name="run_obs_detach_tool",
                start_time=now - timedelta(minutes=13),
            )
            detach_audit_id = detach_audit.id
            session.add(detach_audit)

            # ---- E-21：另一租户的 Run/Task（本租户查询应 404） ----
            other_conversation = _conversation(
                tenant=OTHER_TENANT, user_id=user.id, agent_id=agent.id, title="other tenant run"
            )
            session.add(other_conversation)
            other_run_id = uuid.uuid4()
            session.add(
                RunRecord(
                    id=other_run_id,
                    tenant_id=OTHER_TENANT,
                    conversation_id=other_conversation.id,
                    user_id=user.id,
                    agent_id=agent.id,
                    status=RunStatus.RUNNING,
                    input_text="other tenant run",
                    trace_id=f"trace-run-obs-other-{uuid.uuid4().hex[:8]}",
                    start_time=now - timedelta(minutes=5),
                )
            )
            await session.flush()

    _save_state(
        state_path,
        {
            "tenant": TENANT,
            "otherTenant": OTHER_TENANT,
            "account": {"username": USERNAME, "password": PASSWORD},
            "viewer": {"username": VIEWER_USERNAME, "password": VIEWER_PASSWORD},
            "waitRunId": str(wait_run_id),
            "waitTaskId": str(wait_task.id),
            "waitOperationId": str(wait_operation.id),
            "waitAuditId": str(wait_audit_id),
            "pageRunId": str(page_run_id),
            "pageAuditId": str(page_audit_id),
            "pageFirstOperationId": str(page_operations[0].id),
            "pageDetachOperationId": str(page_operations[1].id),
            "pageRunLastOperationId": str(page_operations[-1].id),
            "pageRunDetachTaskId": str(page_detach_task.id),
            "pageRunUnknownTaskId": str(page_unknown_task.id),
            "pageRunCrossTaskId": str(other_task.id),
            "emptyRunId": str(empty_run_id),
            "emptyAuditId": str(empty_audit_id),
            "contentRunId": str(content_run_id),
            "contentAuditId": str(content_audit_id),
            "contentTaskId": str(content_task.id),
            "detachRunId": str(detach_run_id),
            "detachTaskId": str(detach_task.id),
            "detachAuditId": str(detach_audit_id),
            "otherRunId": str(other_run_id),
            "otherTaskId": str(other_task.id),
            "markers": {
                "input": INPUT_MARKER,
                "result": RESULT_MARKER,
                "credential": CREDENTIAL_MARKER,
            },
            "waitingSinceUtc": WAITING_SINCE.isoformat(),
            "waitingSinceShanghai": WAITING_SINCE_SHANGHAI,
        },
    )
    print(f"created run-observability seed for tenant {TENANT}")


async def _ready(state_path: str) -> None:
    """S-20 第二步：结果已到但尚未 claim（continuation.ready=true → RESUME_READY）。"""
    state = _load_state(state_path)
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "UPDATE runtime.tool_operation SET status = :status, completed_at = now() "
                    "WHERE id = :operation_id AND tenant_id = :tenant"
                ),
                {
                    "status": OperationStatus.RESULT_RECEIVED.value,
                    "operation_id": _uid(state, "waitOperationId"),
                    "tenant": TENANT,
                },
            )
            await session.execute(
                text(
                    "UPDATE task.task_execution SET status = 'COMPLETED', finished_at = now() "
                    "WHERE id = :task_id AND tenant_id = :tenant"
                ),
                {"task_id": _uid(state, "waitTaskId"), "tenant": TENANT},
            )
            await session.execute(
                text(
                    "UPDATE runtime.run_continuation SET ready = true "
                    "WHERE run_id = :run_id AND tenant_id = :tenant"
                ),
                {"run_id": _uid(state, "waitRunId"), "tenant": TENANT},
            )
            await session.execute(
                text(
                    "INSERT INTO runtime.canonical_event "
                    "(tenant_id, conversation_id, run_id, seq, event_type, "
                    "stream_type, payload_json, create_time) "
                    "SELECT :tenant, conversation_id, run_id, 6, 'BACKGROUND_RESULT', "
                    "'background.result', '{}'::jsonb, now() "
                    "FROM runtime.run_record WHERE id = :run_id"
                ),
                {"run_id": _uid(state, "waitRunId"), "tenant": TENANT},
            )
    print("wait run advanced to RESUME_READY")


async def _resume(state_path: str) -> None:
    """S-20 第三步：接续完成（Run COMPLETED、待处理计数归零）。"""
    state = _load_state(state_path)
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "UPDATE runtime.tool_operation SET status = :status, completed_at = now() "
                    "WHERE id = :operation_id AND tenant_id = :tenant"
                ),
                {
                    "status": OperationStatus.COMPLETED.value,
                    "operation_id": _uid(state, "waitOperationId"),
                    "tenant": TENANT,
                },
            )
            await session.execute(
                text(
                    "UPDATE runtime.run_continuation SET ready = false "
                    "WHERE run_id = :run_id AND tenant_id = :tenant"
                ),
                {"run_id": _uid(state, "waitRunId"), "tenant": TENANT},
            )
            await session.execute(
                text(
                    "UPDATE runtime.run_record SET status = :status, end_time = now() "
                    "WHERE id = :run_id AND tenant_id = :tenant"
                ),
                {"status": RunStatus.COMPLETED.value, "run_id": _uid(state, "waitRunId"), "tenant": TENANT},
            )
            await session.execute(
                text(
                    "INSERT INTO runtime.canonical_event "
                    "(tenant_id, conversation_id, run_id, seq, event_type, "
                    "stream_type, payload_json, create_time) "
                    "SELECT :tenant, conversation_id, run_id, 7, 'RUN_RESUMED', "
                    "'run.resumed', '{}'::jsonb, now() "
                    "FROM runtime.run_record WHERE id = :run_id"
                ),
                {"run_id": _uid(state, "waitRunId"), "tenant": TENANT},
            )
    print("wait run resumed to COMPLETED")


async def _shrink(state_path: str) -> None:
    """B-20：软删最后一条 operation（16→15），使停留在第 2 页的清单回落合法页码。"""
    state = _load_state(state_path)
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "UPDATE runtime.tool_operation SET is_deleted = true "
                    "WHERE id = :operation_id AND tenant_id = :tenant"
                ),
                {"operation_id": _uid(state, "pageRunLastOperationId"), "tenant": TENANT},
            )
    print("page run shrunk to 15 operations")


async def _restore(state_path: str) -> None:
    """恢复 B-20 的软删（供套件内重复运行/乱序执行不脏读）。"""
    state = _load_state(state_path)
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "UPDATE runtime.tool_operation SET is_deleted = false "
                    "WHERE id = :operation_id AND tenant_id = :tenant"
                ),
                {"operation_id": _uid(state, "pageRunLastOperationId"), "tenant": TENANT},
            )
    print("page run restored to 16 operations")


async def _cleanup() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        for tenant in (TENANT, OTHER_TENANT):
            for statement in CLEANUP_STATEMENTS:
                await session.execute(text(statement), {"t": tenant})
        await session.commit()
    print(f"cleaned {TENANT} / {OTHER_TENANT}")


async def _counts() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        parts = []
        for table in TABLES:
            value = await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": TENANT}
            )
            parts.append(f"{table.split('.')[-1]}={int(value or 0)}")
        print(" ".join(parts))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="run-observability e2e seed")
    parser.add_argument(
        "action",
        choices=("create", "ready", "resume", "shrink", "restore", "cleanup", "counts"),
    )
    parser.add_argument("--out", default=os.environ.get("E2E_RUN_OBS_STATE_FILE", ""))
    return parser


async def _run(action: str, out: str) -> int:
    if action == "create":
        await _cleanup()
        if not out:
            raise SystemExit("create 需要 --out（或 E2E_RUN_OBS_STATE_FILE）")
        await _create(out)
    elif action in ("ready", "resume", "shrink", "restore"):
        if not out:
            raise SystemExit(f"{action} 需要 --out（或 E2E_RUN_OBS_STATE_FILE）")
        await {"ready": _ready, "resume": _resume, "shrink": _shrink, "restore": _restore}[action](out)
    elif action == "cleanup":
        await _cleanup()
    elif action == "counts":
        await _counts()
    else:
        raise SystemExit(f"unknown action: {action}")
    return 0


if __name__ == "__main__":
    parsed = _parser().parse_args()
    raise SystemExit(asyncio.run(_run(parsed.action, parsed.out)))
