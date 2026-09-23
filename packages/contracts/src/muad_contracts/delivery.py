from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .tasks import ContractModel, DeliveryRouteInput


class DeliveryMessage(ContractModel):
    type: Literal["text"] = "text"
    text: str = Field(min_length=1)


class DeliveryRequest(ContractModel):
    task_id: UUID
    delivery_key: str = Field(pattern=r"^task:[0-9a-fA-F-]{36}:final$")
    route: DeliveryRouteInput
    message: DeliveryMessage
    artifact_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _delivery_key_matches_task(self) -> Self:
        if self.delivery_key != f"task:{self.task_id}:final":
            raise ValueError("delivery_key 必须为 task:{task_id}:final 且与 task_id 一致")
        return self


class DeliveryResponse(ContractModel):
    accepted: bool
    delivered: bool
    # 命中成功键的重放为 True（200 且不重发）
    deduplicated: bool
