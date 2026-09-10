from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel


class ChannelEnvelope(BaseModel):
    channel: str
    account_id: str
    peer_type: Literal["user", "group"]
    peer_id: str
    message_id: str
    conversation_ref: str | None = None
    content_type: str = "text"
    content: str
    received_at: datetime


class DeliveryCommand(BaseModel):
    execution_id: str
    route_id: str
    event_type: str
    content_ref: str
    dedupe_key: str


DeliveryResult = Literal["SUCCESS", "RETRYABLE_FAILURE", "NON_RETRYABLE_FAILURE"]


class ChannelAdapter(Protocol):
    """V1.7 D03: single best-effort send, no business-level retry."""

    async def send(self, command: DeliveryCommand) -> DeliveryResult: ...
