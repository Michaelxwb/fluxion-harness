"""认证缓存与同步失效的 Redis 读写原语（Console 写、Gateway 读，WebChat / MSSWWEB 共用）。

**为什么要独立于 `session_store.py`**：撤权要秒级生效，但把授权冻结进登录缓存又会把"撤
Agent 授权"变成"强制退出登录"。所以这里把事实拆成三层，各自独立失效：

- 凭证层（`cred:`）：这张 token/会话本身是否有效、属于谁、何时到期；不含任何 Agent/grant。
- 权限层（`perm:`）：该用户当前可用的 Agent 与入口授权；任何授权/身份/入口/用户变化删除它。
- 状态层（`barrier:` / `userver:` / `platform:`）：屏障、用户权限版本、平台激活 epoch。

**屏障值语义**（一个 key 同时承载"是否放行"与"当前 fence"）：

- BLOCKED：值是裸 fence（如 ``"3f2a..."``）——不放行，且所有起始 fence ≠ 它的回填都丢弃。
- READY：值是 ``"READY:<fence>"``——放行；回填必须比较自己的起始 fence 等于这个 ``<fence>``。

把 fence 缝进屏障值，使"是否放行"与"起始 fence 是否过期"在同一次 GET 里得到，回填脚本因此
能用一次读取完成 CAS，不需要额外 key。

**校验为什么不能只比记录里的版本**：屏障、版本、平台 epoch 必须**独立读取**，再与缓存记录里的
副本比较；拿记录和它自己比恒等，等于没校验。读取脚本一次读完四项再判定，避免中间被撤权插入。

**回填为什么用 CAS**：并发下会有人「读到旧权限 → 撤权 DEL → 旧结果又写回去」。回填脚本同时比较
**起始 fence、READY、用户版本、平台 epoch**，任何一项变了就丢弃这次回填，让调用方重查；这样旧
回源结果永远无法覆盖撤权后的状态。

**fail closed**：Redis 异常一律抛 `AuthCacheUnavailable`，调用方按不可重试失败/503 处理，绝不
降级放行旧授权。TTL 只用于回收数据，不提供任何撤权保证。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

#: 认证缓存的独立命名空间：隔离服务不清空整个 Redis，定向失效也绝不 SCAN/KEYS。
AUTH_CACHE_ROOT: Final = "muad:authcache"
#: READY 值前缀；其后是当前 fence。BLOCKED 时整值是裸 fence（不带前缀）。
READY_PREFIX: Final = "READY:"


# ---------------------------------------------------------------------------
# key 工厂：同租户 hash tag 保证屏障/版本/权限/凭证落在同一 slot，
# 多 key 的 EVAL / 批量 DEL 在 Redis Cluster 下才不会被跨槽拒绝。
# ---------------------------------------------------------------------------


def _tag(tenant_id: str) -> str:
    return f"{{{tenant_id}}}"


def barrier_key(tenant_id: str) -> str:
    return f"{AUTH_CACHE_ROOT}:{_tag(tenant_id)}:barrier"


def user_version_key(tenant_id: str, user_id: uuid.UUID) -> str:
    return f"{AUTH_CACHE_ROOT}:{_tag(tenant_id)}:userver:{user_id}"


def platform_state_key(tenant_id: str, platform_key: str) -> str:
    """平台激活状态：值是该平台当前 ``policy_epoch``（非密、不重复的激活版本）。"""
    return f"{AUTH_CACHE_ROOT}:{_tag(tenant_id)}:platform:{platform_key}"


def permission_key(tenant_id: str, user_id: uuid.UUID) -> str:
    return f"{AUTH_CACHE_ROOT}:{_tag(tenant_id)}:perm:{user_id}"


def credential_key(
    tenant_id: str, platform_key: str, token_hash: str
) -> str:
    """凭证键：``{tenant}`` hash tag 之后先平台段再 token 哈希。

    平台段必须在 hash tag **之后**：放前面会打散同租户槽位，破坏多 key 原子操作与批量删除。
    不同平台即使 token 哈希偶然相同也不会互相命中。
    """
    return f"{AUTH_CACHE_ROOT}:{_tag(tenant_id)}:cred:{platform_key}:{token_hash}"


def user_reverse_index_key(
    tenant_id: str, platform_key: str, user_id: uuid.UUID
) -> str:
    """user → 该用户名下**同一平台**凭证 key 的反向索引（有界集合，用于定向批量删除）。

    按平台拆分，避免解绑某平台身份时连带清掉同一用户其它平台的凭证。
    """
    return f"{AUTH_CACHE_ROOT}:{_tag(tenant_id)}:useridx:{platform_key}:{user_id}"


def new_fence() -> str:
    """一次失效随机生成的新 fence；写入屏障后所有更早的回填都会被 CAS 丢弃。"""
    return uuid.uuid4().hex


def parse_barrier(raw: bytes | str | None) -> tuple[bool, str | None]:
    """把屏障值解析成 ``(is_ready, fence)``。

    - 缺失：视为 READY 且无 fence（首次部署、从未发生过失效）——``(True, None)``。
    - 以 ``READY:`` 开头：放行，fence 取其后缀。
    - 其它：BLOCKED，fence 就是整个值。
    """
    if raw is None:
        return True, None
    text = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
    if text.startswith(READY_PREFIX):
        return True, text[len(READY_PREFIX) :] or None
    return False, text or None


class AuthCacheUnavailable(RuntimeError):
    """Redis 不可用或数据损坏：调用方必须 fail closed，不得降级放行旧授权。"""


# ---------------------------------------------------------------------------
# 读取：一条脚本读完屏障 + 缓存 + 独立版本 + 平台 epoch，全部相符才放行。
# ---------------------------------------------------------------------------
# 返回 {status, value}：
#   0 = 屏障阻塞（BLOCKED），value 为当前 fence
#   1 = 放行，value 为缓存记录
#   2 = 未命中（缓存缺失或版本/epoch 不符），value 为 ''
# ARGV: [1]=expected_user_version, [2]=expected_epoch, [3]=check_epoch("1"/"0")
# 四个 key 都是同租户 hash tag，Cluster 下同槽；无 epoch 时 KEYS[4] 仍传但被忽略。
_READ_CACHED = """
local barrier = redis.call('GET', KEYS[1])
if barrier then
  if string.sub(barrier, 1, 6) ~= 'READY:' then
    return {0, barrier}
  end
