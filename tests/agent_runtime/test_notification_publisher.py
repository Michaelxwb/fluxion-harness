"""通知实时发布器：提交后 best-effort 发布，失败不上抛。"""

from __future__ import annotations

import json
import uuid

import pytest
from muad_agent_runtime.infrastructure.notification_publisher import (
    NOTIFICATION_CHANNEL,
    NotificationPublisher,
    NullNotificationPublisher,
)
from redis.exceptions import RedisError


class FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.published: list[tuple[str, str]] = []
        self.values: dict[str, str] = {}
        self.closed = False

    async def set(self, key: str, value: str, *, ex: int) -> None:
        if self.fail:
            raise RedisError("redis down")
        self.values[key] = str(value)

    async def publish(self, channel: str, payload: str) -> int:
        if self.fail:
            raise RedisError("redis down")
        self.published.append((channel, payload))
        return 1

    async def aclose(self) -> None:
        self.closed = True


async def test_publish_emits_lightweight_event() -> None:
    client = FakeRedis()
    publisher = NotificationPublisher(client)  # type: ignore[arg-type]
    notification_id = uuid.uuid4()
    user_id = uuid.uuid4()
    notification = {"id": str(notification_id), "seq": 7}
    await publisher.publish(
        tenant_id="t1",
        user_id=user_id,
        notification_id=notification_id,
        seq=7,
        notification=notification,
    )
    channel, payload = client.published[0]
    assert channel == NOTIFICATION_CHANNEL
    body = json.loads(payload)
    assert body["tenant_id"] == "t1"
    assert body["user_id"] == str(user_id)
    assert body["notification_id"] == str(notification_id)
    assert body["seq"] == 7
    assert body["notification"] == notification
    assert client.values[f"webchat:notify:seq:t1:{user_id}"] == "7"


async def test_publish_failure_is_visible_but_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    publisher = NotificationPublisher(FakeRedis(fail=True))  # type: ignore[arg-type]
    await publisher.publish(
        tenant_id="t1",
        user_id=uuid.uuid4(),
        notification_id=uuid.uuid4(),
        seq=1,
        notification={"id": "n"},
    )
    assert "webchat_notification_publish_failed" in caplog.text


async def test_null_publisher_is_noop() -> None:
    publisher = NullNotificationPublisher()
    await publisher.publish(
        tenant_id="t1",
        user_id=uuid.uuid4(),
        notification_id=uuid.uuid4(),
        seq=1,
        notification={"id": "n"},
    )
    await publisher.aclose()
