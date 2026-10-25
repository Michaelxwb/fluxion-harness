"""跨 Connector 副本的回复目标存储（design §3.3.1 `im:reply:*`）。

为什么必须外置：reply_ref（企微 `req_id`）只在 Connector 的内存里时，owner 崩溃/接管后新
owner 会把它当成"没有会话上下文"→ 主动发送。那是合法降级，但**能在 TTL 内继续会话内回复**
时不该无谓降级。把 message/run/route/stream 四类目标放进 Redis 后，接管者可以继续用原回调；
超过 TTL 才降级，并且降级是**显式**的（调用方据 `ReplyTarget is None` 决定）。

值里没有正文、没有渠道取件凭据；只有不透明的 reply_ref、epoch 与过期时刻。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, cast

from redis.asyncio import Redis

DEFAULT_MESSAGE_TTL_SEC = 300
DEFAULT_RUN_TTL_SEC = 3600
DEFAULT_STREAM_TTL_SEC = 300


@dataclass(frozen=True, slots=True)
class ReplyTarget:
    """一个仍然可用的会话内回复目标。"""

    reply_ref: str
    epoch: int
    expires_at: float


def _now() -> float:
    return time.time()


class ReplyStore:
    def __init__(
        self,
        redis: Redis,
        *,
        message_ttl_sec: int = DEFAULT_MESSAGE_TTL_SEC,
        run_ttl_sec: int = DEFAULT_RUN_TTL_SEC,
        stream_ttl_sec: int = DEFAULT_STREAM_TTL_SEC,
    ) -> None:
        if min(message_ttl_sec, run_ttl_sec, stream_ttl_sec) < 1:
            raise ValueError("reply TTLs must be positive")
        self._redis = redis
        self._message_ttl = message_ttl_sec
        self._run_ttl = run_ttl_sec
        self._stream_ttl = stream_ttl_sec

    # ---- keys -----------------------------------------------------------------
    @staticmethod
    def message_key(tenant_id: str, bot_id: str, message_id: str) -> str:
        return f"im:reply:message:{tenant_id}:{bot_id}:{message_id}"

    @staticmethod
    def run_key(tenant_id: str, run_id: str) -> str:
        return f"im:reply:run:{tenant_id}:{run_id}"

    @staticmethod
    def route_key(
        tenant_id: str,
        bot_id: str,
        external_user_id: str,
        external_conversation_id: str | None,
    ) -> str:
        return (
            "im:reply:route:"
            f"{tenant_id}:{bot_id}:{external_user_id}:{external_conversation_id or ''}"
        )

    @staticmethod
    def stream_key(tenant_id: str, run_id: str) -> str:
        return f"im:reply:stream:{tenant_id}:{run_id}"

    @staticmethod
    def ref_key(tenant_id: str, bot_id: str, reply_ref: str) -> str:
        return f"im:reply:ref:{tenant_id}:{bot_id}:{reply_ref}"

    # ---- writes ---------------------------------------------------------------
    async def remember_message(
        self,
        *,
        tenant_id: str,
        bot_id: str,
        message_id: str,
        reply_ref: str | None,
        epoch: int,
    ) -> None:
        if not reply_ref:
            return
        expires_at = _now() + self._message_ttl
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.hset(
                self.message_key(tenant_id, bot_id, message_id),
                mapping={"reply_ref": reply_ref, "epoch": epoch, "expires_at": expires_at},
            )
            pipe.expire(self.message_key(tenant_id, bot_id, message_id), self._message_ttl)
            pipe.hset(
                self.ref_key(tenant_id, bot_id, reply_ref),
                mapping={"epoch": epoch, "expires_at": expires_at, "message_id": message_id},
            )
            pipe.expire(self.ref_key(tenant_id, bot_id, reply_ref), self._message_ttl)
            await pipe.execute()

    async def remember_route(
        self,
        *,
        tenant_id: str,
        bot_id: str,
        external_user_id: str,
        external_conversation_id: str | None,
        reply_ref: str | None,
        epoch: int,
    ) -> None:
        if not reply_ref:
            return
        key = self.route_key(tenant_id, bot_id, external_user_id, external_conversation_id)
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.hset(
                key,
                mapping={
                    "reply_ref": reply_ref,
                    "epoch": epoch,
                    "expires_at": _now() + self._message_ttl,
                },
            )
            pipe.expire(key, self._message_ttl)
            await pipe.execute()

    async def bind_run(
        self,
        *,
        tenant_id: str,
        run_id: str,
        reply_ref: str | None,
        epoch: int,
        bot_id: str,
        external_user_id: str,
        external_conversation_id: str | None,
    ) -> None:
        if not reply_ref:
            return
        key = self.run_key(tenant_id, run_id)
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.hset(
                key,
                mapping={
                    "reply_target": reply_ref,
                    "epoch": epoch,
                    "bot_id": bot_id,
                    "external_user_id": external_user_id,
                    "external_conversation_id": external_conversation_id or "",
                    "expires_at": _now() + self._run_ttl,
                },
            )
            pipe.expire(key, self._run_ttl)
            await pipe.execute()

    async def remember_stream(
        self,
        *,
        tenant_id: str,
        run_id: str,
        stream_id: str,
        last_text: str,
        epoch: int,
    ) -> None:
        key = self.stream_key(tenant_id, run_id)
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.hset(
                key,
                mapping={
                    "stream_id": stream_id,
                    "last_text": last_text,
                    "epoch": epoch,
                    "expires_at": _now() + self._stream_ttl,
                },
            )
            pipe.expire(key, self._stream_ttl)
            await pipe.execute()

    # ---- reads ----------------------------------------------------------------
    async def resolve_message(
        self, *, tenant_id: str, bot_id: str, message_id: str
    ) -> ReplyTarget | None:
        return await self._resolve_hash(
            self.message_key(tenant_id, bot_id, message_id), field="reply_ref"
        )

    async def resolve_run(self, *, tenant_id: str, run_id: str) -> ReplyTarget | None:
        return await self._resolve_hash(self.run_key(tenant_id, run_id), field="reply_target")

    async def resolve_route(
        self,
        *,
        tenant_id: str,
        bot_id: str,
        external_user_id: str,
        external_conversation_id: str | None,
    ) -> ReplyTarget | None:
        key = self.route_key(tenant_id, bot_id, external_user_id, external_conversation_id)
        return await self._resolve_hash(key, field="reply_ref")

    async def resolve_ref(
        self, *, tenant_id: str, bot_id: str, reply_ref: str
    ) -> ReplyTarget | None:
        return await self._resolve_hash(
            self.ref_key(tenant_id, bot_id, reply_ref), field="reply_ref", fallback=reply_ref
        )

    async def resolve_stream(self, *, tenant_id: str, run_id: str) -> dict[str, str]:
        raw = await cast(
            Awaitable[Any], self._redis.hgetall(self.stream_key(tenant_id, run_id))
        )
        return {_text(key): _text(value) for key, value in raw.items()}

    async def _resolve_hash(
        self, key: str, *, field: str, fallback: str | None = None
    ) -> ReplyTarget | None:
        raw = await cast(Awaitable[Any], self._redis.hgetall(key))
        if not raw:
            return None
        data = {_text(name): _text(value) for name, value in raw.items()}
        reply_ref = data.get(field) or fallback
        if not reply_ref:
            return None
        expires_at = _float(data.get("expires_at"))
        if expires_at and expires_at <= _now():
            return None
        return ReplyTarget(
            reply_ref=reply_ref,
            epoch=_int(data.get("epoch")),
            expires_at=expires_at,
        )


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _int(value: object) -> int:
    try:
        return int(float(_text(value)))
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return float(_text(value))
    except (TypeError, ValueError):
        return 0.0
