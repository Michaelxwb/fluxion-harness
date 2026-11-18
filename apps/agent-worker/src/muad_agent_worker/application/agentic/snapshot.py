"""冻结 Snapshot v4 的读视图：恢复执行**只读这一份**，不重新解析当前配置。

三条口径（design §3.5.7 / RULE-snapshot-001）：

- 快照自 Admission 起不可变：本模块只读取 `task.execution_snapshot_json`，不查 Agent/Model/Skill/MCP
  的当前定义；配置或授权变更只影响后续新 Execution。
- 落库哈希必须与快照文本一致：不一致就是"冻结事实被改写"，按确定性失败拒绝，不猜测。
- 本次要执行什么（prompt/output_contract）来自 Fire 冻结的 `input_json`，同样不是当前配置。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from muad_contracts import (
    ScheduleMode,
    ScheduleSnapshotInvalid,
    TaskExecutionSnapshotV4,
    TriggerType,
    load_schedule_snapshot,
    schedule_snapshot_hash,
)

from ...infrastructure.models.task import TaskExecution

#: 执行输入（Fire 冻结）的键；缺一个就是确定性失败，不从快照或当前配置猜。
PROMPT_KEY = "prompt"
MODE_KEY = "mode"
OUTPUT_CONTRACT_KEY = "output_contract"
CONTEXT_KEY = "context"
AGENTIC_MODE = "AGENTIC"


class FrozenExecutionError(Exception):
    """快照/输入不可执行（确定性）：错误码直接落终态，不做重试。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class FrozenAgenticExecution:
    """一次 Agentic Execution 的全部冻结输入。"""

    task: TaskExecution
    snapshot: TaskExecutionSnapshotV4
    goal: str
    output_contract: dict[str, Any]
    fire_context: dict[str, Any]

    @property
    def tenant_id(self) -> str:
        return self.task.tenant_id

    @property
    def execution_id(self) -> UUID:
        return self.task.id

    @property
    def actor_user_id(self) -> UUID:
        return self.task.actor_user_id

    @property
    def agent_id(self) -> UUID:
        return self.task.agent_id

    @property
    def instructions(self) -> str:
        return self.snapshot.agent.instructions

    @property
    def runtime_config(self) -> Mapping[str, Any]:
        return self.snapshot.agent.runtime_config

    @property
    def allowlist(self) -> tuple[str, ...]:
        return tuple(self.snapshot.allowlist)

    @property
    def tools_allow(self) -> tuple[str, ...]:
        """per-job `PLATFORM:`/`SKILL:`/`MCP:` policy; empty means follow the owner."""
        payload = self.task.input_json if isinstance(self.task.input_json, Mapping) else {}
        raw = payload.get("tools_allow")
        if not isinstance(raw, list) or not raw:
            return ()
        return tuple(str(entry) for entry in raw)

    @property
    def scheduled(self) -> bool:
        return self.task.trigger_type == str(TriggerType.SCHEDULED)

    @property
    def memory_block(self) -> str:
        """冻结的记忆注入块；缺失/降级时为空串（不阻断任务，也不在运行期重读）。"""
        memory = self.snapshot.memory
        if memory is None:
            return ""
        text = memory.block.get("rendered_text")
        return text if isinstance(text, str) else ""

    @property
    def deadline_at(self) -> datetime:
        """绝对期限：取冻结预算与 Execution 行中更早的那个。"""
        budget_deadline = self.snapshot.budget.deadline_at
        if budget_deadline is None:
            return self.task.deadline_at
        return min(budget_deadline, self.task.deadline_at)

    def skill(self, skill_id: UUID) -> Any | None:
        for entry in self.snapshot.skills:
            if entry.skill_id == skill_id:
                return entry
        return None


def load_frozen_execution(task: TaskExecution) -> FrozenAgenticExecution:
    """从 Execution 行重建冻结执行：版本/模式/哈希与 Fire 输入一次校验。"""
    raw = task.execution_snapshot_json
    if not isinstance(raw, Mapping):
        raise FrozenExecutionError("SNAPSHOT_MISSING", "execution has no frozen snapshot object")
    try:
        snapshot = load_schedule_snapshot(raw)
    except ScheduleSnapshotInvalid as exc:
        raise FrozenExecutionError("SCHEMA_INCOMPATIBLE", str(exc)) from exc
    if snapshot.mode is not ScheduleMode.AGENTIC:
        raise FrozenExecutionError(
            "SCHEMA_INCOMPATIBLE", f"agentic executor received mode={snapshot.mode.value}"
        )
    frozen_hash = schedule_snapshot_hash(raw)
    if frozen_hash != task.snapshot_hash:
        raise FrozenExecutionError(
            "SNAPSHOT_HASH_MISMATCH", "execution snapshot text does not match the frozen hash"
        )
    return FrozenAgenticExecution(
        task=task,
        snapshot=snapshot,
        goal=_goal(task.input_json),
        output_contract=_mapping_input(task.input_json, OUTPUT_CONTRACT_KEY),
        fire_context=_mapping_input(task.input_json, CONTEXT_KEY),
    )


def _goal(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        raise FrozenExecutionError("AGENTIC_INPUT_INVALID", "execution input must be an object")
    if payload.get(MODE_KEY) != AGENTIC_MODE:
        raise FrozenExecutionError("AGENTIC_INPUT_INVALID", "execution input mode is not AGENTIC")
    prompt = payload.get(PROMPT_KEY)
    if not isinstance(prompt, str) or not prompt.strip():
        raise FrozenExecutionError("AGENTIC_INPUT_INVALID", "execution input has no prompt")
    return prompt.strip()


def _mapping_input(payload: Any, key: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise FrozenExecutionError("AGENTIC_INPUT_INVALID", "execution input must be an object")
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise FrozenExecutionError("AGENTIC_INPUT_INVALID", f"execution input {key} must be an object")
    return dict(value)


__all__ = [
    "FrozenAgenticExecution",
    "FrozenExecutionError",
    "load_frozen_execution",
]
