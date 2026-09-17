from __future__ import annotations

from uuid import UUID

from pydantic import Field

from .tasks import ContractModel, DeliveryRouteInput


class DeliveryMessage(ContractModel):
    type: str = "text"
    text: str = Field(min_length=1)


class DeliveryRequest(ContractModel):
    task_id: UUID
    delivery_key: str = Field(pattern=r"^task:[0-9a-fA-F-]{36}:final$")
    route: DeliveryRouteInput
    message: DeliveryMessage
    artifact_ids: list[UUID] = Field(default_factory=list)
