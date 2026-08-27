from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import uuid4


def _new_id() -> str:
    return uuid4().hex


@dataclass(frozen=True, slots=True)
class ExternalChannelMessage:
    tenant_id: str
    channel_user_id: str
    conversation_id: str
    message_id: str
    content: str
    agent_id: str
    request_id: str = field(default_factory=_new_id)
    trace_id: str = field(default_factory=_new_id)


@dataclass(frozen=True, slots=True)
class ChannelMessage:
    tenant_id: str
    channel_type: str
    channel_user_id: str
    conversation_id: str
    message_id: str
    content: str
    agent_id: str
    request_id: str
    trace_id: str


@dataclass(frozen=True, slots=True)
class ChannelResult:
    kind: Literal["bound", "unbound", "message"]
    output: str
    platform_user_id: str | None = None
    request_id: str = ""
    trace_id: str = ""
    execution_id: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "output": self.output,
            "platform_user_id": self.platform_user_id,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "execution_id": self.execution_id,
        }


class ChannelAdapter(Protocol):
    @property
    def channel_type(self) -> str: ...

    def normalize_inbound(self, message: ExternalChannelMessage) -> ChannelMessage: ...

    async def push_outbound(self, result: ChannelResult) -> None: ...
