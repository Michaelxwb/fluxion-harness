"""全局准入阈值的单调版本应用（设计 §3.2 架构与执行边界 / §3.7 配置契约与生效边界）。

Console capacity 设置是唯一来源；`runtime.run_admission_policy` 是 Runtime 侧的 PG 阈值投影。
受理事务的锁序为：Conversation → Run → USER 计数 → TENANT 计数；策略行不在热路径加锁，
本模块负责单调版本应用（单条原子 upsert）与计数校验：

- 配置元信息（设置快照）在短事务**外**取得，事务内只把更高 `source_settings_revision` 用原子
  upsert 应用到 policy 行并读回判定；所有实例按同一 PG 行计数，不按每 Pod 本地缓存独立判定
  （§3.7）；
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
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.platform_settings import (
    CapacitySettings,
    PlatformSettingsError,
    parse_platform_settings,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.models.runtime import RunRecord
from ..infrastructure.run_admission_store import SCOPE_USER, RunAdmissionStore
from ..metrics import (
    RUN_ADMISSION_FLOOR_METRIC,
    RUN_ADMISSION_METRIC,
    record_counter,
    record_outcome,
)
from .ports import PlatformSettingsClient, PlatformSettingsSnapshot

CAPACITY_SECTION = "capacity"
#: 新 Run 的初始排队态：受理后等 Runtime 执行槽（`runtime.run_record.queue_stage` 表级 CHECK 同源）。
QUEUE_STAGE_WAITING_RUNTIME = "WAITING_RUNTIME"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CapacityLimits:
    """一次配置快照里的接纳上限（单位：非终态 Run 数）。"""

    max_open_runs_per_tenant: int
    max_open_runs_per_user: int


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    """事务外取得的一次配置：Console 设置版本 + capacity 阈值 + 本次受理冻结的排队预算。

    `queue_wait_timeout_sec` 只随本次受理冻进 Run 行（`queue_deadline_at`），不参与跨实例
    阈值判定，因此不进 `run_admission_policy` 投影行（§3.7：投影只承载接纳上限）。
    """

    revision: int
    limits: CapacityLimits
    queue_wait_timeout_sec: int = CapacitySettings().queue_wait_timeout_sec


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
        queue_wait_timeout_sec=capacity.queue_wait_timeout_sec,
    )


def queue_deadline(
    queued_at: datetime, wait_timeout_sec: int, run_deadline_at: datetime | None
) -> datetime:
    """初始排队期限：`min(受理 + 配置, 原 Run 绝对 deadline)`（§3.7）。

    只约束开始执行前的等待：首次受理确定后 HTTP 重试/Pod 接管都不延长，执行阶段仍按原 Run
    绝对期限收尾（不因排队期限过去而拒绝合法完成）。
    """
    deadline = queued_at + timedelta(seconds=wait_timeout_sec)
    if run_deadline_at is None:
        return deadline
    return min(deadline, run_deadline_at)


async def release_admission_in(session: AsyncSession, *, run: RunRecord) -> bool:
    """终态释放（与 Run 终态更新**同事务**）：`admission_released_at` CAS 恰一次 → USER→TENANT 递减。

    - CAS（`admission_released_at IS NULL`）保证每个 Run 只释放一次：并发取消/收尾/回收的
      落败者拿到 0 行即返回 False，绝不二次递减（幂等重放同理，不双扣也不欠账）；
    - 锁序：调用方已持有 Conversation → Run 行锁，本函数只追加 USER → TENANT 计数行锁；
      终态释放**不需要** policy 锁，也不从计数行倒锁会话（§3.2 固定锁序）；
    - 计数行缺失按 0 建立后截断在 0（表级 CHECK `open_run_count >= 0`）：早于准入计数的历史
      Run 不会把共享计数压成负数；截断说明投影与 Run 事实不一致，留告警（降级可见）。
    """
    store = RunAdmissionStore(session)
    won = await session.scalar(
        sa.update(RunRecord)
        .where(
            RunRecord.id == run.id,
            RunRecord.tenant_id == run.tenant_id,
            RunRecord.admission_released_at.is_(None),
        )
        .values(admission_released_at=sa.func.now(), update_time=sa.func.now())
        .returning(RunRecord.id)
    )
    if won is None:
        return False
    # 只回收**用户级**计数：租户上限按 `run_record` 在飞数实时判定（见 `check_counts_in`），
    # 不再有共享的 TENANT 计数行——它曾把每次准入/释放都串行化在同一行锁上。
    held = await store.lock_counter(
        tenant_id=run.tenant_id, scope_type=SCOPE_USER, scope_key=str(run.user_id)
    )
    if held == 0:
        record_counter(RUN_ADMISSION_FLOOR_METRIC)
        logger.warning(
            "admission_counter_floor tenant=%s scope=%s scope_key=%s run=%s",
            run.tenant_id,
            SCOPE_USER,
            run.user_id,
            run.id,
        )
    await store.decrement_counter(
        tenant_id=run.tenant_id, scope_type=SCOPE_USER, scope_key=str(run.user_id)
    )
    return True


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
                    policy = await self.apply_policy_in(
                        session, tenant_id=tenant_id, capacity=capacity
                    )
                except AdmissionPolicyStaleError as stale:
                    # 先退出事务再刷新：重取仍在事务外，事务内不联网。
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
        record_outcome(RUN_ADMISSION_METRIC, ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE)
        raise AppError(ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE)

    async def apply_policy_in(
        self, session: AsyncSession, *, tenant_id: str, capacity: CapacitySnapshot
    ) -> AdmissionPolicy:
        """无锁应用策略版本：读 → 更高版本时原子 upsert → 读回判定。

        热路径不持有策略行锁（原 `SELECT … FOR UPDATE` 把每个受理事务串行化）；并发应用同一
        更高版本由单条 upsert 幂等收敛。更低版本抛 `AdmissionPolicyStaleError`（调用方回滚
        后重取）；同版本给出不同阈值说明投影损坏，显式失败——两种情况下都不放行。
        """
        store = RunAdmissionStore(session)
        row = await store.read_policy(tenant_id=tenant_id)
        if row is None or capacity.revision > row.source_settings_revision:
            row = await store.upsert_policy(
                tenant_id=tenant_id,
                revision=capacity.revision,
                max_open_runs_per_tenant=capacity.limits.max_open_runs_per_tenant,
                max_open_runs_per_user=capacity.limits.max_open_runs_per_user,
                now=datetime.now(UTC),
            )
        if capacity.revision < row.source_settings_revision:
            raise AdmissionPolicyStaleError(
                tenant_id=tenant_id,
                fetched_revision=capacity.revision,
                policy_revision=row.source_settings_revision,
            )
        if (row.max_open_runs_per_tenant, row.max_open_runs_per_user) != (
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
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        user_id: uuid.UUID,
        policy: AdmissionPolicy,
        exclude_run_id: uuid.UUID | None = None,
    ) -> None:
        """计数校验：用户上限严格（行锁），租户上限按在飞 Run 实时计数（无锁护栏）。

        用户级是公平性约束，仍用 `run_admission_counter(USER)` 行锁严格判定（每用户互不相干，
        不构成跨用户串行）；租户上限是护栏值（远高于真实用量），改为对 `run_record` 的
        `admission_released_at IS NULL` 实时计数——**不再锁共享的 TENANT 计数行**：那把锁曾
        把 1000 个互不相关的用户的每次准入/释放串成一条全局队列（2026-10-20 真 Pod 实测：
        准入延迟随用户数线性上涨、加 Pod 无效）。并发窗口内允许少量软超发，用户级仍精确。
        """
        store = RunAdmissionStore(session)
        user_count = await store.lock_counter(
            tenant_id=tenant_id, scope_type=SCOPE_USER, scope_key=str(user_id)
        )
        if user_count >= policy.limits.max_open_runs_per_user:
            record_outcome(RUN_ADMISSION_METRIC, ErrorCode.RUN_CAPACITY_EXCEEDED)
            raise AppError(ErrorCode.RUN_CAPACITY_EXCEEDED)
        tenant_count = await store.count_open_runs(
            tenant_id=tenant_id,
            exclude_run_id=exclude_run_id,
            limit=policy.limits.max_open_runs_per_tenant,
        )
        if tenant_count >= policy.limits.max_open_runs_per_tenant:
            record_outcome(RUN_ADMISSION_METRIC, ErrorCode.RUN_CAPACITY_EXCEEDED)
            raise AppError(ErrorCode.RUN_CAPACITY_EXCEEDED)

    async def reserve_counts_in(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        user_id: uuid.UUID,
        policy: AdmissionPolicy,
        exclude_run_id: uuid.UUID | None = None,
    ) -> None:
        """受理占位：用户级行锁严格自增；租户上限按实时在飞数判定（无共享计数行）。

        调用方在同一事务内先插入 Run 行再调用本方法（`exclude_run_id` 即该行）；达用户阈值即
        `RUN_CAPACITY_EXCEEDED`，调用方回滚整个事务——Run、会话、提交、事件与用户计数一起
        消失，不留半建 Run，也不留下只增不减的占位。用户级阈值语义保持精确。
        """
        await self.check_counts_in(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            policy=policy,
            exclude_run_id=exclude_run_id,
        )
        store = RunAdmissionStore(session)
        await store.increment_counter(
            tenant_id=tenant_id, scope_type=SCOPE_USER, scope_key=str(user_id)
        )
        record_outcome(RUN_ADMISSION_METRIC, "ACCEPTED")
