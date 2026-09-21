from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from muad_agent_core.skill import SkillExecutionStatus

DEFAULT_WAIT_SEC = 60
WAIT_KEY = "wait"
RESULT_ARTIFACT_KEY = "result_artifact_id"


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


def interpret_execution(execution: Mapping[str, Any], *, now: datetime) -> TaskOutcome:
    """把执行器的原始返回归成显式结局。

    Skill 用 `{"wait": {"external_ref": ..., "not_before": ...}}` 表达外部异步等待；
    用 `result_artifact_id` 表达大结果外置——沿既有 Artifact 引用契约（只落引用 +
    预览），不把大结果内联进 `result_json`。
    """
    status = str(execution.get("status", ""))
    if status == SkillExecutionStatus.CANCELLED:
        return TaskOutcome(kind=OutcomeKind.CANCELLED)
    if status != SkillExecutionStatus.SUCCEEDED:
        return TaskOutcome(
            kind=OutcomeKind.RETRYABLE_FAILURE,
            error_code=status or "SKILL_EXECUTION_FAILED",
            error_message=str(execution.get("stderr") or ""),
        )
    payload = dict(execution.get("result") or {})
    wait = payload.get(WAIT_KEY)
    if isinstance(wait, Mapping):
        return TaskOutcome(
            kind=OutcomeKind.WAITING,
            external_ref=dict(wait.get("external_ref") or {}),
            not_before=_parse_moment(wait.get("not_before"), now=now),
        )
    reference = _parse_reference(payload.pop(RESULT_ARTIFACT_KEY, None))
    return TaskOutcome(
        kind=OutcomeKind.COMPLETED,
        result=payload,
        result_artifact_id=reference,
    )


def _parse_moment(value: Any, *, now: datetime) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return now + timedelta(seconds=DEFAULT_WAIT_SEC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return now + timedelta(seconds=DEFAULT_WAIT_SEC)


def _parse_reference(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None
