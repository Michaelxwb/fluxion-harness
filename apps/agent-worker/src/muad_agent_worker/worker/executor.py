from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol, cast

from muad_agent_core.skill import (
    ScriptSkillExecutor,
    SkillArtifactResolver,
    SkillExecutionError,
    SkillExecutionRequest,
    SkillExecutionStatus,
)
from muad_artifact_store import SkillArtifactCacheError

from ..application.batch_fanout import (
    BATCH_PLAN_KEY,
    BATCH_REF_KEY,
    PARKED_NOT_BEFORE,
    TASK_TYPE_BATCH,
    BatchFanoutProtocol,
    BatchPlan,
    BatchPlanError,
    FanoutSummary,
)
from ..infrastructure.models.task import TaskExecution

EXECUTION_TIMEOUT_SEC = 300.0
SKILL_ARTIFACT_KEYS = ("artifact_id", "storage_key", "checksum")
# 确定性失败：重跑结果不会变（快照/计划/制品本身有问题），不消耗重试预算直接 FAILED。
NON_RETRYABLE_CODES = frozenset(
    {
        "SKILL_SNAPSHOT_MISSING",
        "BATCH_PLAN_INVALID",
        "BATCH_FANOUT_UNAVAILABLE",
        "SKILL_ARTIFACT_CHECKSUM_MISMATCH",
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


class SkillTaskExecutor:
    """按冻结 Snapshot 执行 Skill。

    经 SkillArtifactCache 把 artifact 落到本地缓存、校验 checksum 并等 READY 落盘后
    再从本地目录执行；NFS 只作为权威源被读取一次，绝不在其上直接执行 Python。
    """

    def __init__(
        self,
        resolver: SkillArtifactResolver,
        executor: ScriptSkillExecutor | None = None,
        fanout: BatchFanoutProtocol | None = None,
    ) -> None:
        self._resolver = resolver
        self._executor = executor or ScriptSkillExecutor()
        self._fanout = fanout

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        if task.task_type == TASK_TYPE_BATCH:
            # reclaim 后不重跑 Skill：已有 Child 的 Parent 只等待 fan-in。
            return self._batch_envelope(task)
        skill = frozen_skill(task)
        try:
            ready_dir = await self._resolver.ensure(
                artifact_id=str(skill["artifact_id"]),
                storage_key=str(skill["storage_key"]),
                checksum=str(skill["checksum"]),
            )
        except SkillArtifactCacheError as exc:
            raise TaskExecutionError(exc.code, f"skill artifact rejected: {exc.code}") from exc
        try:
            result = await self._executor.execute(
                SkillExecutionRequest(
                    ready_dir=ready_dir,
                    input=task.input_json or {},
                    timeout_sec=EXECUTION_TIMEOUT_SEC,
                    env=task_env(task),
                )
            )
        except SkillExecutionError as exc:
            raise TaskExecutionError("SKILL_EXECUTION_FAILED", exc.message) from exc
        payload = dict(result.result) if result.result else {}
        plan = self._parse_plan(payload)
        if plan is not None:
            if self._fanout is None:
                raise TaskExecutionError(
                    "BATCH_FANOUT_UNAVAILABLE", "batch plan returned but fan-out is not configured"
                )
            summary = await self._fanout.fan_out(task, plan)
            return self._batch_envelope(task, summary)
        return {
            "status": str(result.status),
            "result": dict(result.result) if result.result else None,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
            "exit_code": result.exit_code,
        }

    def _parse_plan(self, payload: Mapping[str, Any]) -> BatchPlan | None:
        try:
            return BatchPlan.parse(payload.get(BATCH_PLAN_KEY))
        except BatchPlanError as exc:
            raise TaskExecutionError(exc.code, str(exc)) from exc

    def _batch_envelope(
        self, task: TaskExecution, summary: FanoutSummary | None = None
    ) -> dict[str, Any]:
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
