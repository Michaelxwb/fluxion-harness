"""Agentic 的 Skill 工具执行者：SYNC 直接跑沙箱，ASYNC 走 Task-owned 操作协议。

- **SYNC**：与 Skill 任务同一条执行边界（冻结 artifact → 本地缓存校验 → 沙箱子进程 → egress
  预算/审计）。工具参数在边界就拆成 `input`（只走 stdin）与 `argv`（追加到脚本）；返回值取
  结构化 result（缺省回落 stdout）。
- **ASYNC**：提交给 Runtime API-30（调用身份 `(tenant, execution, call_id)` 唯一），先做
  ready-check；未就绪才把水位交给挂起端口——**结果先到、等待后到**的丢唤醒由这一步消除。
- 工具失败带稳定 `code`（`TaskExecutionError`），由 Kernel 转成同形的工具错误消息；未授权/未知
  工具在注册表层就被拦住，根本到不了这里。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from muad_agent_core.sandbox import (
    WorkspaceDeleting,
    WorkspaceLease,
    WorkspaceQuotaExceeded,
    WorkspaceStore,
    WorkspaceUnavailable,
    workspace_layout_from_settings,
)
from muad_agent_core.skill import (
    SkillArtifactResolver,
    SkillExecutionError,
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillExecutionStatus,
    SkillExecutor,
)
from muad_artifact_store import SkillArtifactCacheError
from muad_common import SharedSettings
from muad_contracts import (
    CompletionMode,
    OperationWatermark,
    ResolvedAgent,
    ResolvedModel,
    ResolvedSkill,
    SkillExecutionMode,
    SkillSource,
    SnapshotSkill,
    TaskStatus,
    TaskType,
    TriggerType,
    WorkspaceRef,
    build_task_snapshot,
    snapshot_hash,
)
from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedLimitExceeded
from muad_platform_sdk.skill_access import SkillPlatformContext
from sqlalchemy import false, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.agentic.suspension import AgenticSuspension, settled_status
from ..application.task_events import EntityType, TaskEventType, append_event
from ..infrastructure.models.task import TaskExecution
from ..infrastructure.task_operation_client import (
    TaskOperationClient,
    TaskOperationError,
    operation_result_content,
)
from .egress_execution import execute_task_skill_with_egress
from .executor import TaskExecutionError, task_env

logger = logging.getLogger("muad_agent_worker.worker.agentic_skills")

VALID_SKILL_SOURCES = frozenset({"SYSTEM", "ADMIN", "USER"})
ASYNC_RECEIPT_KEY = "async_operation"
MIN_TOOL_TIMEOUT_SEC = 1.0


class AgenticSkillTools:
    """把冻结 Skill 变成模型工具：SYNC 执行、ASYNC 提交并挂起。"""

    def __init__(
        self,
        *,
        task: TaskExecution,
        resolver: SkillArtifactResolver,
        executor: SkillExecutor,
        settings: SharedSettings,
        sessions: async_sessionmaker[AsyncSession] | None,
        suspension: AgenticSuspension,
        operations: TaskOperationClient | None,
        workspace_store: WorkspaceStore | None = None,
        create_async_child: bool = True,
    ) -> None:
        self._task = task
        self._resolver = resolver
        self._executor = executor
        self._settings = settings
        self._sessions = sessions
        self._suspension = suspension
        self._operations = operations
        self._workspace_store = workspace_store
        self._create_async_child = create_async_child

    async def call(
        self,
        skill: SnapshotSkill,
        tool_name: str,
        input: Mapping[str, Any],
        argv: Sequence[str],
        *,
        call_id: str,
    ) -> str:
        if skill.execution_mode == "ASYNC":
            return await self._submit(skill, tool_name, input, argv, call_id)
        return await self._execute(skill, input, argv)

    async def _execute(
        self, skill: SnapshotSkill, input: Mapping[str, Any], argv: Sequence[str]
    ) -> str:
        source = skill.source.strip().upper()
        if source not in VALID_SKILL_SOURCES:
            raise TaskExecutionError("SKILL_SOURCE_INVALID", f"invalid frozen skill source: {source!r}")
        lease: WorkspaceLease | None = None
        try:
            workspace, lease = await self._workspace()
            ready_dir = await self._ready(skill)
            request = SkillExecutionRequest(
                ready_dir=ready_dir,
                input=dict(input),
                argv=tuple(argv),
                timeout_sec=self._timeout(),
                source=source,
                workspace_dir=workspace,
                execution_id=str(self._task.id),
                env=task_env(self._task),
                platform_context=self._platform_context(skill),
            )
            result = await execute_task_skill_with_egress(
                self._executor,
                request,
                task=self._task,
                skill_key=self._skill_key(skill),
                settings=self._settings,
                sessions=self._sessions,
            )
        except SkillExecutionError as exc:
            raise TaskExecutionError("SKILL_EXECUTION_FAILED", exc.message) from exc
        except SharedLimitExceeded as exc:
            code = "LOCAL_BUDGET_EXHAUSTED" if exc.kind == "TOTAL" else "LOCAL_RATE_LIMITED"
            raise TaskExecutionError(code, str(exc), retryable=False) from exc
        except MeteringUnavailable:
            raise TaskExecutionError(
                "EGRESS_METERING_UNAVAILABLE", "shared metering unavailable", retryable=False
            ) from None
        finally:
            if lease is not None:
                await self._release(lease)
        return _result_text(result)

    async def _submit(
        self,
        skill: SnapshotSkill,
        tool_name: str,
        input: Mapping[str, Any],
        argv: Sequence[str],
        call_id: str,
    ) -> str:
        if self._operations is None:
            raise TaskExecutionError(
                "ASYNC_OPERATION_UNAVAILABLE", "async operations are not configured", retryable=False
            )
        try:
            view = await self._operations.submit(
                tenant_id=self._task.tenant_id,
                actor_user_id=self._task.actor_user_id,
                execution_id=self._task.id,
                call_id=call_id,
                tool_name=tool_name,
                arguments={"input": dict(input), "argv": list(argv)},
                completion_mode=CompletionMode.JOIN,
            )
        except TaskOperationError as exc:
            raise TaskExecutionError(exc.code, str(exc)) from exc
        if view.result is not None and settled_status(view.result.terminal_status):
            # ready-check：结果早到就别进入等待（避免"结果先到、等待后到"丢唤醒）。
            self._suspension.clear_operation(call_id)
            return operation_result_content(view.result)
        if self._create_async_child:
            await self._ensure_child_task(
                skill, input, argv, call_id=call_id, operation_id=view.operation_id
            )
        self._suspension.stage_operation(
            OperationWatermark(
                operation_id=view.operation_id,
                call_id=call_id,
                status=view.status,
                receipt_seq=view.result.task_event_seq if view.result is not None else 0,
                consumed_event_id=view.result.event_id if view.result is not None else None,
            )
        )
        return json.dumps(
            {
                ASYNC_RECEIPT_KEY: {
                    "operation_id": str(view.operation_id),
                    "status": view.status.value,
                    "tool_name": tool_name,
                }
            },
            ensure_ascii=False,
        )

    async def _ensure_child_task(
        self,
        skill: SnapshotSkill,
        input: Mapping[str, Any],
        argv: Sequence[str],
        *,
        call_id: str,
        operation_id: UUID,
    ) -> UUID:
        """把 ASYNC Skill 提交成一个真实子 Task：由 Worker 正常 claim/lease/crash 恢复执行。

        操作台账仍在 Runtime（`tool_operation.owner_type=TASK`）；子 Task 只是执行载体，
        终态经 `RuntimeResultOutbox` 回写操作结果。重复提交按 operation 幂等。
        """
        if self._sessions is None:
            raise TaskExecutionError(
                "ASYNC_OPERATION_UNAVAILABLE", "async skill child task needs a session factory"
            )
        child_id = uuid4()
        idempotency_key = f"task-operation:{operation_id}:submit"
        snapshot = _child_task_snapshot(self._task, skill, argv)
        now = datetime.now(UTC)
        values = {
            "id": child_id,
            "tenant_id": self._task.tenant_id,
            "parent_id": self._task.id,
            "root_id": self._task.root_id or self._task.id,
            "source_operation_id": operation_id,
            "source_tool_call_id": call_id,
            "completion_mode": CompletionMode.JOIN,
            "agent_id": self._task.agent_id,
            "actor_user_id": self._task.actor_user_id,
            "task_type": TaskType.SKILL.value,
            "skill_id": skill.skill_id,
            "skill_artifact_id": skill.artifact_id,
            "skill_execution_mode": skill.execution_mode,
            "trigger_type": TriggerType.IMMEDIATE.value,
            "status": TaskStatus.QUEUED.value,
            "input_json": dict(input),
            "execution_snapshot_schema_version": snapshot["schema_version"],
            "execution_snapshot_json": snapshot,
            "snapshot_hash": snapshot_hash(snapshot),
            "idempotency_key": idempotency_key,
            "priority": self._task.priority,
            "attempt": 0,
            "max_attempts": 1,
            "not_before": now,
            "deadline_at": self._task.deadline_at,
        }
        async with self._sessions() as session, session.begin():
            inserted = (
                await session.execute(
                    pg_insert(TaskExecution)
                    .values(**values)
                    .on_conflict_do_nothing(
                        index_elements=[TaskExecution.tenant_id, TaskExecution.idempotency_key],
                        index_where=TaskExecution.is_deleted == false(),
                    )
                    .returning(TaskExecution.id)
                )
            ).scalar_one_or_none()
            if inserted is None:
                existing = await session.scalar(
                    select(TaskExecution.id).where(
                        TaskExecution.tenant_id == self._task.tenant_id,
                        TaskExecution.idempotency_key == idempotency_key,
                        TaskExecution.is_deleted.is_(False),
                    )
                )
                if existing is None:
                    raise TaskExecutionError(
                        "ASYNC_OPERATION_UNAVAILABLE", "async skill child task vanished"
                    )
                return existing
            await append_event(
                session,
                tenant_id=self._task.tenant_id,
                entity_type=EntityType.EXECUTION,
                entity_id=child_id,
                event_type=TaskEventType.QUEUED,
                payload={"idempotency_key": idempotency_key},
                occurred_at=now,
            )
        return child_id

    async def _workspace(self) -> tuple[Any, WorkspaceLease | None]:
        if self._workspace_store is None:
            layout = workspace_layout_from_settings(self._settings)
            return (
                layout.workspace_dir(
                    tenant_id=self._task.tenant_id,
                    user_id=str(self._task.actor_user_id),
                    agent_id=str(self._task.agent_id),
                ),
                None,
            )
        try:
            lease = await self._workspace_store.acquire(
                tenant_id=self._task.tenant_id,
                user_id=str(self._task.actor_user_id),
                agent_id=str(self._task.agent_id),
                execution_id=str(self._task.id),
                run_id=str(self._task.source_run_id or ""),
                task_id=str(self._task.id),
                lease_sec=max(60.0, self._timeout() + 30.0),
            )
        except WorkspaceQuotaExceeded as exc:
            raise TaskExecutionError("WORKSPACE_QUOTA_EXCEEDED", str(exc), retryable=False) from exc
        except WorkspaceDeleting as exc:
            raise TaskExecutionError("WORKSPACE_DELETING", str(exc), retryable=False) from exc
        except WorkspaceUnavailable as exc:
            raise TaskExecutionError("WORKSPACE_UNAVAILABLE", str(exc)) from exc
        return lease.workspace_dir, lease

    async def _release(self, lease: WorkspaceLease) -> None:
        assert self._workspace_store is not None
        try:
            await self._workspace_store.refresh_usage(lease)
        except Exception as exc:  # noqa: BLE001 - 统计失败不得覆盖执行结果
            logger.warning("agentic_skill_usage_refresh_failed", extra={"error": type(exc).__name__})
        try:
            await self._workspace_store.release(lease)
        except Exception as exc:  # noqa: BLE001 - 释放失败不得覆盖执行结果
            logger.warning("agentic_skill_workspace_release_failed", extra={"error": type(exc).__name__})

    async def _ready(self, skill: SnapshotSkill) -> Any:
        try:
            return await self._resolver.ensure(
                artifact_id=str(skill.artifact_id),
                storage_key=skill.storage_key,
                checksum=skill.checksum,
            )
        except SkillArtifactCacheError as exc:
            raise TaskExecutionError(exc.code, f"skill artifact rejected: {exc.code}") from exc

    def _timeout(self) -> float:
        remaining = (self._task.deadline_at - datetime.now(UTC)).total_seconds()
        return max(MIN_TOOL_TIMEOUT_SEC, remaining)

    def _skill_key(self, skill: SnapshotSkill) -> str | None:
        key = skill.frontmatter.get("key")
        if isinstance(key, str) and key.strip():
            return key.strip()
        return str(skill.skill_id)

    def _platform_context(self, skill: SnapshotSkill) -> SkillPlatformContext:
        keys = skill.frontmatter.get("platforms", [])
        if not isinstance(keys, list) or any(not isinstance(item, str) or not item for item in keys):
            raise TaskExecutionError("SKILL_PLATFORMS_INVALID", "Invalid Skill platforms", retryable=False)
        return SkillPlatformContext(
            tenant_id=self._task.tenant_id,
            actor_user_id=str(self._task.actor_user_id),
            execution_id=str(self._task.id),
            execution_type="TASK",
            platform_keys=tuple(dict.fromkeys(keys)),
            console_url=self._settings.console_platform_url,
            redis_url=self._settings.redis_url,
            redis_mode=self._settings.redis_mode,
            redis_cluster_seeds=self._settings.redis_cluster_seeds,
            service_token=self._settings.internal_service_token,
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _child_task_snapshot(
    task: TaskExecution, skill: SnapshotSkill, argv: Sequence[str]
) -> dict[str, Any]:
    """子 Task 的 SKILL 快照：单 Skill + 冻结 workspace，与立即 Skill 任务同形。"""
    raw = _mapping(task.execution_snapshot_json)
    agent_raw = _mapping(raw.get("agent"))
    model_raw = _mapping(raw.get("model"))
    model_json = _mapping(model_raw.get("model_json"))
    agent = ResolvedAgent(
        id=UUID(str(agent_raw.get("id"))),
        key=str(agent_raw.get("key") or "task-agent"),
        revision=int(agent_raw.get("revision") or 1),
        instructions=str(agent_raw.get("instructions") or ""),
        runtime_config=dict(_mapping(agent_raw.get("runtime_config"))),
    )
    model = ResolvedModel(
        id=UUID(str(model_raw.get("id"))),
        revision=int(model_raw.get("revision") or 1),
        protocol="OPENAI",
        model_id=str(model_json.get("model_id") or "unknown"),
        base_url=str(model_json.get("base_url") or ""),
        params=dict(_mapping(model_json.get("params"))),
    )
    key = str(skill.frontmatter.get("key") or skill.skill_id)
    resolved = ResolvedSkill(
        skill_id=skill.skill_id,
        artifact_id=skill.artifact_id,
        key=key,
        name=str(skill.frontmatter.get("name") or key),
        description=str(skill.frontmatter.get("description") or ""),
        version=skill.version,
        checksum=skill.checksum,
        storage_key=skill.storage_key,
        execution_mode=SkillExecutionMode(skill.execution_mode),
        frontmatter=dict(skill.frontmatter),
        source=SkillSource(skill.source),
    )
    return build_task_snapshot(
        agent=agent,
        model=model,
        skill=resolved,
        workspace=WorkspaceRef(
            tenant_id=task.tenant_id,
            user_id=task.actor_user_id,
            agent_id=task.agent_id,
        ),
        argv=tuple(argv),
    )


def _result_text(result: SkillExecutionResult) -> str:
    if result.status is not SkillExecutionStatus.SUCCEEDED:
        raise TaskExecutionError(
            "SKILL_EXECUTION_FAILED", result.stderr or f"skill finished with {result.status.value}"
        )
    if result.result is not None:
        return json.dumps(dict(result.result), ensure_ascii=False)
    return result.stdout


__all__ = ["ASYNC_RECEIPT_KEY", "AgenticSkillTools"]
