from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from muad_agent_core.skill import SkillExecutionStatus
from muad_agent_core.tools.outcome import ExecutionOutcome, skill_execution_outcome
from muad_contracts import ToolRetrySafety, WaitKind

from .executor import TaskExecutionError

DEFAULT_WAIT_SEC = 60
WAIT_KEY = "wait"
WAIT_KIND_KEY = "wait_kind"
WAIT_EXTERNAL_REF_KEY = "external_ref"
WAIT_OPERATIONS_KEY = "operations"
WAIT_BATCH_KEY = "batch"
RESULT_ARTIFACT_KEY = "result_artifact_id"
#: Agentic 执行信封（TASK-019）：`{"kind": "TOOL_OPERATION"|"HUMAN_INPUT", "checkpoint": {...}, ...}`。
#: 它是**执行器 → WorkerLoop** 的唯一通道，承载 Kernel 的回合 checkpoint 与 Human 问题；
#: 等待状态仍由 `WorkerLoop._wait` 在停下心跳之后一次性写入（checkpoint + WAITING_* + 事件同事务）。
AGENTIC_KEY = "agentic"
AGENTIC_KIND_KEY = "kind"
AGENTIC_CHECKPOINT_KEY = "checkpoint"
AGENTIC_QUESTION_KEY = "question"
AGENTIC_INPUT_SCHEMA_KEY = "input_schema"
AGENTIC_DEADLINE_KEY = "deadline_at"
#: Skill 结果不符合协议：**确定性**失败（同样的结果重跑多少次都一样），不消耗重试预算。
SKILL_RESULT_INVALID = "SKILL_RESULT_INVALID"
#: 结果待核实：无法确认远端副作用是否发生（响应丢失），不自动重做，保留现场等待核实。
TASK_RESULT_UNVERIFIED = "TASK_RESULT_UNVERIFIED"


class OutcomeKind(StrEnum):
    COMPLETED = "COMPLETED"
    WAITING = "WAITING"
    #: 等人回答：与外部等待语义不同（取消/续接/超时规则都不同，design §3.5.8）。
    WAITING_HUMAN = "WAITING_HUMAN"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class HumanQuestion:
    """一次 Human 挂起要落库的事实（`task_human_interaction` + `WAITING_HUMAN`）。"""

    question: str
    input_schema: dict[str, Any]
    deadline_at: datetime


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    kind: OutcomeKind
    result: dict[str, Any] | None = None
    result_artifact_id: uuid.UUID | None = None
    #: 外部等待的三类载荷（design §3.3.6）：`wait_kind` 是唯一判别器，载荷只带该类需要的键。
    wait_kind: WaitKind | None = None
    wait_payload: dict[str, Any] | None = None
    not_before: datetime | None = None
    error_code: str | None = None
    error_message: str = ""
    #: Agentic 挂起边界上 Kernel 交回的回合 checkpoint（`RunnerCheckpoint.as_state()` 原样）。
    #: WorkerLoop 把它与 WAITING_* 状态写在同一事务里（design §3.5.8）。
    checkpoint_state: dict[str, Any] | None = None
    human: HumanQuestion | None = None
    #: 业务执行结果（TASK-008）：失败分类、证据与重试裁决都来自同一份 `ExecutionOutcome`。
    business_status: str | None = None
    execution_status: str | None = None
    failure_layer: str | None = None
    exit_code: int | None = None
    retryable: bool | None = None
    evidence_preview: str = ""
    output_truncated: bool | None = None


class FailureDisposition(StrEnum):
    """一次失败的收尾裁决（design §3.6「重试与外部副作用」）。"""

    RETRY = "RETRY"
    FAILED = "FAILED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True, slots=True)
class FailureOutcome:
    disposition: FailureDisposition
    error_code: str
    error_message: str


