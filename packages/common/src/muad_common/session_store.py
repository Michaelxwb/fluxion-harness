"""Webchat 会话的 Redis 存储：首次鉴权写入，之后每请求读取/滑动，登出删除。

- key 只用会话原文的 SHA-256（`webchat:session:<hash>`），不存原文；
- value 是受信 principal + 当前授权 Agent 集合 + 绝对到期时刻 + idle TTL；
- **滑动续期**：命中后 `EXPIRE` 到 `min(idle_ttl, absolute_expires_at - now)`，不超过绝对上限；
- **fail closed**：Redis 不可用抛 `SessionStoreUnavailable`，调用方返回 503，不回源 Console；
- key 缺失（登出/过期/Redis 重启）返回 None，调用方要求重新认证。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

logger = logging.getLogger(__name__)

SESSION_KEY_PREFIX: Final = "webchat:session:"


def hash_session_token(token: str) -> str:
    """会话原文的 SHA-256 小写十六进制（与 Console 的 `hash_chat_token` 同口径）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_key(token_hash: str) -> str:
    return f"{SESSION_KEY_PREFIX}{token_hash}"


class SessionStoreUnavailable(RuntimeError):
    """Redis 不可用：调用方必须 fail closed（503），不得降级到 Console/PG。"""


@dataclass(frozen=True, slots=True)
class SessionAgent:
    agent_id: uuid.UUID
    key: str
    name: str


@dataclass(frozen=True, slots=True)
class SessionRecord:
    tenant_id: str
    platform_user_id: uuid.UUID
    session_id: uuid.UUID
    auth_method: str
    expires_at: datetime
    idle_ttl_sec: int
    agents: tuple[SessionAgent, ...]

    def to_json(self) -> str:
        return json.dumps(
            {
                "tenant_id": self.tenant_id,
                "platform_user_id": str(self.platform_user_id),
                "session_id": str(self.session_id),
                "auth_method": self.auth_method,
                "expires_at": self.expires_at.isoformat(),
                "idle_ttl_sec": self.idle_ttl_sec,
                "agents": [
                    {"agent_id": str(agent.agent_id), "key": agent.key, "name": agent.name}
                    for agent in self.agents
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> SessionRecord:
        data: dict[str, Any] = json.loads(raw)
        return cls(
            tenant_id=str(data["tenant_id"]),
            platform_user_id=uuid.UUID(str(data["platform_user_id"])),
            session_id=uuid.UUID(str(data["session_id"])),
            auth_method=str(data["auth_method"]),
            expires_at=datetime.fromisoformat(str(data["expires_at"])),
            idle_ttl_sec=int(data["idle_ttl_sec"]),
            agents=tuple(
                SessionAgent(
                    agent_id=uuid.UUID(str(item["agent_id"])),
                    key=str(item["key"]),
                    name=str(item["name"]),
                )
                for item in data.get("agents") or []
            ),
        )


class RedisSessionStore:
    """极薄的 Redis 适配；所有 Redis 异常统一转成 `SessionStoreUnavailable`。"""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def write(self, token_hash: str, record: SessionRecord, *, now: datetime) -> None:
        ttl = _ttl_seconds(record, now)
        if ttl <= 0:
            return
        try:
            await self._client.set(session_key(token_hash), record.to_json(), ex=ttl)
        except Exception as exc:  # noqa: BLE001 - Redis 客户端边界，统一 fail closed
            raise SessionStoreUnavailable(type(exc).__name__) from exc

    async def read(self, token_hash: str) -> SessionRecord | None:
        try:
            raw = await self._client.get(session_key(token_hash))
        except Exception as exc:  # noqa: BLE001
            raise SessionStoreUnavailable(type(exc).__name__) from exc
        if raw is None:
            return None
        try:
            record = SessionRecord.from_json(str(raw))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("webchat_session_record_corrupt")
            await self.delete(token_hash)
            return None
        if record.expires_at <= _now_utc():
            await self.delete(token_hash)
            return None
        return record

    async def touch(self, token_hash: str, record: SessionRecord, *, now: datetime) -> bool:
        if record.expires_at <= now:
            await self.delete(token_hash)
            return False
        ttl = _ttl_seconds(record, now)
        if ttl <= 0:
            await self.delete(token_hash)
            return False
        try:
            result = await self._client.expire(session_key(token_hash), ttl)
        except Exception as exc:  # noqa: BLE001
            raise SessionStoreUnavailable(type(exc).__name__) from exc
        if not result:
            return False
        return True

    async def delete(self, token_hash: str) -> None:
        try:
            await self._client.delete(session_key(token_hash))
        except Exception as exc:  # noqa: BLE001
            raise SessionStoreUnavailable(type(exc).__name__) from exc


def _ttl_seconds(record: SessionRecord, now: datetime) -> int:
    remaining = int((record.expires_at - now).total_seconds())
    return max(0, min(record.idle_ttl_sec, remaining))


def _now_utc() -> datetime:
    return datetime.now(UTC)
