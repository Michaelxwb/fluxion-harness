"""产物取件的签名令牌（设计 API-05）：**单产物 + 短 TTL + 可撤销**。

**为什么不是"用户身份令牌"**：终端用户是 `platform_user`（IM 侧），Console 登录主体是
`console_account`（ADMIN/BUILDER）——**两套身份**。给终端用户一个 Console 页面等于给一扇
打不开的门，所以"渠道不能直发文件"时唯一可用的形态是**带签名的短 TTL 直链**。

**为什么是"存一张表"而不是纯 HMAC**：设计要求**可撤销**。纯签名令牌在过期前撤不掉，
只能轮换密钥——那会把所有在途令牌一起作废。

**多 worker（design high-qps-remediation §4.1 / RISK-05）**：授权状态必须与进程无关——
uvicorn `--workers N` 下 A worker 签的链接到 B worker 也要能兑换。默认实现是
`RedisFetchGrantStore`（共享、带 TTL、可撤销）；`InMemoryFetchGrantStore` 只用于没有 Redis 的
单进程开发与单元测试（**多实例部署不得**依赖它，否则跨 worker 会 404）。

**凭据卫生（RULE-secret-001）**：令牌**不进日志、不进审计字段**。它只在 URL 的 `?token=`
里出现，而 URL 会进访问日志——所以 `muad_logging.redaction` 的敏感键名必须覆盖裸 `token`
（2026-10-03 已修，见那里的注释）。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import redis.asyncio as redis_async

logger = logging.getLogger(__name__)

#: 取件链接的存活时长。短是刻意的：它是一条**裸 URL**，会进浏览器历史、代理日志、
#: 转发消息——暴露面随时长线性增长，而用户点开它只需要几秒。
DEFAULT_FETCH_TTL_SEC = 300.0

#: 覆盖 TTL 的环境变量。做成可配置的理由只有一条，与 `delivery_backoff_base_sec` 同：
#: **让验收能用同一条代码路径、同一套断言验证"过期即失效"**，而不必真的等满 5 分钟。
#: 生产不设此变量，走上面的默认值。
TTL_ENV = "ARTIFACT_FETCH_TTL_SEC"

#: 单进程保留的令牌上限（仅内存实现需要；Redis 由 TTL 自然收敛）。
MAX_TOKENS = 4096

#: Redis 键前缀：`muad:artifact:fetch:<token>`。
REDIS_KEY_PREFIX = "muad:artifact:fetch:"


def configured_ttl_sec() -> float:
    """本进程要用的 TTL：环境变量优先，缺省 `DEFAULT_FETCH_TTL_SEC`。

    非法值**显式报错**而不是静默回退默认值：一个拼错的环境变量会安静地被 300 秒盖过去，
    验收里表现为"令牌怎么都不过期"，而排查方向会全错在令牌模型上。
    """
    raw = os.getenv(TTL_ENV)
    if raw is None:
        return DEFAULT_FETCH_TTL_SEC
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{TTL_ENV} 必须是数字，实际是 {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class FetchGrant:
    """一次取件授权：**只对一个产物**、只到某个时刻。"""

    artifact_id: uuid.UUID
    tenant_id: str
    expires_at: datetime


class FetchGrantStore(Protocol):
    async def put(self, token: str, grant: FetchGrant, *, ttl_sec: float) -> None: ...

    async def get(self, token: str) -> FetchGrant | None: ...

    async def delete(self, token: str) -> None: ...


class InMemoryFetchGrantStore:
    """进程内存储：单进程开发/测试用。多实例部署**不得**依赖它（跨 worker 会 404）。"""

    def __init__(self, *, max_tokens: int = MAX_TOKENS) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self._max = max_tokens
        self._grants: dict[str, FetchGrant] = {}

    async def put(self, token: str, grant: FetchGrant, *, ttl_sec: float) -> None:
        self._grants[token] = grant
        while len(self._grants) > self._max:
            self._grants.pop(next(iter(self._grants)))

    async def get(self, token: str) -> FetchGrant | None:
        return self._grants.get(token)

    async def delete(self, token: str) -> None:
        self._grants.pop(token, None)


class RedisFetchGrantStore:
    """共享存储（多 worker 唯一正确的形态）：键带 TTL，撤销就是 DEL。"""

    def __init__(self, client: redis_async.Redis, *, prefix: str = REDIS_KEY_PREFIX) -> None:
        self._client = client
        self._prefix = prefix

    def _key(self, token: str) -> str:
        return f"{self._prefix}{token}"

    async def put(self, token: str, grant: FetchGrant, *, ttl_sec: float) -> None:
        if ttl_sec <= 0:
            # 已经过期：不落键（兑换必然是 None），与内存实现的语义一致。
            return
        payload = json.dumps(
            {
                "artifact_id": str(grant.artifact_id),
                "tenant_id": grant.tenant_id,
                "expires_at": grant.expires_at.timestamp(),
            }
        )
        await self._client.set(self._key(token), payload, ex=max(1, int(ttl_sec + 0.999)))

    async def get(self, token: str) -> FetchGrant | None:
        try:
            raw = await self._client.get(self._key(token))
        except redis_async.RedisError as exc:
            # 失败方向是拒绝：调用方统一按 404 回，绝不因为存储抖动放行。
            logger.warning("artifact_fetch_token_read_failed error=%s", type(exc).__name__)
            return None
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            return FetchGrant(
                artifact_id=uuid.UUID(str(data["artifact_id"])),
                tenant_id=str(data["tenant_id"]),
                expires_at=datetime.fromtimestamp(float(data["expires_at"]), tz=UTC),
            )
        except (ValueError, KeyError, TypeError):
            return None

    async def delete(self, token: str) -> None:
        try:
            await self._client.delete(self._key(token))
        except redis_async.RedisError as exc:
            logger.warning("artifact_fetch_token_delete_failed error=%s", type(exc).__name__)


class ArtifactFetchTokens:
    """签发与兑换取件令牌。存储可注入（默认按部署形态由 deps 选择）。"""

    def __init__(
        self,
        *,
        ttl_sec: float = DEFAULT_FETCH_TTL_SEC,
        max_tokens: int = MAX_TOKENS,
        store: FetchGrantStore | None = None,
    ) -> None:
        self._ttl = timedelta(seconds=ttl_sec)
        self._store = store if store is not None else InMemoryFetchGrantStore(max_tokens=max_tokens)

    @property
    def ttl_sec(self) -> float:
        return self._ttl.total_seconds()

    async def issue(
        self, artifact_id: uuid.UUID, *, tenant_id: str, now: datetime | None = None
    ) -> str:
        """签发一枚令牌。**返回值是凭据**：调用方不得把它写进日志或审计。"""
        moment = now or datetime.now(UTC)
        token = secrets.token_urlsafe(32)
        await self._store.put(
            token,
            FetchGrant(
                artifact_id=artifact_id, tenant_id=tenant_id, expires_at=moment + self._ttl
            ),
            ttl_sec=self.ttl_sec,
        )
        return token

    async def redeem(self, token: str, *, now: datetime | None = None) -> FetchGrant | None:
        """兑换令牌。过期/不存在一律 `None` —— 调用方**统一按 404 回**，不区分原因。"""
        grant = await self._store.get(token)
        if grant is None:
            return None
        moment = now or datetime.now(UTC)
        if grant.expires_at <= moment:
            await self._store.delete(token)
            return None
        return grant

    async def revoke(self, token: str) -> None:
        await self._store.delete(token)


__all__ = [
    "ArtifactFetchTokens",
    "DEFAULT_FETCH_TTL_SEC",
    "FetchGrant",
    "FetchGrantStore",
    "InMemoryFetchGrantStore",
    "REDIS_KEY_PREFIX",
    "RedisFetchGrantStore",
    "TTL_ENV",
    "configured_ttl_sec",
]
