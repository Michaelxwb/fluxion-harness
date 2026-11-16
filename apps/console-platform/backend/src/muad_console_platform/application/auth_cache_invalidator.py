"""认证缓存的同步失效（设计 §管理变更的同步失效协议）。

**为什么需要它**：Redis 里的权限/凭证缓存让资源请求不必每跳回源 PG，但撤权必须在秒级生效，
又不能靠"TTL 到期"保证（TTL 只负责回收数据）。每次权限变更都在**同一请求事务提交前**调用这里，
把屏障关掉、删掉受影响用户的缓存、递增其 `auth_revision` 并发布新版本，再恢复 READY；任何
Redis 失步都抛错，让调用方的事务与业务变更一起回滚——绝不出现"管理端显示撤权成功、网关仍放行"。

**与 `ChatSessionDirectory` 的关系**：两者是同一处调用点的姊妹动作（grant/revoke、用户停用等
都会同时调用）。`ChatSessionDirectory` 维护旧的 CHAT 会话副本；本模块维护新的分层认证缓存
（凭证层/权限层/状态层），并按租户屏障串行化。

**调用顺序**（handler 内、请求事务提交前）：

.. code-block:: python

    await AuthCacheDirectory(session, cache=app.state.auth_cache).invalidate(
        tenant_id, plan=InvalidationPlan(user_ids=(uid,))
    )
    # 返回后由请求边界统一 commit

`invalidate` 内部：

1. 取租户级事务 advisory lock + 关屏障为 BLOCKED 并写入新随机 `cache_fence`
   （拒绝所有起始 fence 更早的回源回填）。
2. 定向删除受影响用户的权限缓存（必要时连凭证一起删）。
3. `UPDATE platform_user SET auth_revision = auth_revision + 1 ... RETURNING`，拿到新版本；
   与业务变更同属调用方未提交的事务。
4. 把新版本发布到 Redis，屏障切回 `READY:<fence>`；协调行收敛 `READY`。

**崩溃/失败**：第 1、2 步失败 → 屏障保持 BLOCKED、抛 `AuthCacheInvalidationUnavailable`，业务
回滚；屏障由启动恢复按 PG 实际状态收敛（`recover_tenant`）。第 3 步之后的 Redis 失败同理回滚
业务，由恢复补齐。所有失败都计数，绝不静默吞掉。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import AuthCacheSyncState, PlatformUser
from ..metrics import AUTH_CACHE_INVALIDATION_METRIC, record_reason

logger = logging.getLogger(__name__)

#: 屏障阶段常量。
PHASE_PREPARING = "PREPARING"
PHASE_COMMITTED = "COMMITTED"
PHASE_READY = "READY"
SCOPE_TENANT = "TENANT"
SCOPE_PLATFORM = "PLATFORM"


class AuthCacheInvalidationUnavailable(RuntimeError):
    """认证缓存失效无法完成：调用方必须回滚业务变更（不展示"已生效"）。"""


@dataclass(frozen=True, slots=True)
class InvalidationPlan:
    """一次授权变更的影响范围（调用方在业务变更前算好）。

    - `user_ids`：直接受影响的用户（grant 增删、身份变更、用户停用）。
    - `delete_credentials`：是否连凭证一起删（用户停用、外部身份/平台配置变化）。
    - `bump_revision`：是否由本模块递增版本。业务服务已自行递增（如 `_touch_auth_revision`）
      时置 False，本模块只读取当前版本发布，避免重复递增。
    """

    user_ids: tuple[uuid.UUID, ...] = ()
    delete_credentials: bool = False
    bump_revision: bool = True

    def is_empty(self) -> bool:
        return not self.user_ids


def _tenant_lock_key(tenant_id: str) -> int:
    """租户级 advisory lock 键：同一租户的授权变更在此串行化。"""
    digest = hashlib.sha256(f"{tenant_id}|auth-cache-sync".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def auth_cache_directory(session: AsyncSession, app_state: object) -> AuthCacheDirectory:
    """从 `app.state` 取已装配的认证缓存，构造目录；未装配时为 None 缓存（空操作）。"""
    cache = getattr(app_state, "auth_cache", None)
    return AuthCacheDirectory(session, cache=cache)


class AuthCacheDirectory:
    """把一次授权变更同步进分层认证缓存；无 Redis 时是空操作（fail-open 于装配缺失）。"""

    def __init__(self, session: AsyncSession, *, cache) -> None:
        self._session = session
        self._cache = cache

    async def invalidate(self, tenant_id: str, *, plan: InvalidationPlan) -> None:
        """执行完整失效；`cache` 未装配或 plan 为空时是空操作。"""
        if self._cache is None or plan.is_empty():
            return
        from muad_common.auth_cache import new_fence

        fence = new_fence()
        # 串行化同租户失效（锁随调用方事务结束释放）。
        await self._session.execute(
            select(sa.func.pg_advisory_xact_lock(_tenant_lock_key(tenant_id)))
        )
        versions = await self._phase_prepare(tenant_id, fence, plan)
        await self._phase_publish_and_ready(tenant_id, fence, versions)

    async def _phase_prepare(
        self, tenant_id: str, fence: str, plan: InvalidationPlan
    ) -> dict[uuid.UUID, int]:
        cache = self._cache
        try:
            # 阶段 1：关屏障 + 换 fence（BLOCKED 不放行、拒绝旧回填）。
            await cache.set_blocked(tenant_id, fence)
            # 定向删除权限缓存（必要时连凭证一起删）。
            await cache.delete_users_permissions(tenant_id, list(plan.user_ids))
            if plan.delete_credentials:
                for uid in plan.user_ids:
                    await cache.delete_credentials(tenant_id, uid)
        except Exception as exc:  # noqa: BLE001 - Redis 客户端边界
            record_reason(AUTH_CACHE_INVALIDATION_METRIC, "prepare_failed", {})
            raise AuthCacheInvalidationUnavailable(type(exc).__name__) from exc
        # 递增受影响用户版本并拿到新值；与业务变更同一未提交事务。
        # `bump_revision=False` 时业务服务已递增，这里只读取当前版本发布。
        versions: dict[uuid.UUID, int] = {}
        if plan.user_ids:
            if plan.bump_revision:
                rows = await self._session.execute(
                    sa.update(PlatformUser)
                    .where(PlatformUser.id.in_(plan.user_ids))
                    .values(
                        auth_revision=PlatformUser.auth_revision + 1,
                        update_time=datetime.now(UTC),
                    )
                    .returning(PlatformUser.id, PlatformUser.auth_revision)
                )
                versions = {row[0]: int(row[1]) for row in rows}
            else:
                rows = await self._session.execute(
                    select(PlatformUser.id, PlatformUser.auth_revision).where(
                        PlatformUser.id.in_(plan.user_ids)
                    )
                )
                versions = {row[0]: int(row[1]) for row in rows}
        await self._write_state(tenant_id, phase=PHASE_PREPARING, fence=fence, plan=plan)
        return versions

    async def _phase_publish_and_ready(
        self, tenant_id: str, fence: str, versions: dict[uuid.UUID, int]
    ) -> None:
        cache = self._cache
        try:
            for uid, version in versions.items():
                await cache.set_user_version(tenant_id, uid, version)
            # 屏障切回 READY 并携带新 fence：此后起始 fence 更早的回填都会被 CAS 拒绝。
            await cache.set_ready(tenant_id, fence)
        except Exception as exc:  # noqa: BLE001
            record_reason(AUTH_CACHE_INVALIDATION_METRIC, "publish_failed", {})
            raise AuthCacheInvalidationUnavailable(type(exc).__name__) from exc
        await self._write_state(tenant_id, phase=PHASE_READY, fence=fence, plan=None)
        record_reason(AUTH_CACHE_INVALIDATION_METRIC, "ok", {})

    async def _write_state(
        self,
        tenant_id: str,
        *,
        phase: str,
        fence: str,
        plan: InvalidationPlan | None,
    ) -> None:
        row = (
            await self._session.execute(
                select(AuthCacheSyncState).where(
                    AuthCacheSyncState.tenant_id == tenant_id,
                    AuthCacheSyncState.scope == SCOPE_TENANT,
                    AuthCacheSyncState.scope_key == "",
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = AuthCacheSyncState(tenant_id=tenant_id, scope=SCOPE_TENANT, scope_key="")
            self._session.add(row)
        row.phase = phase
        row.cache_fence = fence
        row.operation_id = uuid.uuid4() if phase != PHASE_READY else None
        if plan is not None:
            row.affected_scope = {"users": [str(uid) for uid in plan.user_ids]}
        row.update_time = datetime.now(UTC)
        await self._session.flush()

    async def recover_tenant(self, tenant_id: str) -> None:
        """启动恢复：把上次中断留下的非 READY 协调行收敛到 READY。

        `PREPARING`/`COMMITTED` 都表示"上次失效可能没跑完"：清掉受影响用户的权限缓存并按 PG
        当前版本重新发布，再置 READY。期间屏障维持关闭；失败记录 `last_error` 并保持关闭
        （不放行旧授权），由下次启动或运维重试。
        """
        if self._cache is None:
            return
        row = (
            await self._session.execute(
                select(AuthCacheSyncState).where(
                    AuthCacheSyncState.tenant_id == tenant_id,
                    AuthCacheSyncState.scope == SCOPE_TENANT,
                    AuthCacheSyncState.scope_key == "",
                )
            )
        ).scalar_one_or_none()
        if row is None or row.phase == PHASE_READY:
            return
        fence = row.cache_fence or uuid.uuid4().hex
        affected = [uuid.UUID(raw) for raw in (row.affected_scope or {}).get("users", [])]
        try:
            await self._cache.set_blocked(tenant_id, fence)
            for uid in affected:
                await self._cache.delete_permission(tenant_id, uid)
                version = await self._session.scalar(
                    select(PlatformUser.auth_revision).where(PlatformUser.id == uid)
                )
                if version is not None:
                    await self._cache.set_user_version(tenant_id, uid, int(version))
            await self._cache.set_ready(tenant_id, fence)
        except Exception as exc:  # noqa: BLE001
            record_reason(AUTH_CACHE_INVALIDATION_METRIC, "recover_failed", {})
            row.last_error = type(exc).__name__
            row.recovery_count += 1
            row.update_time = datetime.now(UTC)
            await self._session.commit()
            raise
        row.phase = PHASE_READY
        row.operation_id = None
        row.last_error = None
        row.recovery_count += 1
        row.update_time = datetime.now(UTC)
        await self._session.commit()
        record_reason(AUTH_CACHE_INVALIDATION_METRIC, "recovered", {})
