"""Redis Stream 键派生：与 contracts 的 route/shard 同一口径。"""

from __future__ import annotations

from muad_contracts.im import (
    DeliveryCommand,
    InboundEvent,
    inbound_stream_key,
    outbound_stream_key,
)


def stream_keys(
    *, event: InboundEvent | None = None, command: DeliveryCommand | None = None, shards: int = 32
) -> tuple[str, ...]:
    keys: list[str] = []
    if event is not None:
        keys.append(inbound_stream_key(event, shards=shards))
    if command is not None:
        keys.append(outbound_stream_key(command, shards=shards))
    if not keys:
        raise ValueError("event or command is required")
    return tuple(keys)
