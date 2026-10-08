"""Shared durable async-tool protocol. Credentials never belong in these DTOs."""

from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from .canonical import canonical_json, ensure_strict_json
from .enums import CompletionMode, TaskStatus, TerminalStatus

MAX_TOOL_RESULT_BYTES = 256 * 1024
SNAPSHOT_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"


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
