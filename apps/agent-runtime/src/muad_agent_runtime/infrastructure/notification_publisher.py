"""Webchat 通知的实时发布（Redis Pub/Sub，SSE 通知流的源）。

通知事实先落 PG（`ChannelNotification`，与终态同事务）；**提交后**再 best-effort 发布一个
轻量事件（tenant/user/notification_id/seq）。发布失败只告警：客户端重连时按持久 seq 回填，
实时通道故障不能回滚业务事实，也不能伪造通知。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Final, cast

import redis.asyncio
from muad_common import process_settings
from muad_common.notification_keys import (
    NOTIFICATION_WATERMARK_TTL_SEC,
    notification_watermark_key,
)
from muad_common.redis_client import (
    create_pubsub_client_sync,
    create_redis_client,
)
from redis.asyncio.cluster import RedisCluster
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

#: 全局通知频道：gateway 每个 Pod 订阅一次，按 tenant/user 路由到本 Pod 的 SSE 流。
NOTIFICATION_CHANNEL: Final = "muad:webchat.notifications"


class NullNotificationPublisher:
    """未配置 Redis / 测试装配：显式空实现（客户端仍有持久回填）。"""

    async def publish(
        self,
        *,
        tenant_id: str,
        user_id: uuid.UUID,
        notification_id: uuid.UUID,
        seq: int,
        notification: dict[str, object],
    ) -> None:
        return None

    async def aclose(self) -> None:
        return None


class NotificationPublisher:
    """Redis Pub/Sub 发布器；`publish` 永不上抛（事实已在 PG 提交）。"""

    def __init__(
        self,
        client: redis.asyncio.Redis | RedisCluster,
        *,
        pubsub_client: redis.asyncio.Redis | None = None,
        channel: str = NOTIFICATION_CHANNEL,
    ) -> None:
        #: 水位写走 keyed 客户端；PUBLISH 走普通 pub/sub 连接（cluster 客户端没有 pub/sub）。
        self._keyed: redis.asyncio.Redis | RedisCluster = client
        # standalone 下 keyed 客户端本身就是普通 Redis，可以兼任 pub/sub。
        self._pubsub: redis.asyncio.Redis = pubsub_client or cast(
            redis.asyncio.Redis, client
        )
        self._channel = channel

    async def publish(
        self,
        *,
        tenant_id: str,
        user_id: uuid.UUID,
        notification_id: uuid.UUID,
        seq: int,
        notification: dict[str, object],
    ) -> None:
        try:
            await self._keyed.set(
                notification_watermark_key(tenant_id, user_id),
                int(seq),
                ex=NOTIFICATION_WATERMARK_TTL_SEC,
            )
        except (RedisError, OSError) as exc:
            logger.warning(
                "webchat_notification_watermark_failed",
                extra={"error_type": type(exc).__name__},
            )
        payload = json.dumps(
            {
                "tenant_id": tenant_id,
                "user_id": str(user_id),
                "notification_id": str(notification_id),
                "seq": int(seq),
                "notification": notification,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            await self._pubsub.publish(self._channel, payload)
        except (RedisError, OSError) as exc:
            logger.warning(
                "webchat_notification_publish_failed",
                extra={"error_type": type(exc).__name__},
            )

    async def aclose(self) -> None:
        if self._pubsub is not self._keyed:
            await self._pubsub.aclose()
        await self._keyed.aclose()


_publisher: NotificationPublisher | NullNotificationPublisher | None = None


def get_notification_publisher() -> NotificationPublisher | NullNotificationPublisher:
    """进程级发布器：Redis 未配置时显式空实现，不静默伪造发布。"""
    global _publisher
    if _publisher is None:
        settings = process_settings()
        if not settings.redis_url:
            _publisher = NullNotificationPublisher()
        else:
            seeds = [
                item.strip()
                for item in (settings.redis_cluster_seeds or "").split(",")
                if item.strip()
            ]
            mode = settings.redis_mode
            _publisher = NotificationPublisher(
                create_redis_client(
                    settings.redis_url, mode=mode, seeds=seeds, decode_responses=True
                ),
                pubsub_client=create_pubsub_client_sync(
                    settings.redis_url, mode=mode, seeds=seeds, decode_responses=True
                ),
            )
    return _publisher


async def close_notification_publisher() -> None:
    global _publisher
    if _publisher is not None:
        publisher, _publisher = _publisher, None
        await publisher.aclose()


def reset_notification_publisher() -> None:
    """测试专用：丢弃进程级实例（不等待 Redis 关闭）。"""
    global _publisher
    _publisher = None