end
local payload = redis.call('GET', KEYS[2])
if not payload then
  return {2, ''}
end
local ver = redis.call('GET', KEYS[3])
if not ver or ver ~= ARGV[1] then
  return {2, ''}
end
if ARGV[3] == '1' then
  local epoch = redis.call('GET', KEYS[4])
  if not epoch or epoch ~= ARGV[2] then
    return {2, ''}
  end
end
return {1, payload}
"""
# ---------------------------------------------------------------------------
# 回填 CAS：起始 fence 必须等于当前 READY fence、版本/epoch 仍一致，才允许写入。
# ---------------------------------------------------------------------------
# KEYS: barrier, cache, userver, platform
# ARGV: start_fence, expected_user_version, expected_epoch, value, ttl_seconds, check_epoch
# 返回 1=写入成功；0=fence/版本/epoch 变化或阻塞，丢弃本次回填。
_WRITE_CACHED_CAS = """
local barrier = redis.call('GET', KEYS[1])
if not barrier then
  -- 从未发生过失效：起始 fence 必须是空（None）才允许回填
  if ARGV[1] ~= '' then
    return 0
  end
else
  if string.sub(barrier, 1, 6) ~= 'READY:' then
    return 0
  end
  local current_fence = string.sub(barrier, 7)
  if current_fence ~= ARGV[1] then
    return 0
  end
end
local ver = redis.call('GET', KEYS[3])
if not ver or ver ~= ARGV[2] then
  return 0
end
if ARGV[6] == '1' then
  local epoch = redis.call('GET', KEYS[4])
  if not epoch or epoch ~= ARGV[3] then
    return 0
  end
