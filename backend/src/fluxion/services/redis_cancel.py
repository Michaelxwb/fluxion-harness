"""Redis 取消信号总线（设计 §11.7）：多 Pod cancel 的低延迟通知。

正确性不依赖 Redis（PG CANCELLING 才是事实源；publish 失败只记录，
取消语义不变）；Redis 只降低取消延迟。事件只带非 secret 数据（§25.10）。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Protocol

CANCEL_CHANNEL = "fluxion:execution:cancel_requested"


class RedisClient(Protocol):
    """redis.asyncio.Redis 使用的最小子集（鸭子类型，便于测试替换）。"""

    async def publish(self, channel: str, message: str) -> int: ...

    def pubsub(self) -> object: ...


CancelEventHandler = Callable[[str, str], Awaitable[None]]
"""取消事件处理（tenant_id, execution_id）。"""


class RedisCancelBus:
    """取消信号发布 + 订阅（单实例一 bus；订阅由 serving 循环驱动）。"""

    def __init__(self, client: RedisClient, *, channel: str = CANCEL_CHANNEL) -> None:
        self._client = client
        self._channel = channel

    async def publish_cancel(self, tenant_id: str, execution_id: str) -> None:
        """发布取消信号。失败上抛——调用方决定降级（取消语义不受影响）。"""
        await self._client.publish(
            self._channel,
            json.dumps({"tenant_id": tenant_id, "execution_id": execution_id}),
        )

    async def listen_forever(self, handler: CancelEventHandler) -> None:
        """订阅循环（取消即退出，供 serving 后台任务驱动）。

        坏帧跳过（记录即丢弃，不中断订阅）；订阅中断（连接丢失）即退出，
        由外层重连策略决定是否重建——正确性由 PG 兜底。
        """
        pubsub = self._client.pubsub()
        try:
            await pubsub.subscribe(self._channel)
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    continue
                try:
                    payload = json.loads(message["data"])
                    await handler(str(payload["tenant_id"]), str(payload["execution_id"]))
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
        finally:
            close = getattr(pubsub, "aclose", None)
            if callable(close):
                await close()
            else:
                unsubscribe = getattr(pubsub, "unsubscribe", None)
                if callable(unsubscribe):
                    await unsubscribe(self._channel)
