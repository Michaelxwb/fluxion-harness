from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from muad_agent_core.skill import SkillExecutionStatus

from .executor import TaskExecutionError

DEFAULT_WAIT_SEC = 60
WAIT_KEY = "wait"
RESULT_ARTIFACT_KEY = "result_artifact_id"
#: Skill 结果不符合协议：**确定性**失败（同样的结果重跑多少次都一样），不消耗重试预算。
SKILL_RESULT_INVALID = "SKILL_RESULT_INVALID"


class OutcomeKind(StrEnum):
    COMPLETED = "COMPLETED"
    WAITING = "WAITING"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    kind: OutcomeKind
    result: dict[str, Any] | None = None
    result_artifact_id: uuid.UUID | None = None
    external_ref: dict[str, Any] | None = None
    not_before: datetime | None = None
    error_code: str | None = None
    error_message: str = ""


def _invalid(detail: str) -> TaskExecutionError:
    return TaskExecutionError(SKILL_RESULT_INVALID, f"skill result violates the protocol: {detail}")


def interpret_execution(execution: Mapping[str, Any], *, now: datetime) -> TaskOutcome:
    """把执行器的原始返回归成显式结局。

    Skill 用 `{"wait": {"external_ref": ..., "not_before": ...}}` 表达外部异步等待；
    用 `result_artifact_id` 表达大结果外置——沿既有 Artifact 引用契约（只落引用 +
    预览），不把大结果内联进 `result_json`。

    **协议违规一律抛 `TaskExecutionError(SKILL_RESULT_INVALID)`，不做宽容转换**
    （2026-10-06 评审 #14）：此前 `dict(wait.get("external_ref") or {})` 之类会在
    `external_ref` 不是对象时抛 `TypeError`，而这个函数当时在 `run_once` 的 try 之外
    ——异常直接冒泡，既不记账也不失败，任务停在没有心跳的 RUNNING 等回收，然后重跑
    同一个确定性错误。宁可显式失败。
    """
    status = execution.get("status")
    if not isinstance(status, str):
        raise _invalid(f"execution envelope has no status string: {status!r}")
    if status == SkillExecutionStatus.CANCELLED:
        return TaskOutcome(kind=OutcomeKind.CANCELLED)
    if status != SkillExecutionStatus.SUCCEEDED:
        return TaskOutcome(
            kind=OutcomeKind.RETRYABLE_FAILURE,
            error_code=status or "SKILL_EXECUTION_FAILED",
            error_message=str(execution.get("stderr") or ""),
        )
    payload = _result_payload(execution)
    wait = payload.get(WAIT_KEY)
    if wait is not None:
        return TaskOutcome(
            kind=OutcomeKind.WAITING,
            external_ref=_external_ref(wait),
            not_before=_wait_not_before(wait, now=now),
        )
    reference = _parse_reference(payload.pop(RESULT_ARTIFACT_KEY, None))
    return TaskOutcome(
        kind=OutcomeKind.COMPLETED,
        result=payload,
        result_artifact_id=reference,
    )


def _result_payload(execution: Mapping[str, Any]) -> dict[str, Any]:
    """`result` 必须是对象；缺省时回落 stdout 文本（脚本打印 JSON 之外的纯文本）。"""
    raw = execution.get("result")
    stdout = execution.get("stdout")
    if raw is None:
        # `result: {}` 是**一个合法的结构化结果**：只有 None 才允许被 stdout 兜底覆盖，
        # 否则「空对象优先于 stdout」这条保证会失效。
        return {"text": stdout.strip()} if isinstance(stdout, str) and stdout.strip() else {}
    if not isinstance(raw, Mapping):
        raise _invalid(f"result must be an object, got {type(raw).__name__}")
    return dict(raw)


def _external_ref(wait: Any) -> dict[str, Any]:
    if not isinstance(wait, Mapping):
        raise _invalid(f"wait must be an object, got {type(wait).__name__}")
    ref = wait.get("external_ref")
    if ref is None:
        return {}
    if not isinstance(ref, Mapping):
        raise _invalid(f"wait.external_ref must be an object, got {type(ref).__name__}")
    return dict(ref)


def _wait_not_before(wait: Mapping[str, Any], *, now: datetime) -> datetime:
    """缺省/无法解析的时间戳按 `DEFAULT_WAIT_SEC` 后重试；**类型不对则不改写**。"""
    value = wait.get("not_before")
    if value is None:
        return now + timedelta(seconds=DEFAULT_WAIT_SEC)
    if not isinstance(value, str) or not value:
        raise _invalid(f"wait.not_before must be an ISO-8601 string, got {type(value).__name__}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return now + timedelta(seconds=DEFAULT_WAIT_SEC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_reference(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None
