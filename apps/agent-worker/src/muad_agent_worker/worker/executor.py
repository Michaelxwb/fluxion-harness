from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

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
    build_sandboxed_skill_executor,
)
from muad_artifact_store import SkillArtifactCacheError
from muad_common import process_settings
from muad_contracts.skill_invocation import validate_skill_argv
from muad_platform_sdk.skill_access import SkillPlatformContext

from ..application.batch_fanout import (
    BATCH_PLAN_KEY,
    BATCH_REF_KEY,
    PARKED_NOT_BEFORE,
    TASK_TYPE_BATCH,
    BatchFanoutProtocol,
    BatchFanoutStaleError,
    BatchPlan,
    BatchPlanError,
    FanoutSummary,
)
from ..infrastructure.models.task import TaskExecution
from ..metrics import (
    SANDBOX_OUTPUT_LIMIT_METRIC,
    SANDBOX_TIMEOUT_METRIC,
    SKILL_EXECUTION_METRIC,
    record_outcome,
)

logger = logging.getLogger("muad_agent_worker.worker.executor")

EXECUTION_TIMEOUT_SEC = 300.0
#: 剩余时间已经不够一秒时不再把它夹成 0：给子进程一个能正常启动/收尾的下限。
MIN_EXECUTION_TIMEOUT_SEC = 1.0
SKILL_ARTIFACT_KEYS = ("artifact_id", "storage_key", "checksum")
VALID_SKILL_SOURCES = frozenset({"SYSTEM", "ADMIN", "USER"})
# 确定性失败：重跑结果不会变（快照/计划/制品本身有问题），不消耗重试预算直接 FAILED。
NON_RETRYABLE_CODES = frozenset(
    {
        "SKILL_SNAPSHOT_MISSING",
        "BATCH_PLAN_INVALID",
        "BATCH_FANOUT_STALE",
        "BATCH_FANOUT_UNAVAILABLE",
        "SKILL_ARTIFACT_UNAVAILABLE",
        "SKILL_ARTIFACT_INTEGRITY_ERROR",
        "SKILL_RESULT_INVALID",
    }
)
# 传给 Skill 子进程的 Task 上下文（环境变量，不改动 Skill 的 stdin 输入契约）。
ENV_TASK_ID = "MUAD_TASK_ID"
ENV_TASK_IDEMPOTENCY_KEY = "MUAD_TASK_IDEMPOTENCY_KEY"
ENV_TASK_ATTEMPT = "MUAD_TASK_ATTEMPT"
ENV_TASK_EXTERNAL_REF = "MUAD_TASK_EXTERNAL_REF"


class TaskExecutionError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = code not in NON_RETRYABLE_CODES if retryable is None else retryable


def task_env(task: TaskExecution) -> dict[str, str]:
    """Skill 据此做副作用幂等（`idempotency_key` 在 reclaim/重试间恒定）与外部轮询续接。

    `MUAD_TASK_EXTERNAL_REF` 是上次 WAITING 落库的 `external_ref_json`；首次执行为 `{}`。
    """
    return {
        ENV_TASK_ID: str(task.id),
        ENV_TASK_IDEMPOTENCY_KEY: task.idempotency_key,
        ENV_TASK_ATTEMPT: str(task.attempt),
        ENV_TASK_EXTERNAL_REF: json.dumps(task.external_ref_json or {}, ensure_ascii=False),
    }


class TaskExecutorProtocol(Protocol):
    async def execute(self, task: TaskExecution) -> dict[str, Any]: ...


def frozen_skill(task: TaskExecution) -> Mapping[str, Any]:
    """取出快照里与本 Task `skill_artifact_id` 对应的冻结 skill 条目。

    只认 Task 自己的 `execution_snapshot_json`，不查 current 定义——冻结的意义就是
    执行结果不随 Skill/Binding 的后续变更而漂移。按 artifact 精确匹配而不是取第一个，
    快照里即使混入其它 Skill 也不会执行错对象。
    """
    snapshot = task.execution_snapshot_json or {}
    skills = snapshot.get("skills") or []
    expected = str(task.skill_artifact_id)
    entry = next(
        (item for item in skills if isinstance(item, Mapping) and str(item.get("artifact_id")) == expected),
        None,
    )
    if entry is None:
        raise TaskExecutionError(
            "SKILL_SNAPSHOT_MISSING", f"execution snapshot has no frozen skill for artifact {expected}"
        )
    missing = [key for key in SKILL_ARTIFACT_KEYS if not entry.get(key)]
    if missing:
        raise TaskExecutionError("SKILL_SNAPSHOT_MISSING", f"frozen skill missing {missing}")
    return cast(Mapping[str, Any], entry)