def classify_failure(
    *,
    retry_safety: ToolRetrySafety,
    error_code: str,
    error_message: str,
    retryable: bool,
) -> FailureOutcome:
    """把一次执行失败归成显式收尾裁决。

    - 确定性失败（`retryable=False`：快照缺失/协议违规/计划无效等）保持精确错误码直接终态，
      重试本来就没有意义；
    - 可重试形状的失败按**冻结的** retry_safety 分流：READ_ONLY/IDEMPOTENT 可有界重试
      （幂等复用 Task 行恒定的 `idempotency_key`，由执行器经环境变量交给 Skill）；
    - NON_RETRYABLE（含未声明/未知/非法取值，见 `frozen_retry_safety`）不得假定外部副作用
      未发生：不自动重做，落 `TASK_RESULT_UNVERIFIED` 待核实，保留现场等人工/业务确认。
    """
    if not retryable:
        return FailureOutcome(
            disposition=FailureDisposition.FAILED,
            error_code=error_code,
            error_message=error_message,
        )
    if retry_safety is ToolRetrySafety.NON_RETRYABLE:
        # 处置码表达「结果待核实」，不覆盖原始 stderr：用户/模型要看到真实失败原因。
        return FailureOutcome(
            disposition=FailureDisposition.UNVERIFIED,
            error_code=TASK_RESULT_UNVERIFIED,
            error_message=error_message,
        )
    return FailureOutcome(
        disposition=FailureDisposition.RETRY,
        error_code=error_code,
        error_message=error_message,
    )


def _invalid(detail: str) -> TaskExecutionError:
    return TaskExecutionError(SKILL_RESULT_INVALID, f"skill result violates the protocol: {detail}")


