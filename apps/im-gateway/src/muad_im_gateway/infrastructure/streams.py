"""出站命令 Stream（API-06）与投递结果等待。

投递 facade 的诚实边界（design §3.4.1 API-04）：

- **先入队、再有界等待结果**：命令进 `im.outbound:{tenant}:{bot}` 之后才等 Connector 写
  `im:outbound:result:{command_id}`；等待超时回**可重试错误**，绝不把"已入队"当"已送达"；
- 结果键里带 `route_key`（Connector 侧适配器产出）：网关写交付审计时不需要认识渠道私有形状；
- Redis 故障显式失败（不能持久化就没有"先持久化再 ACK"这回事）。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import UUID

import redis.asyncio
from muad_contracts.im import DeliveryCommand, command_result_key, outbound_stream_key

RESULT_POLL_SEC = 0.1


class DeliveryStreamsError(Exception):
    pass


class DeliveryStreams:
    def __init__(self, client: redis.asyncio.Redis, *, maxlen: int) -> None:
        if maxlen < 1:
            raise ValueError("stream maxlen must be positive")
        self._client = client
        self._maxlen = maxlen

    async def enqueue(self, command: DeliveryCommand) -> str:
        """XADD 一条出站命令；返回 entry id（可观测/排障用）。"""
        stream = outbound_stream_key(command)
        try:
            entry_id = await self._client.xadd(
                stream,
                {
                    "command_id": str(command.command_id),
                    "channel": command.channel,
                    "tenant_id": command.tenant_id,
                    "bot_id": command.bot_id,
                    "event": command.model_dump_json(),
                },
                maxlen=self._maxlen,
                approximate=True,
            )
        except (redis.RedisError, OSError) as exc:
            raise DeliveryStreamsError(f"enqueue failed: {exc}") from exc
        return str(entry_id)

    async def await_result(self, command_id: UUID, *, timeout_sec: float) -> dict[str, Any] | None:
        """有界等待 Connector 的结果；超时返回 `None`（调用方据此回可重试错误）。"""
        deadline = time.monotonic() + timeout_sec
        key = command_result_key(command_id)
        while True:
            try:
                raw = await self._client.get(key)
            except (redis.RedisError, OSError) as exc:
                raise DeliveryStreamsError(f"result read failed: {exc}") from exc
            if raw is not None:
                return _parse_result(raw)
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(RESULT_POLL_SEC)

    async def aclose(self) -> None:
        await self._client.aclose()


def _parse_result(raw: object) -> dict[str, Any]:
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise DeliveryStreamsError("connector result is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise DeliveryStreamsError("connector result must be an object")
    return payload
