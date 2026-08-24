from __future__ import annotations

from fluxion.protocols.channel import ChannelMessage, ChannelResult, ExternalChannelMessage


class _BaseChannelAdapter:
    channel_type = "unknown"

    def __init__(self) -> None:
        self._outbound: list[ChannelResult] = []

    @property
    def outbound(self) -> tuple[ChannelResult, ...]:
        return tuple(self._outbound)

    def normalize_inbound(self, message: ExternalChannelMessage) -> ChannelMessage:
        return ChannelMessage(
            tenant_id=message.tenant_id,
            channel_type=self.channel_type,
            channel_user_id=message.channel_user_id,
            conversation_id=message.conversation_id,
            message_id=message.message_id,
            content=message.content,
            runtime_profile_id=message.runtime_profile_id,
            request_id=message.request_id,
            trace_id=message.trace_id,
        )

    async def push_outbound(self, result: ChannelResult) -> None:
        self._outbound.append(result)


class WebChannelAdapter(_BaseChannelAdapter):
    channel_type = "web"


class StubImChannelAdapter(_BaseChannelAdapter):
    channel_type = "stub-im"
