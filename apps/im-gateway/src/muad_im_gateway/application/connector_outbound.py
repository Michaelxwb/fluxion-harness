"""`ConnectorCommandSink` 的真实实现：把回复动作翻译成 `im.outbound` 命令。

归属：入站/出站的渠道形状在 Connector，Gateway 只写**渠道中立**的命令（契约
`DeliveryCommand`）。这里不做去重/审计——那是投递 facade（`/internal/deliveries`）的职责；
本端口服务的是"Gateway 自己产生回复"的路径（状态、增量、收尾、错误文案）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from muad_contracts import DeliveryRouteInput
from muad_contracts.im import DeliveryCommand, DeliveryKind

from ..infrastructure.streams import DeliveryStreams


class ConnectorOutboundCommands:
    def __init__(self, streams: DeliveryStreams, *, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required for outbound commands")
        self._streams = streams
        self._tenant_id = tenant_id

    async def enqueue(
        self,
        *,
        channel: str,
        route: DeliveryRouteInput,
        kind: DeliveryKind,
        text: str = "",
        message_id: str | None = None,
        run_id: str | None = None,
        stream_id: str | None = None,
        status: bool = False,
    ) -> uuid.UUID:
        command = DeliveryCommand(
            command_id=uuid.uuid4(),
            channel=channel,
            tenant_id=self._tenant_id,
            bot_id=route.bot_id,
            run_id=_run_id(run_id),
            message_id=message_id,
            external_conversation_id=route.external_conversation_id,
            external_user_id=route.external_user_id,
            kind=kind,
            text=text,
            payload={"status": True} if status else {},
            stream_id=stream_id,
            created_at=datetime.now(UTC),
        )
        await self._streams.enqueue(command)
        return command.command_id

    async def await_result(
        self, command_id: uuid.UUID, *, timeout_sec: float
    ) -> dict[str, Any] | None:
        return await self._streams.await_result(command_id, timeout_sec=timeout_sec)


def _run_id(raw: str | None) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ValueError(f"run_id must be a UUID, got {raw!r}") from exc


__all__ = ["ConnectorOutboundCommands"]