def interpret_execution(execution: Mapping[str, Any], *, now: datetime) -> TaskOutcome:
    """把执行器的原始返回归成显式结局。

    Skill 用 `{"wait": {"wait_kind": ..., "external_ref": ..., "not_before": ...}}` 表达外部等待
    （`wait_kind` 缺省是 `EXTERNAL_POLL`：普通 Skill 只能自己轮询外部句柄；`ASYNC_OPERATION` 与
    `BATCH_FANIN` 由 Kernel / Worker 的批量信封显式声明）；
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
        return TaskOutcome(kind=OutcomeKind.CANCELLED, **_outcome_fields(execution))
    if status != SkillExecutionStatus.SUCCEEDED:
        fields = _outcome_fields(execution)
        return TaskOutcome(
            kind=OutcomeKind.RETRYABLE_FAILURE,
            result=(
                _skill_result_details(fields, dict(report))
                if isinstance((report := execution.get("result")), Mapping)
                and fields.get("execution_status") == "FAILED"
                else None
            ),
            error_code=str(fields.get("error_code") or status or "SKILL_EXECUTION_FAILED"),
            error_message=str(fields.get("error_message") or execution.get("stderr") or ""),
            business_status=fields.get("business_status"),
            execution_status=fields.get("execution_status"),
            failure_layer=fields.get("failure_layer"),
            exit_code=fields.get("exit_code"),
            retryable=fields.get("retryable"),
            evidence_preview=fields.get("evidence_preview", ""),
            output_truncated=fields.get("output_truncated"),
        )
    payload = _result_payload(execution)
    if execution.get("execution_status") == "COMPLETED":
        return _completed_skill_contract(execution, payload)
    agentic = _agentic_envelope(execution.get(AGENTIC_KEY))
    wait = payload.get(WAIT_KEY)
    if wait is not None:
        return replace(_wait_outcome(wait, now=now), **_agentic_fields(agentic))
    if agentic is not None and agentic[AGENTIC_KIND_KEY] == "HUMAN_INPUT":
        return _human_outcome(agentic, payload)
    reference = _parse_reference(payload.pop(RESULT_ARTIFACT_KEY, None))
    return TaskOutcome(
        kind=OutcomeKind.COMPLETED,
        result=payload,
        result_artifact_id=reference,
        **_agentic_fields(agentic),
    )


def _completed_skill_contract(execution: Mapping[str, Any], payload: dict[str, Any]) -> TaskOutcome:
    facts = _outcome_fields(execution)
    if facts.get("business_status") in {None, "UNKNOWN", "NOT_APPLICABLE"}:
        raise _invalid("verified result is missing its business conclusion")
    return TaskOutcome(
        kind=OutcomeKind.COMPLETED,
        result=_skill_result_details(facts, payload),
        execution_status=facts["execution_status"],
        business_status=facts["business_status"],
        exit_code=facts["exit_code"],
    )


def _skill_result_details(facts: Mapping[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "execution_status": facts["execution_status"],
        "business_status": facts["business_status"],
        "exit_code": facts["exit_code"],
        "result": payload,
    }


def _outcome_fields(execution: Mapping[str, Any]) -> dict[str, Any]:
    """共享的业务判定（TASK-008）：Task 路径与 Runtime 工具路径用同一份 `ExecutionOutcome`。"""
    outcome: ExecutionOutcome | None = skill_execution_outcome(execution)
    if outcome is None:
        return {}
    return {
        "error_code": outcome.error_code,
        "error_message": outcome.error_message,
        "business_status": str(outcome.business_status),
        "execution_status": outcome.execution_status.value if outcome.execution_status else None,
        "failure_layer": str(outcome.failure_layer),
        "exit_code": outcome.exit_code,
        "retryable": outcome.retryable,
        "evidence_preview": outcome.evidence.preview,
        "output_truncated": outcome.evidence.output_truncated,
    }


def _agentic_envelope(raw: Any) -> dict[str, Any] | None:
    """Agentic 执行信封：缺省表示这不是 Agentic 执行；形状不对是确定性协议违规。"""
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or not isinstance(raw.get(AGENTIC_KIND_KEY), str):
        raise _invalid("agentic envelope must be an object with a kind string")
    return dict(raw)


def _agentic_fields(envelope: dict[str, Any] | None) -> dict[str, Any]:
    if envelope is None:
        return {}
    checkpoint = envelope.get(AGENTIC_CHECKPOINT_KEY)
    if checkpoint is not None and not isinstance(checkpoint, Mapping):
        raise _invalid("agentic.checkpoint must be an object")
    return {"checkpoint_state": dict(checkpoint) if checkpoint is not None else None}


def _human_outcome(agentic: Mapping[str, Any], payload: dict[str, Any]) -> TaskOutcome:
    """等人回答：问题、输入 schema 与问题期限一起交给 WorkerLoop 落库。"""
    question = agentic.get(AGENTIC_QUESTION_KEY)
    deadline = agentic.get(AGENTIC_DEADLINE_KEY)
    schema = agentic.get(AGENTIC_INPUT_SCHEMA_KEY)
    if not isinstance(question, str) or not question.strip():
        raise _invalid("agentic human question must be a non-empty string")
    if not isinstance(deadline, str) or not deadline:
        raise _invalid("agentic human deadline must be an ISO-8601 string")
    parsed = _wait_not_before({"not_before": deadline}, now=datetime.now(UTC))
    if schema is not None and not isinstance(schema, Mapping):
        raise _invalid("agentic human input_schema must be an object")
    return TaskOutcome(
        kind=OutcomeKind.WAITING_HUMAN,
        result=payload,
        human=HumanQuestion(
            question=question.strip(),
            input_schema=dict(schema) if schema is not None else {},
            deadline_at=parsed,
        ),
        **_agentic_fields(dict(agentic)),
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


def _wait_outcome(wait: Any, *, now: datetime) -> TaskOutcome:
    if not isinstance(wait, Mapping):
        raise _invalid(f"wait must be an object, got {type(wait).__name__}")
    kind = _wait_kind(wait)
    return TaskOutcome(
        kind=OutcomeKind.WAITING,
        wait_kind=kind,
        wait_payload=_wait_payload(wait, kind),
        not_before=_wait_not_before(wait, now=now),
    )


def _wait_kind(wait: Mapping[str, Any]) -> WaitKind:
    """`wait_kind` 缺省是 `EXTERNAL_POLL`；无法识别的类型是协议违规，不猜。"""
    raw = wait.get(WAIT_KIND_KEY)
    if raw is None:
        return WaitKind.EXTERNAL_POLL
    try:
        return WaitKind(raw)
    except ValueError as exc:
        raise _invalid(f"wait.wait_kind is not a known kind: {raw!r}") from exc


def _wait_payload(wait: Mapping[str, Any], kind: WaitKind) -> dict[str, Any]:
    """按 `wait_kind` 取该类的载荷；键缺失按空对象处理，类型不对则显式失败。"""
    if kind is WaitKind.ASYNC_OPERATION:
        operations = wait.get(WAIT_OPERATIONS_KEY)
        if (
            not isinstance(operations, list)
            or not operations
            or not all(isinstance(entry, Mapping) for entry in operations)
        ):
            raise _invalid("wait.operations must be a non-empty list of objects")
        return {WAIT_OPERATIONS_KEY: [dict(entry) for entry in operations]}
    key = WAIT_BATCH_KEY if kind is WaitKind.BATCH_FANIN else WAIT_EXTERNAL_REF_KEY
    value = wait.get(key)
    if value is None:
        return {key: {}}
    if not isinstance(value, Mapping):
        raise _invalid(f"wait.{key} must be an object, got {type(value).__name__}")
    return {key: dict(value)}


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
