"""RET-01: 入站保留窗口裁剪与触顶检测（不连真实 Redis）。"""

from __future__ import annotations

import logging
import time
from typing import Any

import pytest
from muad_im_gateway.application.inbound_streams import (
    INBOUND_GROUP,
    InboundStreamConsumer,
)
from redis.exceptions import RedisClusterException

KEY = "muad:im.inbound:{inbound}:0"


class _FakeStreamsRedis:
    def __init__(
        self,
        entries: dict[str, list[tuple[str, dict[str, str]]]],
        *,
        last_delivered: dict[str, str],
        pending_min: dict[str, str | None] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.entries = {key: list(value) for key, value in entries.items()}
        self.last_delivered = last_delivered
        self.pending_min = pending_min or {}
        self.error = error
        self.trimmed: list[tuple[str, str]] = []

    async def xinfo_groups(self, key: str) -> list[dict[str, object]]:
        if self.error is not None:
            raise self.error
        return [{"name": INBOUND_GROUP, "last-delivered-id": self.last_delivered[key]}]

    async def xpending(self, key: str, group: str) -> dict[str, object]:
        minimum = self.pending_min.get(key)
        return {"pending": 1 if minimum else 0, "min": minimum}

    async def xtrim(self, key: str, *, minid: str, approximate: bool) -> int:
        self.trimmed.append((key, minid))
        kept = [entry for entry in self.entries[key] if _id(entry[0]) >= _id(minid)]
        removed = len(self.entries[key]) - len(kept)
        self.entries[key] = kept
        return removed

    async def xlen(self, key: str) -> int:
        return len(self.entries[key])

    async def xrange(
        self, key: str, start: str, end: str, count: int | None = None
    ) -> list[tuple[str, dict[str, str]]]:
        return self.entries[key][: count or 1]


def _id(value: str) -> tuple[int, int]:
    head, _, tail = value.partition("-")
    return int(head), int(tail)


def _consumer(redis: Any, *, maxlen: int = 5_000) -> InboundStreamConsumer:
    return InboundStreamConsumer(
        redis,
        registry=object(),  # type: ignore[arg-type]
        pipeline=object(),  # type: ignore[arg-type]
        shards=1,
        max_inflight=1,
        retention_sec=600,
        stream_maxlen=maxlen,
        trim_interval_sec=0.0,
    )


def _entries(*ids: str) -> dict[str, list[tuple[str, dict[str, str]]]]:
    return {KEY: [(entry_id, {"event": "{}"}) for entry_id in ids]}


async def test_trim_respects_pending_boundary() -> None:
    redis = _FakeStreamsRedis(
        _entries("1000-0", "2000-0", "3000-0"),
        last_delivered={KEY: "3000-0"},
        pending_min={KEY: "3000-0"},
    )
    consumer = _consumer(redis)

    await consumer._trim_due()

    assert redis.trimmed == [(KEY, "3000-0")]
    assert [entry[0] for entry in redis.entries[KEY]] == ["3000-0"]


async def test_trim_never_crosses_last_delivered() -> None:
    """未投递积压只能由 MAXLEN 兜底，窗口裁剪不得越过 last-delivered-id。"""
    redis = _FakeStreamsRedis(
        _entries("1000-0", "2000-0", "3000-0"),
        last_delivered={KEY: "2000-0"},
    )
    consumer = _consumer(redis)

    await consumer._trim_due()

    assert redis.trimmed == [(KEY, "2000-1")]
    assert [entry[0] for entry in redis.entries[KEY]] == ["3000-0"]


async def test_trim_skips_when_nothing_delivered() -> None:
    redis = _FakeStreamsRedis(_entries("1000-0"), last_delivered={KEY: "0-0"})
    consumer = _consumer(redis)

    await consumer._trim_due()

    assert redis.trimmed == []


async def test_trim_uses_time_window_for_acked_history() -> None:
    old_ms = int(time.time() * 1000) - 3_600_000
    redis = _FakeStreamsRedis(
        _entries(f"{old_ms}-0", f"{old_ms + 1}-0"),
        last_delivered={KEY: f"{old_ms + 1}-0"},
    )
    consumer = _consumer(redis)

    await consumer._trim_due()

    assert redis.trimmed == [(KEY, f"{old_ms + 1}-1")]


async def test_trim_failure_is_visible_and_non_fatal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = _FakeStreamsRedis(
        _entries("1000-0"),
        last_delivered={KEY: "1000-0"},
        error=RedisClusterException("cluster down"),
    )
    consumer = _consumer(redis)

    with caplog.at_level(logging.WARNING):
        await consumer._trim_due()

    assert "inbound_stream_trim_failed" in caplog.text


async def test_overflow_detected_when_maxlen_trimmed_unread(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = _FakeStreamsRedis(
        _entries("5000-0", "5001-0"),
        last_delivered={KEY: "1000-0"},
    )
    consumer = _consumer(redis, maxlen=2)

    with caplog.at_level(logging.ERROR):
        await consumer._trim_due()

    assert "inbound_stream_backlog_overflow" in caplog.text


async def test_no_overflow_when_maxlen_not_reached(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = _FakeStreamsRedis(
        _entries("5000-0"),
        last_delivered={KEY: "4000-0"},
    )
    consumer = _consumer(redis, maxlen=2)

    with caplog.at_level(logging.ERROR):
        await consumer._trim_due()

    assert "inbound_stream_backlog_overflow" not in caplog.text
