from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class DeliveryRouteInput(BaseModel):
    channel: str
    bot_id: str
    external_user_id: str
    external_conversation_id: str | None = None


class CreateTaskRequest(BaseModel):
    tenant_id: str
    agent_id: UUID
    actor_user_id: UUID
    source_run_id: UUID | None = None
    intent_key: str
    skill_id: UUID
    skill_artifact_id: UUID
    input: dict[str, Any]
    execution_snapshot: dict[str, Any]
    snapshot_hash: str
    idempotency_key: str
    delivery_route: DeliveryRouteInput | None = None
    delivery_mode: Literal["FINAL_ONLY", "NONE"] = "FINAL_ONLY"


class ScheduleSpec(BaseModel):
    type: Literal["CRON", "ONCE"]
    cron: str | None = None
    run_at: str | None = None
    timezone: str
    misfire_policy: Literal["SKIP", "FIRE_ONCE"] = "SKIP"


class CreateScheduleRequest(BaseModel):
    name: str
    agent_id: UUID
    actor_user_id: UUID
    intent_key: str
    skill_id: UUID
    input_template: dict[str, Any] = Field(default_factory=dict)
    schedule: ScheduleSpec
    delivery_route: DeliveryRouteInput
