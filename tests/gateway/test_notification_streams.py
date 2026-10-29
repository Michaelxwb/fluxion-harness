"""通知 SSE hub：按 tenant/user 路由、坏载荷忽略、慢订阅显式结束。"""

from __future__ import annotations

import asyncio
import json
import uuid

from muad_im_gateway.application.notification_streams import NotificationStreamHub


class _DummyRedis:
    def pubsub(self):  # pragma: no cover - 本测试只验证 dispatch
        raise AssertionError("not used")

    async def get(self, key: str) -> None:
        return None


def _payload(*, tenant_id: str, user_id: uuid.UUID, seq: int) -> str:
    return json.dumps(
        {
            "tenant_id": tenant_id,
            "user_id": str(user_id),
            "notification_id": str(uuid.uuid4()),
            "seq": seq,
        }
    )


async def test_dispatch_routes_only_to_matching_user() -> None:
    hub = NotificationStreamHub(_DummyRedis())  # type: ignore[arg-type]
    user = uuid.uuid4()
    other = uuid.uuid4()
    async with hub.subscribe("t1", user) as queue:
        hub._dispatch(_payload(tenant_id="t1", user_id=other, seq=1))
        assert queue.empty()
        hub._dispatch(_payload(tenant_id="t1", user_id=user, seq=2))
        signal = await asyncio.wait_for(queue.get(), timeout=1)
        assert signal is not None
        assert signal.seq == 2
        assert signal.user_id == user


async def test_malformed_payload_is_ignored() -> None:
    hub = NotificationStreamHub(_DummyRedis())  # type: ignore[arg-type]
    user = uuid.uuid4()
    async with hub.subscribe("t1", user) as queue:
        hub._dispatch("not-json")
        hub._dispatch(json.dumps({"tenant_id": "t1"}))
        assert queue.empty()


async def test_slow_subscriber_gets_close_sentinel() -> None:
    hub = NotificationStreamHub(_DummyRedis(), queue_size=1)  # type: ignore[arg-type]
    user = uuid.uuid4()
    async with hub.subscribe("t1", user) as queue:
        hub._dispatch(_payload(tenant_id="t1", user_id=user, seq=1))
        hub._dispatch(_payload(tenant_id="t1", user_id=user, seq=2))
        assert await asyncio.wait_for(queue.get(), timeout=1) is None
