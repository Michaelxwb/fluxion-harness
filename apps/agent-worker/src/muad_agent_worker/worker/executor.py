from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from muad_agent_core.skill import (
    ScriptSkillExecutor,
    SkillArtifactResolver,
    SkillExecutionError,
    SkillExecutionRequest,
)
from muad_artifact_store import SkillArtifactCacheError

from ..infrastructure.models.task import TaskExecution

EXECUTION_TIMEOUT_SEC = 300.0
SKILL_ARTIFACT_KEYS = ("artifact_id", "storage_key", "checksum")


class TaskExecutionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TaskExecutorProtocol(Protocol):
    async def execute(self, task: TaskExecution) -> dict[str, Any]: ...


def frozen_skill(task: TaskExecution) -> Mapping[str, Any]:
    """取出快照里冻结的 skill 条目。

    只认 Task 自己的 `execution_snapshot_json`，不查 current 定义——冻结的意义就是
    执行结果不随 Skill/Binding 的后续变更而漂移。
    """
    snapshot = task.execution_snapshot_json or {}
    skills = snapshot.get("skills") or []
    if not skills:
        raise TaskExecutionError("SKILL_SNAPSHOT_MISSING", "execution snapshot has no frozen skill")
    entry = skills[0]
    missing = [key for key in SKILL_ARTIFACT_KEYS if not entry.get(key)]
    if missing:
        raise TaskExecutionError("SKILL_SNAPSHOT_MISSING", f"frozen skill missing {missing}")
    return entry


class SkillTaskExecutor:
    """按冻结 Snapshot 执行 Skill。

    经 SkillArtifactCache 把 artifact 落到本地缓存、校验 checksum 并等 READY 落盘后
    再从本地目录执行；NFS 只作为权威源被读取一次，绝不在其上直接执行 Python。
    """

    def __init__(
        self,
        resolver: SkillArtifactResolver,
        executor: ScriptSkillExecutor | None = None,
    ) -> None:
        self._resolver = resolver
        self._executor = executor or ScriptSkillExecutor()

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
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
                )
            )
        except SkillExecutionError as exc:
            raise TaskExecutionError("SKILL_EXECUTION_FAILED", exc.message) from exc
        return {
            "status": str(result.status),
            "result": dict(result.result) if result.result else None,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
            "exit_code": result.exit_code,
        }
