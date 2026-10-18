"""Webchat 保留与清理：历史、流增量、通知与暂存上传（design §3.3/§3.6/§3.7；TASK-069）。

**四类数据各有自己的保留策略**，共用一条清理边界（每次清理开始时取一次平台设置）：

- **流增量重放数据**（`webchat.stream_replay_retention_hours`）：候选是 `stream_type IS NOT NULL`
  且超期的 canonical 行；**保护**活跃 Run 的重放数据与在飞 job 输入边界内的事件。
- **可见聊天历史**（`webchat.history_retention_days`，**0 = 不按时间删除**）：候选是被**最新摘要
  覆盖**的旧 `USER_MESSAGE`/`ASSISTANT_MESSAGE`；**保护**唯一摘要、摘要之后的重建必需事件、
  活跃 Run/job 引用。
- **通知收件箱**（`webchat.notification_retention_days`）：候选是超期且回执为终态的
  `channel_notification` 行；**保护**仍待投递（`PENDING`/`DELIVERING`）/待核实（`UNKNOWN`）的
  回执与 feed 水位。
- **暂存上传**（`webchat.upload_staging_ttl_sec`，即行的 `expires_at`）：候选是超期且未归属的
  `channel_upload` 行；**保护**仍被在飞 Run 引用的登记与宽限期内的文件。

为什么"清理边界取设置"而不是随 Run 冻结：清理不是 Run。设置变更只影响**下一次清理**（§3.7 的
"下一次清理"生效标记），已受理数据不因保留期变短而失效。

**保护是三个不同的事实，不能合并**：

- **活跃引用**：`runtime.artifact.metadata_json->>'upload_id'` 指向的登记仍被非终态 Run 的制品
  引用 ⇒ 不删；事件的 `run_id` 仍是非终态 Run ⇒ 不删；记忆提炼 job（`PENDING`/`RUNNING`/`RETRY`）
  的 `through_seq` 覆盖的事件/消息是它的输入 ⇒ 不删。
- **唯一上下文来源**：模型摘要（`CONTEXT_SUMMARY`）是压缩后重建的权威前缀，**永不进入候选**；
  只有被最新摘要 `covers_up_to_seq` 覆盖的旧可见消息才不是唯一来源，才可回收。
- **孤儿宽限**：暂存文件的 `st_mtime` 在宽限期内视为"写入仍在进行"⇒ 本轮不动（与 Console
  产物清理同一口径：宽限期看文件、保留期看行）。

**软删而不是物理删**：canonical 行与通知行都留 tombstone（`is_deleted=true`）。会话 `last_seq`
与 feed `last_seq` 因此不会回退、`(conversation_id, seq)` 与 `(tenant_id, delivery_key)` 的完整
唯一也不会被释放——被回收的行不可能被"重新分配"成另一个事实。清理后更早的游标由
`EventTailHub.check_cursor` 显式报 `CURSOR_EXPIRED`（含最早可用水位），不静默夹取。

**循环独立**：本模块只做"取一次设置 → 有界批量清理 → 可对账报告"，不碰请求路径、不发网络请求、
不持有长事务；装配根（TASK-096）按独立维护循环调用 `run_retention_sweep`。

**租户谓词**：`tenant_id=None` 是本部署库的全部租户（运维口径，与 Console 产物清理同一默认）；
按租户维护/验收必须能只碰一个租户——一条没有租户谓词的破坏性命令在共享库上没法安全执行
（本参数是写集成验收时被逼出来的，与 Console `cleanup-artifacts --tenant` 同因）。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from muad_artifact_store import NfsArtifactStore
from muad_contracts.platform_settings import parse_platform_settings
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import (
    Artifact,
    CanonicalEvent,
    ChannelNotification,
    ChannelUpload,
    MemoryConsolidationJob,
    RunRecord,
)
from ..metrics import (
    WEBCHAT_RETENTION_METRIC,
    WEBCHAT_RETENTION_REMOVED_METRIC,
    record_counter,
    record_outcome,
)
from .context_events import CONTEXT_SUMMARY_EVENT

logger = logging.getLogger(__name__)

#: 单轮清理的候选上界：清理是破坏性维护动作，有界前进才可中断、可对账。
RETENTION_BATCH_SIZE = 200
#: 暂存文件的孤儿宽限期：文件 mtime 新于 `now - grace` 即视为写入仍在进行。
STAGING_GRACE_SECONDS = 3600.0
#: 可见消息事件词表（历史保留的唯一候选类；摘要与流增量都不在其中）。
VISIBLE_MESSAGE_EVENT_TYPES: tuple[str, ...] = ("USER_MESSAGE", "ASSISTANT_MESSAGE")
#: 非终态 Run 状态：它们的重放数据与可见历史都还"被引用"。
ACTIVE_RUN_STATUSES: tuple[str, ...] = ("CREATED", "RUNNING", "WAITING_TOOL", "WAITING_INPUT")
#: 未结束的记忆提炼 job 状态：其输入边界（`through_seq`）内的事件/消息不得先淘汰。
INFLIGHT_JOB_STATUSES: tuple[str, ...] = ("PENDING", "RUNNING", "RETRY")
#: 终态投递回执：只有它们超期后可隐藏；`PENDING`/`DELIVERING`/`UNKNOWN` 一律保留。
FINAL_DELIVERY_STATES: tuple[str, ...] = ("DELIVERED", "NOT_REQUIRED")

#: 暂存清理的动作词表（也是报告行的行首标记，便于运维对账）。
ACTION_STAGING_REMOVED = "REMOVED"
ACTION_STAGING_ACTIVE_REFERENCE = "SKIPPED_ACTIVE_REFERENCE"
ACTION_STAGING_GRACE = "SKIPPED_WITHIN_GRACE"


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """一次清理边界的保留期（来源是 contracts 的 typed 平台设置，不在此重述默认值）。"""

    stream_replay_retention_hours: int
    notification_retention_days: int
    history_retention_days: int

    def __post_init__(self) -> None:
        for name in ("stream_replay_retention_hours", "notification_retention_days"):
            value = getattr(self, name)
            if isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(self.history_retention_days, bool) or self.history_retention_days < 0:
            raise ValueError("history_retention_days must be a non-negative integer")


def retention_policy_from_settings(settings: Mapping[str, Any] | None) -> RetentionPolicy:
    """平台设置快照 → 保留策略；缺省分组回落 schema 默认（唯一来源在 contracts）。

    坏值/未知键由 `parse_platform_settings` 显式失败——**不静默回落默认值**（RULE-06）。
    """
    webchat = parse_platform_settings(settings).webchat
    return RetentionPolicy(
        stream_replay_retention_hours=webchat.stream_replay_retention_hours,
        notification_retention_days=webchat.notification_retention_days,
        history_retention_days=webchat.history_retention_days,
    )


@dataclass(frozen=True, slots=True)
class StagingCleanupAction:
    """一条可对账的暂存清理动作（运维核对的是"哪份字节被删/被保护、为什么"）。"""

    action: str
    upload_id: uuid.UUID
    storage_key: str
    detail: str = ""

    def line(self) -> str:
        suffix = f" {self.detail}" if self.detail else ""
        return f"{self.action} upload_id={self.upload_id} key={self.storage_key}{suffix}"


@dataclass(frozen=True, slots=True)
class StagingCleanupReport:
    actions: tuple[StagingCleanupAction, ...]

    def count(self, action: str) -> int:
        return sum(1 for item in self.actions if item.action == action)

    @property
    def removed(self) -> int:
        return self.count(ACTION_STAGING_REMOVED)

    @property
    def skipped_active_reference(self) -> int:
        return self.count(ACTION_STAGING_ACTIVE_REFERENCE)

    @property
    def skipped_grace(self) -> int:
        return self.count(ACTION_STAGING_GRACE)

    def lines(self) -> list[str]:
        return [item.line() for item in self.actions]


@dataclass(frozen=True, slots=True)
class RetentionReport:
    """一次完整清理扫描的结果（可对账；不含任何内部载荷或存储键以外的内容）。"""

    stream_replay_removed: int
    history_removed: int
    notifications_removed: int
    staging: StagingCleanupReport

    @property
    def staging_removed(self) -> int:
        return self.staging.removed

    def summary(self) -> str:
        return (
            f"webchat-retention: stream_replay_removed={self.stream_replay_removed} "
            f"history_removed={self.history_removed} "
            f"notifications_removed={self.notifications_removed} "
            f"staging_removed={self.staging_removed} "
            f"staging_skipped_active_reference={self.staging.skipped_active_reference} "
            f"staging_skipped_grace={self.staging.skipped_grace}"
        )


def _require_batch_size(batch_size: int) -> None:
    if isinstance(batch_size, bool) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")


def _require_grace_seconds(grace_seconds: float) -> None:
    if isinstance(grace_seconds, bool) or grace_seconds < 0:
        raise ValueError("grace_seconds must be a non-negative number")


def _active_run_reference() -> Any:
    """关联子查询：本行所属 Run 是否仍是**非终态**（"尚被活跃引用"）。"""
    return (
        sa.select(sa.literal(1))
        .select_from(RunRecord)
        .where(
            RunRecord.id == CanonicalEvent.run_id,
            RunRecord.is_deleted.is_(False),
            RunRecord.status.in_(ACTIVE_RUN_STATUSES),
        )
    )


def _inflight_job_reference() -> Any:
    """关联子查询：本行是否落在某个**未结束**提炼 job 的输入边界内（`through_seq`）。"""
    return (
        sa.select(sa.literal(1))
        .select_from(MemoryConsolidationJob)
        .where(
            MemoryConsolidationJob.tenant_id == CanonicalEvent.tenant_id,
            MemoryConsolidationJob.conversation_id == CanonicalEvent.conversation_id,
            MemoryConsolidationJob.status.in_(INFLIGHT_JOB_STATUSES),
            MemoryConsolidationJob.through_seq >= CanonicalEvent.seq,
        )
    )


def _covered_by_latest_summary() -> Any:
    """`seq <= 最新摘要 covers_up_to_seq`：只有被摘要覆盖的旧消息才不是唯一上下文来源。

    取 `max(covers_up_to_seq)`（而不是"按 seq 取最大摘要再读它的覆盖值"）：覆盖值随 seq 单调，
    两者等价，单次查询即可；`jsonb_typeof = 'number'` 让坏形状的载荷不参与（不因一条历史脏数据
    让整轮清理失败），没有可用摘要时 `coalesce` 成 0——等于"什么都不覆盖"。
    """
    summary = sa.orm.aliased(CanonicalEvent, name="latest_summary")
    cover = summary.payload_json["covers_up_to_seq"]
    latest_cover = (
        sa.select(sa.func.max(sa.cast(cover.astext, sa.BigInteger())))
        .where(
            summary.tenant_id == CanonicalEvent.tenant_id,
            summary.conversation_id == CanonicalEvent.conversation_id,
            summary.event_type == CONTEXT_SUMMARY_EVENT,
            summary.is_deleted.is_(False),
            sa.func.jsonb_typeof(cover) == "number",
        )
        .scalar_subquery()
    )
    return CanonicalEvent.seq <= sa.func.coalesce(latest_cover, 0)


async def _soft_delete_events(
    session: AsyncSession, *, ids: Sequence[uuid.UUID], now: datetime
) -> int:
    if not ids:
        return 0
    await session.execute(
        sa.update(CanonicalEvent)
        .where(CanonicalEvent.id.in_(list(ids)))
        .values(is_deleted=True, update_time=now)
    )
    await session.flush()
    return len(ids)


def _tenant_scope(column: Any, tenant_id: str | None) -> tuple[Any, ...]:
    """租户谓词：`None` = 本部署库的全部租户（运维口径，与 Console 产物清理同口径）。

    按租户拆部署时（§3.7）每实例只有本租户的行；共享库上按租户维护/验收必须能只碰一个租户——
    一条没有租户谓词的破坏性命令在共享库上无法安全执行。
    """
    return () if tenant_id is None else (column == tenant_id,)


async def cleanup_stream_replay(
    session: AsyncSession,
    *,
    policy: RetentionPolicy,
    batch_size: int = RETENTION_BATCH_SIZE,
    tenant_id: str | None = None,
) -> int:
    """回收保留期外的**流增量重放数据**（`stream_type IS NOT NULL` 的 canonical 行）。

    候选只含流事件：可见消息（`stream_type IS NULL`）与模型摘要走各自策略，这里永不触碰。
    活跃 Run 的重放数据必须保留到有安全快照/终态可接管；未结束 job 的输入边界内事件同样保留。
    行锁 + `SKIP LOCKED` 让并发清理者各自前进；软删让 seq 水位与唯一约束都不回退。
    """
    _require_batch_size(batch_size)
    now = await database_now(session)
    cutoff = now - timedelta(hours=policy.stream_replay_retention_hours)
    candidates = (
        await session.scalars(
            sa.select(CanonicalEvent.id)
            .where(
                CanonicalEvent.is_deleted.is_(False),
                CanonicalEvent.stream_type.is_not(None),
                CanonicalEvent.create_time < cutoff,
                *_tenant_scope(CanonicalEvent.tenant_id, tenant_id),
                ~_active_run_reference().exists(),
                ~_inflight_job_reference().exists(),
            )
            .order_by(CanonicalEvent.create_time, CanonicalEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()
    return await _soft_delete_events(session, ids=candidates, now=now)


async def cleanup_visible_history(
    session: AsyncSession,
    *,
    policy: RetentionPolicy,
    batch_size: int = RETENTION_BATCH_SIZE,
    tenant_id: str | None = None,
) -> int:
    """回收保留期外的**可见聊天历史**：只回收被最新摘要覆盖、且无活跃引用的旧消息。

    `history_retention_days = 0` 是显式语义"不按时间自动删除可见聊天历史"，直接返回 0。
    非 0 时也**不删**唯一摘要（`CONTEXT_SUMMARY` 不是候选类）、摘要之后的重建必需事件
    （`seq > covers_up_to_seq`）、活跃 Run 的消息与未结束 job 输入边界内的消息。
    """
    _require_batch_size(batch_size)
    if policy.history_retention_days == 0:
        return 0
    now = await database_now(session)
    cutoff = now - timedelta(days=policy.history_retention_days)
    candidates = (
        await session.scalars(
            sa.select(CanonicalEvent.id)
            .where(
                CanonicalEvent.is_deleted.is_(False),
                CanonicalEvent.event_type.in_(VISIBLE_MESSAGE_EVENT_TYPES),
                CanonicalEvent.stream_type.is_(None),
                CanonicalEvent.create_time < cutoff,
                *_tenant_scope(CanonicalEvent.tenant_id, tenant_id),
                _covered_by_latest_summary(),
                ~_active_run_reference().exists(),
                ~_inflight_job_reference().exists(),
            )
            .order_by(CanonicalEvent.create_time, CanonicalEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()
    return await _soft_delete_events(session, ids=candidates, now=now)


async def cleanup_notifications(
    session: AsyncSession,
    *,
    policy: RetentionPolicy,
    batch_size: int = RETENTION_BATCH_SIZE,
    tenant_id: str | None = None,
) -> int:
    """隐藏保留期外的通知行：只动**终态回执**（`DELIVERED`/`NOT_REQUIRED`）。

    `PENDING`/`DELIVERING`（仍可能重投）与 `UNKNOWN`（待核实）无论多老都保留——隐藏它们会丢掉
    唯一回执。feed 行不参与清理：`last_seq` 不因隐藏通知而重置（软删保留 seq 唯一性）。
    """
    _require_batch_size(batch_size)
    now = await database_now(session)
    cutoff = now - timedelta(days=policy.notification_retention_days)
    candidates = (
        await session.scalars(
            sa.select(ChannelNotification.id)
            .where(
                ChannelNotification.is_deleted.is_(False),
                ChannelNotification.create_time < cutoff,
                ChannelNotification.delivery_state.in_(FINAL_DELIVERY_STATES),
                *_tenant_scope(ChannelNotification.tenant_id, tenant_id),
            )
            .order_by(ChannelNotification.create_time, ChannelNotification.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()
    if not candidates:
        return 0
    await session.execute(
        sa.update(ChannelNotification)
        .where(ChannelNotification.id.in_(list(candidates)))
        .values(is_deleted=True, update_time=now)
    )
    await session.flush()
    return len(candidates)


async def _has_active_reference(session: AsyncSession, row: ChannelUpload) -> bool:
    """暂存登记是否仍被**非终态 Run** 的已受理制品引用（`metadata_json.upload_id`）。"""
    existing = await session.scalar(
        sa.select(sa.literal(1))
        .select_from(Artifact)
        .join(RunRecord, RunRecord.id == Artifact.run_id)
        .where(
            Artifact.tenant_id == row.tenant_id,
            Artifact.is_deleted.is_(False),
            Artifact.metadata_json["upload_id"].astext == str(row.id),
            RunRecord.is_deleted.is_(False),
            RunRecord.status.in_(ACTIVE_RUN_STATUSES),
        )
        .limit(1)
    )
    return existing is not None


def _staged_file_modified(store: NfsArtifactStore, row: ChannelUpload) -> datetime | None:
    """暂存文件的 mtime；键越界（`ValueError`）或文件缺失返回 `None`（只清行、不碰盘）。"""
    try:
        path = store.resolve(row.storage_key)
    except ValueError:
        return None
    if not path.is_file():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


async def _classify_staging(
    session: AsyncSession,
    row: ChannelUpload,
    *,
    store: NfsArtifactStore,
    grace_cutoff: datetime,
) -> StagingCleanupAction:
    if await _has_active_reference(session, row):
        return StagingCleanupAction(
            action=ACTION_STAGING_ACTIVE_REFERENCE,
            upload_id=row.id,
            storage_key=row.storage_key,
            detail="active_run_reference",
        )
    modified = _staged_file_modified(store, row)
    if modified is not None and modified > grace_cutoff:
        return StagingCleanupAction(
            action=ACTION_STAGING_GRACE,
            upload_id=row.id,
            storage_key=row.storage_key,
            detail="within_grace_period",
        )
    return StagingCleanupAction(
        action=ACTION_STAGING_REMOVED,
        upload_id=row.id,
        storage_key=row.storage_key,
        detail="expired",
    )


async def cleanup_staging_uploads(
    session: AsyncSession,
    *,
    artifact_root: str | Path,
    batch_size: int = RETENTION_BATCH_SIZE,
    grace_seconds: float = STAGING_GRACE_SECONDS,
    tenant_id: str | None = None,
) -> StagingCleanupReport:
    """清理过期未归属的暂存登记：**活跃引用与孤儿宽限保护**下软删行 + 删暂存文件。

    - 仍被在飞 Run 的已受理制品引用（`metadata_json.upload_id`）⇒ 不删；
    - 暂存文件 mtime 在宽限期内（写入仍在进行）⇒ 不删；
    - 文件缺失或键越界 ⇒ 只清行（不留悬空行），盘上什么都不碰；
    - 已受理 Run 自己的制品在**另一个键**上（硬链接），不因暂存清理消失。
    """
    _require_batch_size(batch_size)
    _require_grace_seconds(grace_seconds)
    store = NfsArtifactStore(artifact_root)
    now = await database_now(session)
    grace_cutoff = now - timedelta(seconds=grace_seconds)
    rows = list(
        (
            await session.scalars(
                sa.select(ChannelUpload)
                .where(
                    ChannelUpload.is_deleted.is_(False),
                    ChannelUpload.expires_at <= now,
                    *_tenant_scope(ChannelUpload.tenant_id, tenant_id),
                )
                .order_by(ChannelUpload.expires_at, ChannelUpload.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    actions: list[StagingCleanupAction] = []
    for row in rows:
        action = await _classify_staging(session, row, store=store, grace_cutoff=grace_cutoff)
        actions.append(action)
        if action.action != ACTION_STAGING_REMOVED:
            continue
        row.is_deleted = True
        row.update_time = now
        _remove_staged_file(store, row)
    await session.flush()
    return StagingCleanupReport(actions=tuple(actions))


def _remove_staged_file(store: NfsArtifactStore, row: ChannelUpload) -> None:
    """删暂存文件（行已软删）：缺失是幂等清理的正常情形；其余 IO 失败留可观测告警。"""
    try:
        store.resolve(row.storage_key).unlink(missing_ok=True)
    except ValueError:
        logger.warning("staged_upload_key_out_of_root", extra={"upload_id": str(row.id)})
    except OSError as exc:
        logger.warning(
            "staged_upload_unlink_failed",
            extra={"upload_id": str(row.id), "error_type": type(exc).__name__},
        )


async def run_retention_sweep(
    session: AsyncSession,
    *,
    policy: RetentionPolicy,
    artifact_root: str | Path,
    batch_size: int = RETENTION_BATCH_SIZE,
    grace_seconds: float = STAGING_GRACE_SECONDS,
    tenant_id: str | None = None,
) -> RetentionReport:
    """一次完整的保留清理扫描（独立维护循环的调用点；调用方负责提交）。

    四类清理各自有界、互不阻塞；顺序固定为"流增量 → 可见历史 → 通知 → 暂存"（先回收可重建的
    增量，再动可见历史与收件箱，最后清盘上字节），失败即抛出、由循环按既有口径记录并重试。
    `tenant_id=None` 是本部署库的全部租户（运维口径）；按租户维护/验收传具体租户。
    """
    stream_replay_removed = await cleanup_stream_replay(
        session, policy=policy, batch_size=batch_size, tenant_id=tenant_id
    )
    history_removed = await cleanup_visible_history(
        session, policy=policy, batch_size=batch_size, tenant_id=tenant_id
    )
    notifications_removed = await cleanup_notifications(
        session, policy=policy, batch_size=batch_size, tenant_id=tenant_id
    )
    staging = await cleanup_staging_uploads(
        session,
        artifact_root=artifact_root,
        batch_size=batch_size,
        grace_seconds=grace_seconds,
        tenant_id=tenant_id,
    )
    report = RetentionReport(
        stream_replay_removed=stream_replay_removed,
        history_removed=history_removed,
        notifications_removed=notifications_removed,
        staging=staging,
    )
    # 四类回收量各自记在 **amount**（条数进 label 会裂出无界时间序列）；节拍结局记 ok。
    record_counter(WEBCHAT_RETENTION_REMOVED_METRIC, stream_replay_removed, {"kind": "stream_replay"})
    record_counter(WEBCHAT_RETENTION_REMOVED_METRIC, history_removed, {"kind": "history"})
    record_counter(WEBCHAT_RETENTION_REMOVED_METRIC, notifications_removed, {"kind": "notifications"})
    record_counter(WEBCHAT_RETENTION_REMOVED_METRIC, staging.removed, {"kind": "staging_upload"})
    record_outcome(WEBCHAT_RETENTION_METRIC, "ok")
    logger.info("webchat_retention_sweep", extra={"summary": report.summary()})
    return report
