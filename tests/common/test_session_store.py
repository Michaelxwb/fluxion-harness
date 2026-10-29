"""Redis 会话存储：滑动 TTL、绝对上限、登出删除、fail closed。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from muad_common.session_store import (
    RedisSessionStore,
    SessionAgent,
    SessionRecord,
    SessionStoreUnavailable,
    hash_session_token,
    session_key,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.fail = False

    async def set(self, key: str, value: str, *, ex: int) -> None:
        if self.fail:
            raise OSError("redis down")
        self.values[key] = value
        self.ttls[key] = ex

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise OSError("redis down")
        return self.values.get(key)

    async def expire(self, key: str, ttl: int) -> bool:
        if self.fail:
            raise OSError("redis down")
        if key not in self.values:
            return False
        self.ttls[key] = ttl
        return True

    async def delete(self, key: str) -> None:
        if self.fail:
            raise OSError("redis down")
        self.values.pop(key, None)
        self.ttls.pop(key, None)


def _record(now: datetime, *, idle: int = 60, expires_in: int = 3600) -> SessionRecord:
    return SessionRecord(
        tenant_id="t1",
        platform_user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        auth_method="USER_TOKEN",
        expires_at=now + timedelta(seconds=expires_in),
        idle_ttl_sec=idle,
        agents=(SessionAgent(agent_id=uuid.uuid4(), key="a", name="A"),),
    )


def test_session_key_uses_hash_not_plaintext() -> None:
    token = "secret-token"
    digest = hash_session_token(token)
    key = session_key(digest)
    assert token not in key
    assert digest in key
    assert key.startswith("webchat:session:")


async def test_write_read_touch_delete_roundtrip() -> None:
    now = datetime.now(UTC)
    client = FakeRedis()
    store = RedisSessionStore(client)
    record = _record(now)

    await store.write(hash_session_token("tok"), record, now=now)
    key = session_key(hash_session_token("tok"))
    assert client.ttls[key] == 60

    loaded = await store.read(hash_session_token("tok"))
    assert loaded is not None
    assert loaded.platform_user_id == record.platform_user_id
    assert loaded.agents[0].key == "a"

    assert await store.touch(hash_session_token("tok"), record, now=now) is True
    assert client.ttls[key] == 60

    await store.delete(hash_session_token("tok"))
    assert await store.read(hash_session_token("tok")) is None


async def test_touch_is_capped_by_absolute_expiry() -> None:
    now = datetime.now(UTC)
    client = FakeRedis()
    store = RedisSessionStore(client)
    record = _record(now, idle=3600, expires_in=10)
    await store.write(hash_session_token("tok"), record, now=now)
    assert await store.touch(hash_session_token("tok"), record, now=now) is True
    assert client.ttls[session_key(hash_session_token("tok"))] == 10

    later = now + timedelta(seconds=20)
    assert await store.touch(hash_session_token("tok"), record, now=later) is False
    assert await store.read(hash_session_token("tok")) is None


async def test_corrupt_record_is_deleted_and_returns_none() -> None:
    client = FakeRedis()
    store = RedisSessionStore(client)
    key = session_key(hash_session_token("tok"))
    client.values[key] = "not-json"
    assert await store.read(hash_session_token("tok")) is None
    assert key not in client.values


async def test_redis_failure_is_explicit() -> None:
    client = FakeRedis()
    client.fail = True
    store = RedisSessionStore(client)
    with pytest.raises(SessionStoreUnavailable):
        await store.read(hash_session_token("tok"))
    with pytest.raises(SessionStoreUnavailable):
        await store.delete(hash_session_token("tok"))