end
redis.call('SET', KEYS[2], ARGV[4], 'EX', ARGV[5])
return 1
"""


@dataclass(frozen=True, slots=True)
class CacheReadResult:
    """读取三态：``blocked`` / ``hit`` / ``miss``；统一出口便于调用方穷举。"""

    status: str  # "blocked" | "hit" | "miss"
    value: str | None = None
    fence: str | None = None

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"

    @property
    def hit(self) -> bool:
        return self.status == "hit"


_STATUS = {0: "blocked", 1: "hit", 2: "miss"}


class AuthCacheStore:
    """极薄的 Redis 适配；所有 Redis 异常统一转成 `AuthCacheUnavailable`（fail closed）。"""

    def __init__(self, client: Any, *, ttl_sec: int) -> None:
        self._client = client
        self._ttl = ttl_sec

    # -- 状态层 -------------------------------------------------------------

    async def get_barrier(self, tenant_id: str) -> tuple[bool, str | None]:
        try:
            raw = await self._client.get(barrier_key(tenant_id))
        except Exception as exc:  # noqa: BLE001 - Redis 客户端边界
            raise AuthCacheUnavailable(type(exc).__name__) from exc
        return parse_barrier(raw)

    async def set_blocked(self, tenant_id: str, fence: str) -> None:
        """关闭屏障为 BLOCKED 并写入新 fence；不带 TTL——屏障绝不靠过期自动打开。"""
        try:
            await self._client.set(barrier_key(tenant_id), fence)
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc

    async def set_ready(self, tenant_id: str, fence: str) -> None:
        try:
            await self._client.set(barrier_key(tenant_id), f"{READY_PREFIX}{fence}")
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc

    async def get_user_version(self, tenant_id: str, user_id: uuid.UUID) -> int | None:
        try:
            raw = await self._client.get(user_version_key(tenant_id, user_id))
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    async def set_user_version(self, tenant_id: str, user_id: uuid.UUID, version: int) -> None:
        try:
            await self._client.set(user_version_key(tenant_id, user_id), str(version))
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc

    async def get_platform_epoch(self, tenant_id: str, platform_key: str) -> str | None:
        try:
            raw = await self._client.get(platform_state_key(tenant_id, platform_key))
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)

    async def set_platform_epoch(self, tenant_id: str, platform_key: str, epoch: str) -> None:
        try:
            await self._client.set(platform_state_key(tenant_id, platform_key), epoch)
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc

    # -- 读取 ---------------------------------------------------------------
    # `platform_key=None` 时只校验屏障 + 用户版本（权限层、WebChat 凭证）；
    # 外部断言凭证传 ``platform_key`` 以额外校验平台激活 epoch。

    async def read_permission(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        *,
        expected_user_version: int,
        platform_key: str | None = None,
        expected_epoch: str | None = None,
    ) -> CacheReadResult:
        return await self._read(
            tenant_id,
            permission_key(tenant_id, user_id),
            user_version_key(tenant_id, user_id),
            expected_user_version=expected_user_version,
            platform_key=platform_key,
            expected_epoch=expected_epoch,
        )

    async def read_credential(
        self,
        tenant_id: str,
        token_hash: str,
        user_id: uuid.UUID,
        *,
        platform_key: str,
        expected_user_version: int,
        expected_epoch: str | None = None,
    ) -> CacheReadResult:
        return await self._read(
            tenant_id,
            credential_key(tenant_id, platform_key, token_hash),
            user_version_key(tenant_id, user_id),
            expected_user_version=expected_user_version,
            platform_key=platform_key,
            expected_epoch=expected_epoch,
        )

    async def _read(
        self,
        tenant_id: str,
        cache_key: str,
        version_key: str,
        *,
        expected_user_version: int,
        platform_key: str | None,
        expected_epoch: str | None,
    ) -> CacheReadResult:
        check_epoch = "1" if (platform_key is not None and expected_epoch is not None) else "0"
        keys = [
            barrier_key(tenant_id),
            cache_key,
            version_key,
            platform_state_key(tenant_id, platform_key or "__none__"),
        ]
        try:
            raw = await self._client.eval(
                _READ_CACHED,
                len(keys),
                *keys,
                str(expected_user_version),
                expected_epoch or "",
                check_epoch,
            )
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc
        status_code, value = int(raw[0]), raw[1]
        status = _STATUS[status_code]
        text = value.decode() if isinstance(value, (bytes, bytearray)) else str(value)
        if status == "blocked":
            return CacheReadResult(status=status, fence=text or None)
        return CacheReadResult(status=status, value=text or None)

    # -- 回填 ---------------------------------------------------------------

    async def write_permission(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        payload: dict[str, Any],
        *,
        start_fence: str | None,
        expected_user_version: int,
        platform_key: str | None = None,
        expected_epoch: str | None = None,
    ) -> bool:
        return await self._write_cas(
            tenant_id,
            permission_key(tenant_id, user_id),
            user_version_key(tenant_id, user_id),
            payload,
            start_fence=start_fence,
            expected_user_version=expected_user_version,
            platform_key=platform_key,
            expected_epoch=expected_epoch,
        )

    async def write_credential(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        token_hash: str,
        payload: dict[str, Any],
        *,
        platform_key: str,
        start_fence: str | None,
        expected_user_version: int,
        expected_epoch: str | None = None,
    ) -> bool:
        key = credential_key(tenant_id, platform_key, token_hash)
        ok = await self._write_cas(
            tenant_id,
            key,
            user_version_key(tenant_id, user_id),
            payload,
            start_fence=start_fence,
            expected_user_version=expected_user_version,
            platform_key=platform_key,
            expected_epoch=expected_epoch,
        )
        if ok:
            await self._add_reverse_index(tenant_id, platform_key, user_id, key, self._ttl)
        return ok

    async def _write_cas(
        self,
        tenant_id: str,
        cache_key: str,
        version_key: str,
        payload: dict[str, Any],
        *,
        start_fence: str | None,
        expected_user_version: int,
        platform_key: str | None,
        expected_epoch: str | None,
    ) -> bool:
        check_epoch = "1" if (platform_key is not None and expected_epoch is not None) else "0"
        keys = [
            barrier_key(tenant_id),
            cache_key,
            version_key,
            platform_state_key(tenant_id, platform_key or "__none__"),
        ]
        args = [
            start_fence or "",
            str(expected_user_version),
            expected_epoch or "",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            str(self._ttl),
            check_epoch,
        ]
        try:
            result = await self._client.eval(_WRITE_CACHED_CAS, len(keys), *keys, *args)
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc
        return bool(result)

    # -- 精简读写（无屏障/无 fence/无版本）---------------------------------
    #
    # 供外部断言（MSSWWEB）读路径使用：凭证层只缓存「验签结论」，安全性来自键是断言原文的
    # SHA-256（任何改动都会 miss 并回源完整验签），不依赖屏障协议。**权限层不得走这里**——
    # 授权必须经 `read_permission` / `write_permission` 的版本/fence 校验，保证撤权即时生效。
    # Redis 异常一律转 `AuthCacheUnavailable`；调用方（Gateway 读侧）据此回源，不 fail closed。

    async def read_credential_plain(
        self, tenant_id: str, platform_key: str, token_hash: str
    ) -> str | None:
        """读取凭证原文；未命中返回 ``None``。不校验屏障/版本/epoch。"""
        try:
            raw = await self._client.get(credential_key(tenant_id, platform_key, token_hash))
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)

    async def write_credential_plain(
        self,
        tenant_id: str,
        platform_key: str,
        token_hash: str,
        user_id: uuid.UUID,
        payload: str,
        *,
        expires_at_epoch: float,
        now_epoch: float,
    ) -> None:
        """写入凭证并按平台登记反向索引。

        TTL = ``max(0, min(闲置窗口, expires_at - now))``：闲置窗口取 Store 构造时的
        `ttl_sec`（既有 `external_assertion_cache_ttl_sec`），绝对到期是硬封顶。
        """
        remaining = int(expires_at_epoch - now_epoch)
        ttl_sec = max(0, min(self._ttl, remaining))
        if ttl_sec <= 0:
            return
        key = credential_key(tenant_id, platform_key, token_hash)
        try:
            await self._client.set(key, payload, ex=ttl_sec)
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc
        await self._add_reverse_index(tenant_id, platform_key, user_id, key, ttl_sec)

    async def read_permission_plain(
        self, tenant_id: str, user_id: uuid.UUID
    ) -> str | None:
        """读取权限层原文；未命中返回 ``None``。不校验屏障/版本/epoch。

        安全性来自：任何授权变更都会经 `AuthCacheDirectory.invalidate` 删除该键，
        故 miss 必然回源 PG；命中即「自上次变更以来未变」。
        """
        try:
            raw = await self._client.get(permission_key(tenant_id, user_id))
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)

    async def write_permission_plain(
        self, tenant_id: str, user_id: uuid.UUID, payload: str
    ) -> None:
        """写入权限层，TTL 用 Store 的闲置窗口。"""
        if self._ttl <= 0:
            return
        try:
            await self._client.set(permission_key(tenant_id, user_id), payload, ex=self._ttl)
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc

    async def touch_permission(self, tenant_id: str, user_id: uuid.UUID) -> bool:
        """命中后续期闲置窗口。"""
        if self._ttl <= 0:
            return False
        try:
            return bool(await self._client.expire(permission_key(tenant_id, user_id), self._ttl))
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc

    async def touch_credential(
        self,
        tenant_id: str,
        platform_key: str,
        token_hash: str,
        user_id: uuid.UUID,
        *,
        expires_at_epoch: float,
        now_epoch: float,
    ) -> bool:
        """命中后延长闲置窗口，但**绝不越过绝对到期**，并同步续期反向索引。

        TTL = ``max(0, min(闲置窗口, expires_at - now))``——闲置窗口取 Store 构造时的
        `ttl_sec`，与 `RedisSessionStore._ttl_seconds` 同口径。键已消失返回 ``False``。

        索引必须跟着续期：只续凭证会让索引先过期，之后停用用户/删除身份时按索引
        找不到凭证键，旧 token 便删不掉、继续放行。
        """
        remaining = int(expires_at_epoch - now_epoch)
        ttl = max(0, min(self._ttl, remaining))
        if ttl <= 0:
            return False
        try:
            alive = bool(
                await self._client.expire(credential_key(tenant_id, platform_key, token_hash), ttl)
            )
            if alive:
                index = user_reverse_index_key(tenant_id, platform_key, user_id)
                await self._client.expire(index, ttl)
            return alive
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc

    # -- 定向删除 -----------------------------------------------------------

    async def delete_permission(self, tenant_id: str, user_id: uuid.UUID) -> None:
        try:
            await self._client.delete(permission_key(tenant_id, user_id))
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc

    async def delete_credentials(
        self, tenant_id: str, user_id: uuid.UUID, *, platform_keys: list[str]
    ) -> None:
        """删除该用户在这些平台下的全部凭证项。

        索引按平台拆开后每个平台一个 set，直接 ``SMEMBERS`` 即可，不 ``SCAN``：
        Redis Cluster 下 ``SCAN`` 无法跨节点枚举，因此调用方须给出平台列表
        （「停用用户」由 PG 的身份行反查得到）。
        """
        if not platform_keys:
            return
        try:
            async with self._client.pipeline(transaction=False) as pipe:
                for platform_key in platform_keys:
                    pipe.smembers(user_reverse_index_key(tenant_id, platform_key, user_id))
                member_sets = await pipe.execute()
            cred_keys = [
                m.decode() if isinstance(m, (bytes, bytearray)) else str(m)
                for members in member_sets
                for m in members
            ]
            if cred_keys:
                await self._client.delete(*cred_keys)
            await self._client.delete(
                *[user_reverse_index_key(tenant_id, pk, user_id) for pk in platform_keys]
            )
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc

    async def delete_users_permissions(
        self, tenant_id: str, user_ids: list[uuid.UUID]
    ) -> None:
        """批量删除一批用户的权限缓存（Agent/入口停用反查出的集合）；一次 DEL，不做 N+1。"""
        if not user_ids:
            return
        try:
            await self._client.delete(*[permission_key(tenant_id, uid) for uid in user_ids])
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc

    async def _add_reverse_index(
        self,
        tenant_id: str,
        platform_key: str,
        user_id: uuid.UUID,
        cache_key: str,
        ttl_sec: int,
    ) -> None:
        """登记反向索引并为其续期。

        Set 无法给单个成员设 TTL，改为给整个索引续期：只要该用户还有活凭证，索引就
        持续存活（每次写入刷新）；凭证全部过期后索引整体过期，不会无界增长。
        """
        if ttl_sec <= 0:
            return
        try:
            index = user_reverse_index_key(tenant_id, platform_key, user_id)
            await self._client.sadd(index, cache_key)
            await self._client.expire(index, ttl_sec)
        except Exception as exc:  # noqa: BLE001
            raise AuthCacheUnavailable(type(exc).__name__) from exc
