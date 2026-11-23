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

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
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
from muad_contracts.egress_identity import egress_operation_key
from muad_contracts.skill_invocation import validate_skill_argv
from muad_logging import get_logger
from muad_platform_sdk.host_egress_execution import HostEgressExecution
from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedLimitExceeded
from muad_platform_sdk.skill_access import SkillPlatformContext
from sqlalchemy import false, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.agentic.async_dispatch import AsyncCallDispatcher
from ..application.agentic.suspension import AgenticSuspension
from ..application.task_events import EntityType, TaskEventType, append_event
from ..infrastructure.models.task import TaskExecution
from ..infrastructure.task_operation_client import (
    TaskOperationClient,
    TaskOperationError,
)
from .egress_execution import execute_task_skill_with_egress
from .executor import TaskExecutionError, task_env

logger = get_logger("muad_agent_worker.worker.agentic_skills")

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
        skills: Mapping[UUID, SnapshotSkill] | None = None,
        egress: HostEgressExecution | None = None,
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
        self._skills = dict(skills or {})
        self._egress = egress
        self._dispatcher = (
            AsyncCallDispatcher(operations, ensure_child=self._ensure_child_from_recovery)
            if operations is not None
            else None
        )

    async def call(
        self,
        skill: SnapshotSkill,
        tool_name: str,
        input: Mapping[str, Any],
        argv: Sequence[str],
        *,
        call_id: str,
        args_hash: str | None = None,
    ) -> str:
        if skill.execution_mode == "ASYNC":
            return await self._submit(skill, tool_name, input, argv, call_id, args_hash=args_hash)
        return await self._execute(skill, input, argv, call_id)

    async def _execute(
        self, skill: SnapshotSkill, input: Mapping[str, Any], argv: Sequence[str], call_id: str
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
                operation_key=egress_operation_key("TOOL_CALL", call_id),
                egress=self._egress,
            )
        except SkillExecutionError as exc:
            message = exc.message
            if exc.platform_sessions:
                message = f"{message}; {_platform_summary(exc.platform_sessions)}"
            raise TaskExecutionError(
                "SKILL_EXECUTION_FAILED",
                message,
                platform_sessions=exc.platform_sessions,
            ) from exc
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
        *,
        args_hash: str | None = None,
    ) -> str:
        if self._dispatcher is None:
            raise TaskExecutionError(
                "ASYNC_OPERATION_UNAVAILABLE", "async operations are not configured", retryable=False
            )
        try:
            state = await self._dispatcher.ensure(
                tenant_id=self._task.tenant_id,
                actor_user_id=self._task.actor_user_id,
                execution_id=self._task.id,
                call_id=call_id,
                tool_name=tool_name,
                arguments={"input": dict(input), "argv": list(argv)},
                completion_mode=CompletionMode.JOIN,
                recovered=False,
                input_hash=args_hash,
            )
        except TaskOperationError as exc:
            raise TaskExecutionError(exc.code, str(exc)) from exc
        if state.ready:
            # ready-check：结果早到就别进入等待（避免"结果先到、等待后到"丢唤醒）。
            self._suspension.clear_operation(call_id)
            return state.content or ""
        self._suspension.stage_operation(
            OperationWatermark(
                operation_id=state.operation_id,
                call_id=call_id,
                status=state.status,
            )
        )
        return json.dumps(
            {
                ASYNC_RECEIPT_KEY: {
                    "operation_id": str(state.operation_id),
                    "status": state.status.value,
                    "tool_name": tool_name,
                }
            },
            ensure_ascii=False,
        )

    async def load_skill(self, skill_key: str) -> str:
        skill = next((item for item in self._skills.values() if item.key == skill_key), None)
        if skill is None:
            raise TaskExecutionError("SKILL_NOT_EFFECTIVE", f"skill is not effective: {skill_key}")
        root = await self._ready(skill)
        instructions = str(
            await asyncio.to_thread(
                (Path(root) / "SKILL.md").read_text,
                encoding="utf-8",
                errors="replace",
            )
        )
        return json.dumps(
            {"manifest": {"key": skill.key, "name": skill.frontmatter.get("name", skill.key)},
             "instructions": instructions},
            ensure_ascii=False,
        )

    async def read_skill_resource(self, skill_key: str, relative: str) -> str:
        skill = next((item for item in self._skills.values() if item.key == skill_key), None)
        if skill is None:
            raise TaskExecutionError("SKILL_NOT_EFFECTIVE", f"skill is not effective: {skill_key}")
        root = Path(await self._ready(skill)).resolve()
        target = (root / relative).resolve()
        if root not in target.parents and target != root:
            raise TaskExecutionError("SKILL_RESOURCE_NOT_FOUND", f"resource is not readable: {relative}")
        try:
            return str(
                await asyncio.to_thread(
                    target.read_text, encoding="utf-8", errors="replace"
                )
            )
        except OSError as exc:
            raise TaskExecutionError(
                "SKILL_RESOURCE_NOT_FOUND", f"resource is not readable: {relative}"
            ) from exc

    async def search_skills(self, query: str) -> str:
        term = query.casefold().strip()
        matches = [
            {"key": skill.key, "name": skill.frontmatter.get("name", skill.key)}
            for skill in self._skills.values()
            if not term or term in skill.key.casefold()
            or term in str(skill.frontmatter.get("name", "")).casefold()
        ]
        return json.dumps({"total_matches": len(matches), "skills": matches}, ensure_ascii=False)

    async def ensure_child(
        self,
        *,
        call_id: str,
        operation_id: UUID,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> UUID | None:
        return await self._ensure_child_from_recovery(
            call_id, operation_id, tool_name, arguments
        )

    async def _ensure_child_from_recovery(
        self,
        call_id: str,
        operation_id: UUID,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> UUID | None:
        if not self._create_async_child:
            return None
        skill_id = _skill_id_from_tool_name(tool_name)
        skill = self._skills.get(skill_id) if skill_id is not None else None
        if skill is None:
            raise TaskExecutionError(
                "ASYNC_OPERATION_UNAVAILABLE",
                f"frozen skill is not available for recovery: {tool_name}",
            )
        raw_input = arguments.get("input")
        input_data = dict(raw_input) if isinstance(raw_input, Mapping) else {}
        raw_argv = arguments.get("argv")
        argv = validate_skill_argv(raw_argv) if raw_argv is not None else ()
        return await self._ensure_child_task(
            skill,
            input_data,
            argv,
            call_id=call_id,
            operation_id=operation_id,
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
        return skill.key or str(skill.skill_id)

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


def _platform_summary(report: dict[str, object]) -> str:
    platforms = report.get("platforms")
    parts: list[str] = []
    for item in platforms if isinstance(platforms, list) else []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        available = item.get("available")
        if available is True:
            parts.append(f"{key}=ok")
            continue
        if available is False:
            error = item.get("error")
            err = error if isinstance(error, dict) else {}
            detail = err.get("detail")
            det = detail if isinstance(detail, dict) else {}
            status = det.get("http_status")
            message = det.get("message")
            suffix = f"(http={status})" if status is not None else ""
            if isinstance(message, str) and message:
                suffix += f" {message[:80]}"
            parts.append(f"{key}={err.get('code') or 'ERROR'}{suffix}")
            continue
        reason = item.get("reason")
        parts.append(f"{key}=not_checked" + (f"({reason})" if reason else ""))
    return ("platform_sessions: " + "; ".join(parts))[:300]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _skill_id_from_tool_name(name: str) -> UUID | None:
    parts = name.split("::")
    if len(parts) != 3 or parts[0] != "skill":
        return None
    try:
        return UUID(parts[1])
    except ValueError:
        return None


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
    key = skill.key or str(skill.skill_id)
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
