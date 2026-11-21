"""InboundPublisher：批次多成员去重。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from muad_contracts import InboundEvent
from muad_contracts.im import inbound_dedupe_key
from muad_im_core.ingress import InboundPublisher, PublishOutcome

pytestmark = pytest.mark.unit


class _FakePipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, str, str | None]] = []

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._ops.append((key, value, ex))

    async def execute(self) -> list[bool]:
        for key, value, _ex in self._ops:
            self._redis.values[key] = value
        return [True for _ in self._ops]


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.xadds: list[tuple[str, dict[str, str]]] = []

    async def set(
        self, key: str, value: str, *, nx: bool = False, ex: int | None = None, **_: object
    ) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def xadd(self, stream: str, fields: dict[str, str], **_: object) -> str:
        self.xadds.append((stream, fields))
        return "1-0"

    def pipeline(self, transaction: bool = False) -> _FakePipeline:
        return _FakePipeline(self)


def _event(message_id: str = "m1") -> InboundEvent:
    return InboundEvent(
        event_id=uuid.uuid4(),
        channel="WECOM",
        tenant_id="t",
        bot_id="b",
        external_message_id=message_id,
        external_user_id="u",
        text="x",
        received_at=datetime.now(UTC),
    )


async def test_multi_identity_publish_marks_every_member() -> None:
    redis = _FakeRedis()
    publisher = InboundPublisher(redis, shards=1, stream_maxlen=100, lost_after_sec=60)

    outcome = await publisher.publish(_event("t1"), dedupe_ids=("t1", "f1", "f2"))
    assert outcome is PublishOutcome.PUBLISHED
    assert len(redis.xadds) == 1
    for message_id in ("t1", "f1", "f2"):
        assert await publisher.is_published(
            tenant_id="t", bot_id="b", message_id=message_id
        )

    assert (
        await publisher.publish(_event("f1"), dedupe_ids=("t1", "f1", "f2"))
        is PublishOutcome.DUPLICATE
    )
    assert len(redis.xadds) == 1


async def test_single_identity_publish_keeps_existing_shape() -> None:
    redis = _FakeRedis()
    publisher = InboundPublisher(redis, shards=1, stream_maxlen=100, lost_after_sec=60)
    assert await publisher.publish(_event("m1")) is PublishOutcome.PUBLISHED
    assert await publisher.is_published(
        tenant_id="t", bot_id="b", message_id="m1"
    )
    assert await publisher.is_published(
        tenant_id="t", bot_id="b", message_id="other"
    ) is False


async def test_partial_member_marks_are_repaired_on_retry() -> None:
    redis = _FakeRedis()
    publisher = InboundPublisher(redis, shards=1, stream_maxlen=100, lost_after_sec=60)
    await publisher.publish(_event("t1"), dedupe_ids=("t1", "f1"))
    redis.values.pop(inbound_dedupe_key("t", "b", "f1"))
    assert await publisher.publish(_event("t1"), dedupe_ids=("t1", "f1")) is PublishOutcome.DUPLICATE
    assert await publisher.is_published(tenant_id="t", bot_id="b", message_id="f1")
    assert len(redis.xadds) == 1