def frozen_argv(task: TaskExecution) -> tuple[str, ...]:
    try:
        return validate_skill_argv(task.execution_snapshot_json.get("argv", ()))
    except ValueError as exc:
        raise TaskExecutionError("SKILL_SNAPSHOT_INVALID", str(exc), retryable=False) from exc


def frozen_source(skill: Mapping[str, Any]) -> str:
    """冻结的 Skill 来源决定 policy；缺失/非法一律拒绝执行（RULE-snapshot-001）。"""
    source = skill.get("source")
    if not isinstance(source, str) or not source.strip():
        raise TaskExecutionError(
            "SKILL_SNAPSHOT_MISSING", "frozen skill is missing source", retryable=False
        )
    normalized = source.strip().upper()
    if normalized not in VALID_SKILL_SOURCES:
        raise TaskExecutionError(
            "SKILL_SOURCE_INVALID", f"invalid frozen skill source: {source!r}", retryable=False
        )
    return normalized


@dataclass(frozen=True, slots=True)
class FrozenWorkspace:
    tenant_id: str
    user_id: str
    agent_id: str


def frozen_workspace_identity(task: TaskExecution, skill: Mapping[str, Any]) -> FrozenWorkspace:
    """从冻结 Snapshot 取 tenant/user/Agent 归属；不信任 Task 行的可变列。"""
    raw = skill.get("workspace")
    if not isinstance(raw, Mapping):
        raise TaskExecutionError(
            "SKILL_SNAPSHOT_MISSING", "frozen skill is missing workspace", retryable=False
        )
    tenant_id = raw.get("tenant_id")
    user_id = raw.get("user_id")
    agent_id = raw.get("agent_id")
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise TaskExecutionError(
            "SKILL_SNAPSHOT_MISSING", "frozen workspace tenant is missing", retryable=False
        )
    if not isinstance(user_id, str) or not user_id.strip():
        raise TaskExecutionError(
            "SKILL_SNAPSHOT_MISSING", "frozen workspace user is missing", retryable=False
        )
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise TaskExecutionError(
            "SKILL_SNAPSHOT_MISSING", "frozen workspace agent is missing", retryable=False
        )
    if tenant_id != task.tenant_id:
        raise TaskExecutionError(
            "SKILL_SNAPSHOT_MISSING", "frozen workspace tenant does not match task", retryable=False
        )
    return FrozenWorkspace(tenant_id=tenant_id, user_id=user_id, agent_id=agent_id)


def frozen_workspace(task: TaskExecution, skill: Mapping[str, Any]) -> Path:
    identity = frozen_workspace_identity(task, skill)
    try:
        layout = workspace_layout_from_settings(process_settings())
        return layout.workspace_dir(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            agent_id=identity.agent_id,
        )
    except ValueError as exc:
        raise TaskExecutionError(
            "SKILL_SNAPSHOT_MISSING", f"invalid frozen workspace: {exc}", retryable=False
        ) from exc


