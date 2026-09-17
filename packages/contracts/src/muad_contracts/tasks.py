from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import DeliveryMode


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeliveryRouteInput(ContractModel):
    channel: Literal["WECOM"]
    bot_id: str = Field(min_length=1)
    external_user_id: str = Field(min_length=1)
    external_conversation_id: str | None = None


class CreateTaskRequest(ContractModel):
    tenant_id: str = Field(min_length=1)
    agent_id: UUID
    actor_user_id: UUID
    source_run_id: UUID | None = None
    intent_key: str = Field(min_length=1)
    skill_id: UUID
    skill_artifact_id: UUID
    input: dict[str, Any]
    execution_snapshot: dict[str, Any]
    execution_snapshot_schema_version: int = Field(default=1, ge=1)
    snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=256)
    delivery_route: DeliveryRouteInput | None = None
    delivery_mode: DeliveryMode = DeliveryMode.FINAL_ONLY

    @model_validator(mode="after")
    def _require_route_for_delivery(self) -> Self:
        if self.delivery_mode is DeliveryMode.FINAL_ONLY and self.delivery_route is None:
            raise ValueError("delivery_route is required when delivery_mode=FINAL_ONLY")
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
    intent_key: str = Field(min_length=1)
    skill_id: UUID
    input_template: dict[str, Any] = Field(default_factory=dict)
    schedule: ScheduleSpec
    delivery_route: DeliveryRouteInput
