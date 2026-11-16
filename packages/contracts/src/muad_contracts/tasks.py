from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical import ensure_strict_json
from .enums import (
    ChannelName,
    ScheduleMode,
    ScheduleStatus,
    SkillExecutionMode,
    TaskStatus,
    TaskType,
    TriggerType,
)
from .tool_runtime import RuntimeOperationInput


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeliveryRouteInput(ContractModel):
    channel: ChannelName = Field(min_length=1)
    bot_id: str = Field(min_length=1)
    external_user_id: str = Field(min_length=1)
    external_conversation_id: str | None = None


class CreateTaskRequest(ContractModel):
    tenant_id: str = Field(min_length=1)
    agent_id: UUID
    actor_user_id: UUID
    source_run_id: UUID | None = None
    runtime_operation: RuntimeOperationInput | None = None
    #: 立即任务在 v2 只提交 SKILL；REMINDER/AGENTIC 由 Schedule 触发（design §2.3.2）。
    task_type: TaskType = TaskType.SKILL
    skill_id: UUID | None = None
    skill_artifact_id: UUID | None = None
    skill_execution_mode: SkillExecutionMode | None = None
    input: dict[str, Any]
    execution_snapshot: dict[str, Any]
    execution_snapshot_schema_version: int = Field(default=1, ge=1)
    snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=256)
    #: 创建时的投递目标元数据（design §3.5.9）；投递状态只落在 `task_delivery`。
    delivery_route: DeliveryRouteInput | None = None

    @field_validator("input", "execution_snapshot", mode="before")
    @classmethod
    def _strict_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        # 自由形态载荷只认严格 JSON：非有限数（NaN/Infinity）与未知类型在**入参**就拒，
        # 否则它会一路穿过指纹、直到写 jsonb 才被 PG 拒（500 而不是 422）。
        return ensure_strict_json(value)

    @model_validator(mode="after")
    def _require_skill_columns(self) -> Self:
        if self.runtime_operation is not None and self.runtime_operation.source_run_id != self.source_run_id:
            raise ValueError("runtime operation must reference the task source Run")
        if self.task_type is not TaskType.SKILL:
            raise ValueError("immediate submissions must use task_type=SKILL")
        if self.skill_id is None or self.skill_artifact_id is None:
            raise ValueError("skill_id and skill_artifact_id are required for task_type=SKILL")
        return self


class ScheduleSpec(ContractModel):
    type: Literal["CRON", "ONCE"]
    cron: str | None = None
    run_at: datetime | None = None
    timezone: str

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def _validate_trigger(self) -> Self:
        if self.type == "CRON" and not self.cron:
            raise ValueError("cron is required when type=CRON")
        if self.type == "ONCE" and self.run_at is None:
            raise ValueError("run_at is required when type=ONCE")
        return self


class CreateScheduleRequest(ContractModel):
    name: str = Field(min_length=1, max_length=256)
    agent_id: UUID
    actor_user_id: UUID
    skill_id: UUID
    input_template: dict[str, Any] = Field(default_factory=dict)
    schedule: ScheduleSpec
    delivery_route: DeliveryRouteInput

    @field_validator("input_template", mode="before")
    @classmethod
    def _strict_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_strict_json(value)


class UpdateScheduleRequest(ContractModel):
    """更新 Schedule（API-07）：字段全部可选，但至少提供一项。"""

    name: str | None = Field(default=None, min_length=1, max_length=256)
    input_template: dict[str, Any] | None = None
    schedule: ScheduleSpec | None = None
    delivery_route: DeliveryRouteInput | None = None

    @field_validator("input_template", mode="before")
    @classmethod
    def _strict_json(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else ensure_strict_json(value)

    @model_validator(mode="after")
    def _require_at_least_one(self) -> Self:
        if all(
            value is None
            for value in (self.name, self.input_template, self.schedule, self.delivery_route)
        ):
            raise ValueError("at least one field must be provided")
        return self


class TaskListQuery(ContractModel):
    """任务列表查询参数（API-03 / API-09）。

    start_time/end_time 作用于 create_time；deadline_from/deadline_to 作用于
    deadline_at。两组独立，不混用。
    """

    schedule_id: UUID | None = None
    agent_id: UUID | None = None
    actor_user_id: UUID | None = None
    status: TaskStatus | None = None
    trigger_type: TriggerType | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    deadline_from: datetime | None = None
    deadline_to: datetime | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class ScheduleListQuery(ContractModel):
    """Schedule 列表查询参数（API-06 / API-12）。"""

    actor_user_id: UUID | None = None
    agent_id: UUID | None = None
    status: ScheduleStatus | None = None
    mode: ScheduleMode | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class CancelTaskResponse(ContractModel):
    """取消 Task 的响应（API-05 / API-11）。

    QUEUED/WAITING 直接 CAS 置 CANCELLED；RUNNING 走协作取消，先置
    cancel_requested=true 并返回 RUNNING。取消状态不含 CANCELLING。
    """

    task_id: UUID
    status: Literal["RUNNING", "CANCELLED"]
    cancel_requested: bool = False