class SkillTaskExecutor:
    """按冻结 Snapshot 执行 Skill。

    经 SkillArtifactCache 把 artifact 落到本地缓存、校验 checksum 并等 READY 落盘后
    再从本地目录执行；NFS 只作为权威源被读取一次，绝不在其上直接执行 Python。
    """

    def __init__(
        self,
        resolver: SkillArtifactResolver,
        executor: SkillExecutor | None = None,
        fanout: BatchFanoutProtocol | None = None,
        workspace_store: WorkspaceStore | None = None,
    ) -> None:
        self._resolver = resolver
        self._executor = executor or build_sandboxed_skill_executor(process_settings())
        self._fanout = fanout
        self._workspace_store = workspace_store

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        if task.task_type == TASK_TYPE_BATCH:
            # reclaim 后不重跑 Skill：已有 Child 的 Parent 只等待 fan-in。
            return self._batch_envelope(task)
        skill = frozen_skill(task)
        source = frozen_source(skill)
        lease = None
        if self._workspace_store is None:
            workspace = frozen_workspace(task, skill)
        else:
            identity = frozen_workspace_identity(task, skill)
            lease = await self._acquire_workspace(task, identity)
            workspace = lease.workspace_dir
        try:
            return await self._run_skill(task, skill, source, workspace)
        finally:
            if lease is not None:
                await self._finish_workspace(lease)

    async def _run_skill(
        self,
        task: TaskExecution,
        skill: Mapping[str, Any],
        source: str,
        workspace: Path,
    ) -> dict[str, Any]:
        try:
            ready_dir = await self._resolver.ensure(
                artifact_id=str(skill["artifact_id"]),
                storage_key=str(skill["storage_key"]),
                checksum=str(skill["checksum"]),
            )
        except SkillArtifactCacheError as exc:
            if exc.code == "SKILL_ARTIFACT_INTEGRITY_ERROR":
                # checksum 不匹配是安全事件：拒绝执行并留下可聚合的告警（RULE-skill-001）。
                logger.warning(
                    "skill_artifact_integrity_error skill_id=%s artifact_id=%s",
                    skill.get("skill_id"),
                    skill.get("artifact_id"),
                )
                record_outcome(SKILL_EXECUTION_METRIC, "FAILED", {"source": source})
            raise TaskExecutionError(exc.code, f"skill artifact rejected: {exc.code}") from exc
        try:
            result = await self._executor.execute(
                SkillExecutionRequest(
                    ready_dir=ready_dir,
                    input=task.input_json or {},
                    argv=frozen_argv(task),
                    timeout_sec=self._execution_timeout(task),
                    source=source,
                    workspace_dir=workspace,
                    execution_id=str(task.id),
                    env=task_env(task),
                    platform_context=task_platform_context(task, skill),
                )
            )
        except SkillExecutionError as exc:
            raise TaskExecutionError("SKILL_EXECUTION_FAILED", exc.message) from exc
        self._record_result(source, result)
        payload = dict(result.result) if result.result else {}
        plan = self._parse_plan(payload)
        if plan is not None:
            if self._fanout is None:
                raise TaskExecutionError(
                    "BATCH_FANOUT_UNAVAILABLE", "batch plan returned but fan-out is not configured"
                )
            try:
                summary = await self._fanout.fan_out(task, plan)
            except BatchFanoutStaleError as exc:
                # 父任务已经不归本次执行所有：不写任何东西，等 CAS 拦下收尾。
                raise TaskExecutionError(exc.code, str(exc)) from exc
            return self._batch_envelope(task, summary)
        return {
            "status": str(result.status),
            # `is not None` 而不是真值判断：脚本打印 `{}` 是**一个合法的结构化结果**，
            # 用真值判断会把它折成 `None`，随后 `interpret_execution` 的 stdout 兜底
            # 就把 `"{}"` 当成文本结果覆盖掉它（"空对象优先于 stdout"这条保证会失效）。
            "result": dict(result.result) if result.result is not None else None,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
            "exit_code": result.exit_code,
            "outputs": [
                {
                    "relative_path": output.relative_path,
                    "size": output.size,
                    "checksum": output.checksum,
                }
                for output in result.outputs
            ],
            "output_notice": result.output_notice,
        }

    async def _acquire_workspace(
        self, task: TaskExecution, identity: FrozenWorkspace
    ) -> WorkspaceLease:
        assert self._workspace_store is not None
        try:
            return await self._workspace_store.acquire(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                agent_id=identity.agent_id,
                execution_id=str(task.id),
                run_id=str(task.source_run_id or ""),
                task_id=str(task.id),
                lease_sec=max(60.0, self._execution_timeout(task) + 30.0),
            )
        except WorkspaceQuotaExceeded as exc:
            raise TaskExecutionError(
                "WORKSPACE_QUOTA_EXCEEDED", str(exc), retryable=False
            ) from exc
        except WorkspaceDeleting as exc:
            raise TaskExecutionError(
                "WORKSPACE_DELETING", str(exc), retryable=False
            ) from exc
        except WorkspaceUnavailable as exc:
            raise TaskExecutionError("WORKSPACE_UNAVAILABLE", str(exc)) from exc

    async def _finish_workspace(self, lease: WorkspaceLease) -> None:
        assert self._workspace_store is not None
        try:
            await self._workspace_store.refresh_usage(lease)
        except Exception as exc:  # noqa: BLE001 - 统计失败不得覆盖执行结果
            logger.warning(
                "skill_workspace_usage_refresh_failed execution_id=%s error=%s",
                lease.execution_id,
                type(exc).__name__,
            )
        try:
            await self._workspace_store.release(lease)
        except Exception as exc:  # noqa: BLE001 - 释放失败不得覆盖执行结果
            logger.warning(
                "skill_workspace_release_failed execution_id=%s error=%s",
                lease.execution_id,
                type(exc).__name__,
            )

    def _record_result(self, source: str, result: SkillExecutionResult) -> None:
        labels = {"source": source}
        record_outcome(SKILL_EXECUTION_METRIC, result.status.value, labels)
        if result.output_notice:
            record_outcome(SANDBOX_OUTPUT_LIMIT_METRIC, result.output_notice, labels)
        if result.status is SkillExecutionStatus.TIMED_OUT:
            record_outcome(SANDBOX_TIMEOUT_METRIC, result.status.value, labels)

    def _parse_plan(self, payload: Mapping[str, Any]) -> BatchPlan | None:
        try:
            return BatchPlan.parse(payload.get(BATCH_PLAN_KEY))
        except BatchPlanError as exc:
            raise TaskExecutionError(exc.code, str(exc)) from exc

    def _execution_timeout(self, task: TaskExecution) -> float:
        """Skill 超时 = `min(单次上限, 到 deadline 的剩余时间)`（评审 #8）。

        不夹这一下，deadline 只剩 10 秒的任务照样能再跑满 300 秒——超时的副作用真实发生
        了，只是最后被写成 FAILED 而已；夹住它，过期之后执行器主动收手。
        """
        remaining = (task.deadline_at - datetime.now(UTC)).total_seconds()
        return max(MIN_EXECUTION_TIMEOUT_SEC, min(EXECUTION_TIMEOUT_SEC, remaining))

    def _batch_envelope(self, task: TaskExecution, summary: FanoutSummary | None = None) -> dict[str, Any]:
        """Parent 以 WAITING 收尾：`not_before` 远期停放，等 fan-in 聚合后置终态。"""
        batch = (
            summary.as_ref()
            if summary is not None
            else dict((task.external_ref_json or {}).get(BATCH_REF_KEY) or {})
        )
        return {
            "status": str(SkillExecutionStatus.SUCCEEDED),
            "result": {
                "wait": {
                    "external_ref": {BATCH_REF_KEY: batch},
                    "not_before": PARKED_NOT_BEFORE.isoformat(),
                }
            },
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "exit_code": 0,
        }


def task_platform_context(task: TaskExecution, skill: Mapping[str, Any]) -> SkillPlatformContext:
    keys = skill.get("platforms", [])
    if not isinstance(keys, list) or any(not isinstance(k, str) or not k for k in keys):
        raise TaskExecutionError("SKILL_PLATFORMS_INVALID", "Invalid Skill platforms", retryable=False)
    settings = process_settings()
    return SkillPlatformContext(
        tenant_id=task.tenant_id,
        actor_user_id=str(task.actor_user_id),
        execution_id=str(task.id),
        execution_type="TASK",
        platform_keys=tuple(dict.fromkeys(keys)),
        console_url=settings.console_platform_url,
        redis_url=settings.redis_url,
        redis_mode=settings.redis_mode,
        redis_cluster_seeds=settings.redis_cluster_seeds,
        service_token=settings.internal_service_token,
    )
