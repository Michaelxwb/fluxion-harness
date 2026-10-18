"""Shared durable async-tool protocol. Credentials never belong in these DTOs."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from .canonical import canonical_json, ensure_strict_json
from .enums import CompletionMode, RunStatus, TaskStatus, TerminalStatus, WaitReason

MAX_TOOL_RESULT_BYTES = 256 * 1024
SNAPSHOT_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
#: 冻结执行策略（Task execution snapshot 的 `budget`）中承载重试安全性的唯一键。
RETRY_SAFETY_POLICY_KEY = "retry_safety"
#: 不可信提示键：MCP readOnlyHint 等单独不能证明重试安全，解析永不读取。
UNTRUSTED_RETRY_HINT_KEYS = ("readOnlyHint", "read_only_hint", "destructiveHint", "destructive_hint")


class ToolRetrySafety(StrEnum):
    """冻结的可信重试安全性（design §3.6「重试与外部副作用」）。

    - READ_ONLY：只读操作，可有界重试；
    - IDEMPOTENT：幂等操作，可重试但必须复用原 operation/business key；
    - NON_RETRYABLE：未声明/未知/非幂等效果，响应丢失时结果待核实，不得自动重做。
    """

    READ_ONLY = "READ_ONLY"
    IDEMPOTENT = "IDEMPOTENT"
    NON_RETRYABLE = "NON_RETRYABLE"


#: 未知/未声明/非法取值的保守默认：不得自动重做外部副作用。
DEFAULT_TOOL_RETRY_SAFETY = ToolRetrySafety.NON_RETRYABLE


def frozen_retry_safety(policy: Mapping[str, Any]) -> ToolRetrySafety:
    """从**冻结执行策略**解析可信 retry_safety。

    只认 `retry_safety` 一个键：MCP `readOnlyHint` 之类提示字段不参与解析，单独出现一律
    NON_RETRYABLE（提示不能证明重试安全）；未知/非法取值同样回落 NON_RETRYABLE——保守方向
    不抛错，把未知静默升级成「可重试」才是真正危险的行为。
    """
    value = policy.get(RETRY_SAFETY_POLICY_KEY)
    if isinstance(value, ToolRetrySafety):
        return value
    if not isinstance(value, str):
        return DEFAULT_TOOL_RETRY_SAFETY
    try:
        return ToolRetrySafety(value)
    except ValueError:
        return DEFAULT_TOOL_RETRY_SAFETY


def task_retry_safety(snapshot: Mapping[str, Any]) -> ToolRetrySafety:
    """Task execution snapshot → 冻结可信 retry_safety（`budget.retry_safety`）。

    缺失/类型不对/未知取值都按 NON_RETRYABLE：Task 未声明安全性时不得假定外部副作用未发生。
    """
    budget = snapshot.get("budget")
    return frozen_retry_safety(budget if isinstance(budget, Mapping) else {})


class RuntimeOperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    source_run_id: UUID
    source_tool_call_id: str = Field(min_length=1, max_length=256)
    completion_mode: CompletionMode


class AsyncToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    parallel_limit: int = Field(default=4, ge=1, le=16)
    pending_operation_limit: int = Field(default=4, ge=1, le=32)
    event_batch_size: int = Field(default=16, ge=1, le=100)


class ToolResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    event_id: UUID
    operation_id: UUID
    task_id: UUID
    task_event_seq: int = Field(gt=0, strict=True)
    source_run_id: UUID
    actor_user_id: UUID
    task_snapshot_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    terminal_status: TerminalStatus
    completed_at: AwareDatetime
    result: dict[str, JsonValue] | None = None
    error_code: str | None = Field(default=None, min_length=1, max_length=64)
    error_message: str | None = Field(default=None, max_length=2048)

    @field_validator("result", mode="before")
    @classmethod
    def _strict_result(cls, value: object) -> object:
        ensure_strict_json(value)
        if value is not None and len(canonical_json(value).encode("utf-8")) > MAX_TOOL_RESULT_BYTES:
            raise ValueError("tool result exceeds protocol byte limit")
        return value

    @model_validator(mode="after")
    def _terminal_shape(self) -> Self:
        if self.terminal_status is TerminalStatus.COMPLETED:
            if self.result is None or self.error_code is not None or self.error_message is not None:
                raise ValueError("completed result requires result and no error")
        elif self.result is not None or self.error_code is None:
            raise ValueError("failed/cancelled result requires error_code and no result")
        return self


class ToolResultReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    persisted: bool
    duplicate: bool


def tool_waiting_reason(
    status: RunStatus,
    *,
    resume_ready: bool,
    pending_submission_count: int,
    pending_join_count: int,
) -> WaitReason | None:
    """API-05：Run 详情 `waiting_reason` 的唯一口径（Runtime 与 Console 只读投影共用）。

    仅 WAITING_TOOL 有等待原因：结果已到但尚未被接续 claim 时显示 RESUME_READY；仍有未受理
    提交显示 SUBMISSION；其余待定 JOIN 显示 TASK_RESULT。WAITING_INPUT 等状态一律为 null。
    """
    if status != RunStatus.WAITING_TOOL:
        return None
    if resume_ready:
        return WaitReason.RESUME_READY
    if pending_submission_count > 0:
        return WaitReason.SUBMISSION
    if pending_join_count > 0:
        return WaitReason.TASK_RESULT
    return None


class CancelOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_run_id: UUID
    source_tool_call_id: str = Field(min_length=1, max_length=256)
    actor_user_id: UUID


class CancelOperationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    cancel_recorded: bool
    task_id: UUID | None
    task_status: TaskStatus | None
    cancel_requested: bool


class ToolSubmissionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["SUBMITTED", "SUBMISSION_PENDING"]
    operation_id: UUID
    task_id: UUID | None
    task_status: TaskStatus | None
    completion_mode: CompletionMode

    @model_validator(mode="after")
    def _admission_shape(self) -> Self:
        pending = self.status == "SUBMISSION_PENDING"
        if pending != (self.task_id is None) or pending != (self.task_status is None):
            raise ValueError("pending admission has no task identity/status")
        return self
