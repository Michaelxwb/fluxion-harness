"""全局准入阈值的单调版本应用（设计 §3.2 架构与执行边界 / §3.7 配置契约与生效边界）。

Console capacity 设置是唯一来源；`runtime.run_admission_policy` 是 Runtime 侧的 PG 阈值投影。
受理事务的锁序固定为：策略行锁 → Conversation → Run → USER 计数 → TENANT 计数；本模块负责
第一段（策略行锁 + 单调应用）与计数校验：

- 配置元信息（设置快照）在短事务**外**取得，锁内只把更高 `source_settings_revision` 应用到
  policy 行；所有实例按同一 PG 行计数，不按每 Pod 本地缓存独立判定（§3.7）；
- 读到较低版本：退出事务后才重新获取一次有效配置；仍低即显式失败，绝不覆盖更高版本、
  绝不按旧阈值放行；坏文档/源失败同样显式失败，不回落 schema 默认值；
- 降额只影响新受理：已有 Run 与计数行不被改动，新请求要等计数低于有效阈值才可受理；
- 计数校验按 USER → TENANT 顺序锁行判定，达到有效阈值即拒（不新增占位）。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.platform_settings import (
    CapacitySettings,
    PlatformSettingsError,
    parse_platform_settings,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.run_admission_store import SCOPE_TENANT, SCOPE_USER, RunAdmissionStore
from .ports import PlatformSettingsClient, PlatformSettingsSnapshot

CAPACITY_SECTION = "capacity"


@dataclass(frozen=True, slots=True)
class CapacityLimits:
    """一次配置快照里的接纳上限（单位：非终态 Run 数）。"""

    max_open_runs_per_tenant: int
    max_open_runs_per_user: int


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    """事务外取得的一次配置：Console 设置版本 + capacity 阈值。"""

    revision: int
    limits: CapacityLimits


@dataclass(frozen=True, slots=True)
class AdmissionPolicy:
    """受理事务内生效的准入策略：来自 PG 投影行，不是本地缓存值。"""

    tenant_id: str
    source_settings_revision: int
    limits: CapacityLimits


class AdmissionPolicyStaleError(RuntimeError):
    """读到较低设置版本：调用方必须退出事务重取，不得按旧阈值放行或覆盖更高版本。"""

    def __init__(self, *, tenant_id: str, fetched_revision: int, policy_revision: int) -> None:
        super().__init__(
            f"admission policy revision {policy_revision} is newer than fetched {fetched_revision}"
        )
        self.tenant_id = tenant_id
        self.fetched_revision = fetched_revision
        self.policy_revision = policy_revision


def parse_capacity(snapshot: PlatformSettingsSnapshot) -> CapacitySnapshot:
    """把设置快照解析成 capacity 阈值：缺失分组回落 schema 默认，坏文档显式失败。"""
    section: Any = snapshot.settings.get(CAPACITY_SECTION)
    if section is not None and not isinstance(section, Mapping):
        raise AppError(ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE)
    try:
        capacity = (
            CapacitySettings()
            if section is None
            else parse_platform_settings({CAPACITY_SECTION: dict(section)}).capacity
        )
    except PlatformSettingsError as exc:
        raise AppError(ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE) from exc
    return CapacitySnapshot(
        revision=snapshot.revision,
        limits=CapacityLimits(
            max_open_runs_per_tenant=capacity.max_open_runs_per_tenant,
            max_open_runs_per_user=capacity.max_open_runs_per_user,
        ),
    )


class AdmissionPolicyService:
    def __init__(
        self,
        session_factory_provider: SessionFactoryProvider,
        settings_client: PlatformSettingsClient,
        *,
        stale_refresh_attempts: int = 1,
    ) -> None:
        self._session_factory_provider = session_factory_provider
        self._settings_client = settings_client
        self._stale_refresh_attempts = max(stale_refresh_attempts, 0)
        self._logger = logging.getLogger(__name__)

    async def fetch_capacity(self, *, tenant_id: str, trace_id: str = "") -> CapacitySnapshot:
        """事务外取一次配置（真实 Console 内部 HTTP）；源失败/坏文档都不回落默认值。"""
        snapshot = await self._settings_client.fetch_snapshot(tenant_id=tenant_id, trace_id=trace_id)
        return parse_capacity(snapshot)

    @property
    def stale_refresh_attempts(self) -> int:
        """低版本时「退出事务后重取有效配置」的允许次数（与 `resolve` 同一口径）。"""
        return self._stale_refresh_attempts

    async def resolve(self, *, tenant_id: str, trace_id: str = "") -> AdmissionPolicy:
        """投影流程：事务外取配置 → 锁内应用更高版本 → 低版本退出事务后重取一次。"""
        for _ in range(self._stale_refresh_attempts + 1):
            capacity = await self.fetch_capacity(tenant_id=tenant_id, trace_id=trace_id)
            async with self._session_factory_provider()() as session:
                try:
                    policy = await self.lock_and_apply_in(
                        session, tenant_id=tenant_id, capacity=capacity
                    )
                except AdmissionPolicyStaleError as stale:
                    # 先退出事务（连同策略行锁）再刷新：锁内不联网，重取仍在事务外。
                    await session.rollback()
                    self._logger.warning(
                        "admission_policy_stale tenant=%s fetched=%s policy=%s",
                        stale.tenant_id,
                        stale.fetched_revision,
                        stale.policy_revision,
                    )
                    continue
                await session.commit()
                return policy
        raise AppError(ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE)

    async def lock_and_apply_in(
        self, session: AsyncSession, *, tenant_id: str, capacity: CapacitySnapshot
    ) -> AdmissionPolicy:
        """准入事务第一条语句：策略行锁先行，锁内只应用更高设置版本。

        更低版本抛 `AdmissionPolicyStaleError`（调用方回滚后重取）；同一版本给出不同阈值
        说明投影已损坏，同样显式失败——两种情况下都不放行。
        """
        store = RunAdmissionStore(session)
        row = await store.lock_policy(
            tenant_id=tenant_id,
            revision=capacity.revision,
            max_open_runs_per_tenant=capacity.limits.max_open_runs_per_tenant,
            max_open_runs_per_user=capacity.limits.max_open_runs_per_user,
        )
        if capacity.revision < row.source_settings_revision:
            raise AdmissionPolicyStaleError(
                tenant_id=tenant_id,
                fetched_revision=capacity.revision,
                policy_revision=row.source_settings_revision,
            )
        if capacity.revision > row.source_settings_revision:
            row = await store.write_policy(
                tenant_id=tenant_id,
                revision=capacity.revision,
                max_open_runs_per_tenant=capacity.limits.max_open_runs_per_tenant,
                max_open_runs_per_user=capacity.limits.max_open_runs_per_user,
                now=datetime.now(UTC),
            )
        elif (row.max_open_runs_per_tenant, row.max_open_runs_per_user) != (
            capacity.limits.max_open_runs_per_tenant,
            capacity.limits.max_open_runs_per_user,
        ):
            self._logger.warning("admission_policy_revision_conflict tenant=%s", tenant_id)
            raise AppError(ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE)
        return AdmissionPolicy(
            tenant_id=tenant_id,
            source_settings_revision=row.source_settings_revision,
            limits=CapacityLimits(
                max_open_runs_per_tenant=row.max_open_runs_per_tenant,
                max_open_runs_per_user=row.max_open_runs_per_user,
            ),
        )

    async def check_counts_in(
        self, session: AsyncSession, *, tenant_id: str, user_id: uuid.UUID, policy: AdmissionPolicy
    ) -> None:
        """计数校验（USER → TENANT，与 §3.2 锁序一致）：达到有效阈值即拒，不新增占位。"""
        store = RunAdmissionStore(session)
        user_count = await store.lock_counter(
            tenant_id=tenant_id, scope_type=SCOPE_USER, scope_key=str(user_id)
        )
        if user_count >= policy.limits.max_open_runs_per_user:
            raise AppError(ErrorCode.RUN_CAPACITY_EXCEEDED)
        tenant_count = await store.lock_counter(
            tenant_id=tenant_id, scope_type=SCOPE_TENANT, scope_key=tenant_id
        )
        if tenant_count >= policy.limits.max_open_runs_per_tenant:
            raise AppError(ErrorCode.RUN_CAPACITY_EXCEEDED)

    async def reserve_counts_in(
        self, session: AsyncSession, *, tenant_id: str, user_id: uuid.UUID, policy: AdmissionPolicy
    ) -> None:
        """受理占位（USER → TENANT）：达有效阈值即拒，通过则原地自增，与 Run 行**同事务**。

        调用方在同一事务内先插入 Run 行再调用本方法（§3.2 锁序 Conversation → Run → USER →
        TENANT）；任一 scope 达阈值即 `RUN_CAPACITY_EXCEEDED`，调用方回滚整个事务——Run、会话、
        提交、事件与计数一起消失，不留半建 Run，也不留下只增不减的占位。
        """
        await self.check_counts_in(session, tenant_id=tenant_id, user_id=user_id, policy=policy)
        store = RunAdmissionStore(session)
        await store.increment_counter(
            tenant_id=tenant_id, scope_type=SCOPE_USER, scope_key=str(user_id)
        )
        await store.increment_counter(
            tenant_id=tenant_id, scope_type=SCOPE_TENANT, scope_key=tenant_id
        )
