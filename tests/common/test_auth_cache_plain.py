"""认证缓存「精简读写」原语（外部断言面）：键分层、TTL 封顶、权限层、定向删除。

这些原语绕过屏障/fence 协议，安全性来自两点，本文件逐条钉住：

- 凭证键是断言原文的 SHA-256，断言任何改动都会 miss（不存在绕过验签的路径）；
- 权限层被 `invalidate` 删除后必然 miss，故撤权即时生效。

`Redis Cluster` 下 `SCAN` 不能跨节点枚举，所以删除必须按平台定向——这里也覆盖。
"""

from __future__ import annotations

import uuid

import pytest
from muad_common.auth_cache import (
    AuthCacheStore,
    AuthCacheUnavailable,
    credential_key,
    permission_key,
    user_reverse_index_key,
)


class FakeRedis:
    """够用的 Redis 替身：string + set + pipeline（非事务）。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}
        self.fail = False

    def _guard(self) -> None:
        if self.fail:
            raise OSError("redis down")

    async def set(self, key: str, value: str, *, ex: int) -> None:  # noqa: A003
        self._guard()
        self.values[key] = value
        self.ttls[key] = ex

    async def get(self, key: str) -> str | None:
        self._guard()
        return self.values.get(key)

    async def expire(self, key: str, ttl: int) -> bool:
        self._guard()
        if key not in self.values and key not in self.sets:
            return False
        self.ttls[key] = ttl
        return True

    async def delete(self, *keys: str) -> None:
        self._guard()
        for key in keys:
            self.values.pop(key, None)
            self.ttls.pop(key, None)
            self.sets.pop(key, None)

    async def sadd(self, key: str, member: str) -> None:
        self._guard()
        self.sets.setdefault(key, set()).add(member)

    async def smembers(self, key: str) -> set[str]:
        self._guard()
        return set(self.sets.get(key, set()))

    def pipeline(self, *, transaction: bool = False) -> _FakePipeline:
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, client: FakeRedis) -> None:
        self._client = client
        self._ops: list[tuple[str, ...]] = []

    async def __aenter__(self) -> _FakePipeline:
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def smembers(self, key: str) -> None:
        self._ops.append(("smembers", key))

    def sadd(self, key: str, member: str) -> None:
        self._ops.append(("sadd", key, member))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", key, ttl))

    async def execute(self) -> list[object]:
        results: list[object] = []
        for op in self._ops:
            if op[0] == "smembers":
                results.append(await self._client.smembers(op[1]))
            elif op[0] == "sadd":
                await self._client.sadd(op[1], op[2])
                results.append(None)
            elif op[0] == "expire":
                results.append(await self._client.expire(op[1], op[2]))
        self._ops.clear()
        return results


def test_credential_key_puts_platform_after_tenant_tag() -> None:
    key = credential_key("t1", "mssw", "hash-a")
    # 平台段必须在 `{tenant}` 之后：放前面会打散 Redis Cluster 的槽位。
    assert key == "muad:authcache:{t1}:cred:mssw:hash-a"


def test_reverse_index_is_partitioned_by_platform() -> None:
    uid = uuid.uuid4()
    a = user_reverse_index_key("t1", "mssw", uid)
    b = user_reverse_index_key("t1", "other", uid)
    assert a != b
    assert a.endswith(f":useridx:mssw:{uid}")


def test_same_hash_different_platform_never_collides() -> None:
    assert credential_key("t1", "mssw", "h") != credential_key("t1", "other", "h")


@pytest.mark.asyncio
async def test_write_then_read_roundtrip() -> None:
    client = FakeRedis()
    store = AuthCacheStore(client, ttl_sec=60)
    uid = uuid.uuid4()
    await store.write_credential_plain(
        "t1", "mssw", "hash-a", uid, "payload", expires_at_epoch=10_000, now_epoch=9_000
    )
    assert await store.read_credential_plain("t1", "mssw", "hash-a") == "payload"
    # 反向索引按平台登记，供定向删除。
    assert credential_key("t1", "mssw", "hash-a") in client.sets[
        user_reverse_index_key("t1", "mssw", uid)
    ]


@pytest.mark.asyncio
async def test_reverse_index_expires_with_credential_ttl() -> None:
    """索引必须带过期：Set 无法逐成员过期，整体续期避免长期累积无界增长。"""
    client = FakeRedis()
    store = AuthCacheStore(client, ttl_sec=600)
    uid = uuid.uuid4()
    await store.write_credential_plain(
        "t1", "mssw", "hash-a", uid, "payload", expires_at_epoch=10_000, now_epoch=9_000
    )
    index = user_reverse_index_key("t1", "mssw", uid)
    assert client.ttls[index] == 600


@pytest.mark.asyncio
async def test_write_ttl_is_capped_by_absolute_expiry() -> None:
    client = FakeRedis()
    store = AuthCacheStore(client, ttl_sec=3600)
    await store.write_credential_plain(
        "t1", "mssw", "h", uuid.uuid4(), "p", expires_at_epoch=1_030, now_epoch=1_000
    )
    # 剩余 30s 小于闲置窗口 3600s：取更小的那个，绝不越过绝对到期。
    assert client.ttls[credential_key("t1", "mssw", "h")] == 30


@pytest.mark.asyncio
async def test_write_skips_already_expired() -> None:
    client = FakeRedis()
    store = AuthCacheStore(client, ttl_sec=60)
    await store.write_credential_plain(
        "t1", "mssw", "h", uuid.uuid4(), "p", expires_at_epoch=1_000, now_epoch=1_000
    )
    assert await store.read_credential_plain("t1", "mssw", "h") is None


@pytest.mark.asyncio
async def test_touch_never_exceeds_absolute_expiry() -> None:
    client = FakeRedis()
    store = AuthCacheStore(client, ttl_sec=3600)
    uid = uuid.uuid4()
    await store.write_credential_plain(
        "t1", "mssw", "h", uid, "p", expires_at_epoch=1_030, now_epoch=1_000
    )
    ok = await store.touch_credential(
        "t1", "mssw", "h", uid, expires_at_epoch=1_030, now_epoch=1_010
    )
    assert ok is True
    assert client.ttls[credential_key("t1", "mssw", "h")] == 20
    # 索引必须同步续期，否则活跃用户的索引先过期，删除身份时找不到凭证键。
    assert client.ttls[user_reverse_index_key("t1", "mssw", uid)] == 20


@pytest.mark.asyncio
async def test_touch_returns_false_when_expired_or_gone() -> None:
    client = FakeRedis()
    store = AuthCacheStore(client, ttl_sec=3600)
    # 键不存在（并发删除）：touch 返回 False，调用方据此回源。
    assert (
        await store.touch_credential(
            "t1", "mssw", "missing", uuid.uuid4(), expires_at_epoch=2_000, now_epoch=1_000
        )
        is False
    )


@pytest.mark.asyncio
async def test_touch_returns_false_when_already_past_expiry() -> None:
    client = FakeRedis()
    store = AuthCacheStore(client, ttl_sec=3600)
    await store.write_credential_plain(
        "t1", "mssw", "h", uuid.uuid4(), "p", expires_at_epoch=5_000, now_epoch=1_000
    )
    assert (
        await store.touch_credential(
            "t1", "mssw", "h", uuid.uuid4(), expires_at_epoch=5_000, now_epoch=5_000
        )
        is False
    )


@pytest.mark.asyncio
async def test_permission_layer_roundtrip_and_touch() -> None:
    client = FakeRedis()
    store = AuthCacheStore(client, ttl_sec=60)
    uid = uuid.uuid4()
    await store.write_permission_plain(
        "t1", uid, '{"allowed":true}', expires_at_epoch=1_000 + 3_600, now_epoch=1_000
    )
    assert await store.read_permission_plain("t1", uid) == '{"allowed":true}'
    assert (
        await store.touch_permission(
            "t1", uid, expires_at_epoch=1_000 + 3_600, now_epoch=1_000
        )
        is True
    )
    assert client.ttls[permission_key("t1", uid)] == 60


@pytest.mark.asyncio
async def test_touch_permission_is_capped_by_absolute_expiry() -> None:
    """会话到期后权限层不得凭闲置窗口继续放行。"""
    client = FakeRedis()
    store = AuthCacheStore(client, ttl_sec=3_600)
    uid = uuid.uuid4()
    await store.write_permission_plain(
        "t1", uid, '{"allowed":true}', expires_at_epoch=1_000 + 3_600, now_epoch=1_000
    )
    # 只剩 20 秒到绝对到期：续期只能给 20，不能给满 3600。
    assert (
        await store.touch_permission("t1", uid, expires_at_epoch=1_020, now_epoch=1_000)
        is True
    )
    assert client.ttls[permission_key("t1", uid)] == 20
    # 已过绝对到期：不给续期。
    assert (
        await store.touch_permission("t1", uid, expires_at_epoch=900, now_epoch=1_000)
        is False
    )


@pytest.mark.asyncio
async def test_permission_first_write_is_capped_by_absolute_expiry() -> None:
    """首写也按到期封顶：会话只剩 20 秒时权限层不能活满 3600（与凭证层同口径）。"""
    client = FakeRedis()
    store = AuthCacheStore(client, ttl_sec=3_600)
    uid = uuid.uuid4()
    await store.write_permission_plain(
        "t1", uid, '{"allowed":true}', expires_at_epoch=1_020, now_epoch=1_000
    )
    assert client.ttls[permission_key("t1", uid)] == 20


@pytest.mark.asyncio
async def test_permission_first_write_skipped_when_already_expired() -> None:
    """已过绝对到期时不写入：避免留下一个比会话活得更久的权限结论。"""
    client = FakeRedis()
    store = AuthCacheStore(client, ttl_sec=3_600)
    uid = uuid.uuid4()
    await store.write_permission_plain(
        "t1", uid, '{"allowed":true}', expires_at_epoch=900, now_epoch=1_000
    )
    assert await store.read_permission_plain("t1", uid) is None


@pytest.mark.asyncio
async def test_delete_credentials_only_touches_named_platform() -> None:
    client = FakeRedis()
    store = AuthCacheStore(client, ttl_sec=60)
    uid = uuid.uuid4()
    await store.write_credential_plain(
        "t1", "mssw", "h1", uid, "p1", expires_at_epoch=9_999, now_epoch=1_000
    )
    await store.write_credential_plain(
        "t1", "other", "h2", uid, "p2", expires_at_epoch=9_999, now_epoch=1_000
    )
    await store.delete_credentials("t1", uid, platform_keys=["mssw"])
    # 只删指定平台：另一平台的凭证与索引都还在（解绑一个身份不误伤其它平台）。
    assert await store.read_credential_plain("t1", "mssw", "h1") is None
    assert await store.read_credential_plain("t1", "other", "h2") == "p2"


@pytest.mark.asyncio
async def test_delete_credentials_with_no_platforms_is_noop() -> None:
    client = FakeRedis()
    store = AuthCacheStore(client, ttl_sec=60)
    uid = uuid.uuid4()
    await store.write_credential_plain(
        "t1", "mssw", "h1", uid, "p1", expires_at_epoch=9_999, now_epoch=1_000
    )
    await store.delete_credentials("t1", uid, platform_keys=[])
    assert await store.read_credential_plain("t1", "mssw", "h1") == "p1"


@pytest.mark.asyncio
async def test_redis_failure_surfaces_as_unavailable() -> None:
    client = FakeRedis()
    store = AuthCacheStore(client, ttl_sec=60)
    client.fail = True
    # 调用方（Gateway 读侧）据此选择回源，不 fail closed。
    with pytest.raises(AuthCacheUnavailable):
        await store.read_credential_plain("t1", "mssw", "h")
    with pytest.raises(AuthCacheUnavailable):
        await store.read_permission_plain("t1", uuid.uuid4())
