"""B-269 / B-07：历史、流增量、通知与制品清理保护（design §3.3/§3.6/§3.7；TASK-069）。

真实边界（不 mock 存储、清理查询、投递租约或文件系统）：

- **真实 PostgreSQL**：隔离租户的 `runtime.conversation`/`run_record`/`canonical_event`/
  `channel_upload`/`channel_notification`/`memory_consolidation_job`/`artifact` 与 `task.task_execution`
  都是真实行；canonical `seq` 由真实 `EventWriter` 在会话行锁内分配（老化只拨 `create_time`）。
- **真实共享文件系统**：隔离 `ARTIFACT_ROOT` 下写真实暂存字节、用真实硬链接发布 Run 制品、
  清理真的删盘上文件；`os.utime` 把 mtime 拨到过去，模拟"文件早已写完"。
- **真实清理服务**：Runtime 侧保留清理（`channel_retention`）与 Console 既有
  `ArtifactCleanupService` 都是被测对象本体；后者用真实 `task.task_execution` 行表达"仍在跑的 job"。
- **真实 API 边界**：保留边界经真实 Runtime 路由（`GET /v1/runs/{id}/events`）与真实
  `EventTailHub.check_cursor` 观测；"已清理字节的读取一律 404" 经真实内部产物解析路由（HTTP）。
- **真实投递协议**：通知的 `DELIVERED`/`UNKNOWN` 由真实 `ChannelDeliveryService` 的 reserve/ack
  产生（不是手写状态字段）。

关键断言（契约逐项）：

1. **保留期按清理边界取设置**：同一批旧行在 48 小时保留期下不动，下一轮清理边界改成 1 小时即被
   回收；`history_retention_days=0` 是"不按时间自动删除可见历史"的显式语义；非法设置显式失败。
2. **不删唯一摘要 / 活跃 Run-job 引用**：流增量清理保留活跃 Run 的重放数据与在飞 job 输入边界内
   的事件；可见历史清理只回收**被最新摘要覆盖**的旧消息，摘要自身与摘要之后的"重建仍必需"事件
   一律保留。
3. **游标过期明确、feed 水位不重置**：重放数据被回收后，过期游标显式 `CURSOR_EXPIRED` 并给出
   最早可用水位（不静默夹取）；会话持久水位（checkpoint）不因清理回退；通知清理不动 feed 行。
4. **仍待投递回执**：只有终态回执（`DELIVERED`/`NOT_REQUIRED`）且超过保留期的通知才被隐藏；
   `PENDING`/`DELIVERING`/`UNKNOWN` 与重放保留期内的回执一律保留，隐藏行仍保唯一回执。
5. **暂存与 Run 字节独立**：仍被在飞 Run 引用的暂存登记不删；宽限期内的暂存文件不删；清理暂存
   不删已受理 Run 自己的制品字节（硬链接独立）。
6. **Token 过期不删对话**：过期的 CHAT Token/会话不构成删除对话/可见历史的理由——完整清理扫描后
   会话与水位原地不动。
7. **保留边界可观测**：清理报告逐条可对账（动作 + 汇总），已清理字节的读取一律 404。
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_runtime.application.channel_delivery import (
    ChannelDeliveryService,
    DeliveryPolicy,
)
from muad_agent_runtime.application.channel_notification_queries import (
    ChannelNotificationQueryService,
    NotificationScope,
)
from muad_agent_runtime.application.channel_notifications import (
    DELIVERY_NOT_REQUIRED,
    DELIVERY_PENDING,
    KIND_RUN_COMPLETED,
    write_run_notification,
)
from muad_agent_runtime.application.channel_retention import (
    ACTION_STAGING_ACTIVE_REFERENCE,
    ACTION_STAGING_GRACE,
    ACTION_STAGING_REMOVED,
    RETENTION_BATCH_SIZE,
    RetentionPolicy,
    cleanup_notifications,
    cleanup_staging_uploads,
    cleanup_stream_replay,
    cleanup_visible_history,
    retention_policy_from_settings,
    run_retention_sweep,
)
from muad_agent_runtime.application.channel_uploads import (
    ChannelUploadLimits,
    publish_run_artifact,
    register_staged_upload,
    run_artifact_storage_key,
)
from muad_agent_runtime.application.context_events import CONTEXT_SUMMARY_EVENT, record_summary
from muad_agent_runtime.application.event_tail_hub import EventTailHub
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    CanonicalEvent,
    ChannelNotification,
    ChannelNotificationFeed,
    ChannelUpload,
    Conversation,
    MemoryConsolidationJob,
    RunRecord,
)
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from muad_console_platform.application.artifact_cleanup_service import (
    ACTION_REMOVED,
    ACTION_SKIPPED,
    ArtifactCleanupService,
)
from muad_console_platform.infrastructure.db import get_session_factory as console_session_factory
from muad_contracts.enums import RunStatus
from muad_contracts.platform_settings import PlatformSettingsError

from agent_runtime.conftest import CLEANUP_ORDER, TenantContext

pytestmark = pytest.mark.integration

#: 本套件的入口（WEBCHAT 稳定入口账号；真实核属维度之一，不建 Console 行）。
ENTRY = uuid.uuid4()
#: 老化偏移：明显越过 1 小时/30 天保留期，避免时钟抖动把边界用例变成 flaky。
OLD_HOURS = timedelta(hours=30)
OLD_DAYS = timedelta(days=40)
#: 保留期与宽限期口径（本用例显式声明，不从实现里抄默认值）。
STREAM_HOURS = 1
NOTIFICATION_DAYS = 30
STAGING_GRACE_SECONDS = 3600.0
#: 清理顺序：制品/暂存登记先于 run_record/conversation（同 Owner 物理 FK）。
PURGE_ORDER = ("artifact", "channel_upload", *CLEANUP_ORDER)
#: Console 侧的 control 行（过期 CHAT 会话用例自己播种，自己清理）。
CONTROL_PURGE_ORDER = ("chat_session", "chat_access_token", "platform_user")
PAYLOAD = b"retention staged bytes\n"


@dataclass(frozen=True, slots=True)
class RetentionContext:
    """隔离租户 + 隔离 artifact root（真实文件；不触碰共享 dev 目录）。"""

    tenant: TenantContext
    root: Path


def _policy(*, hours: int = STREAM_HOURS, days: int = NOTIFICATION_DAYS, history: int = 0) -> RetentionPolicy:
    return RetentionPolicy(
        stream_replay_retention_hours=hours,
        notification_retention_days=days,
        history_retention_days=history,
    )


@pytest.fixture(autouse=True)
def fresh_console_engine_caches() -> None:
    """Console 侧 engine 缓存也按本进程重建（与套件 conftest 对 runtime 的口径一致）。"""
    from muad_console_platform.infrastructure import db as console_db

    console_db.get_engine.cache_clear()
    console_db.get_session_factory.cache_clear()


@pytest.fixture
def retention_root(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
async def retention_ctx(tenant: TenantContext, retention_root: Path) -> AsyncIterator[RetentionContext]:
    try:
        yield RetentionContext(tenant=tenant, root=retention_root)
    finally:
        async with get_session_factory()() as session:
            for table in CONTROL_PURGE_ORDER:
                await session.execute(
                    sa.text(f"DELETE FROM control.{table} WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant.tenant_id},
                )
            await session.execute(
                sa.text("DELETE FROM task.task_execution WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant.tenant_id},
            )
            await session.commit()


# --------------------------------------------------------------------------------------
# 真实行与真实文件的播种（老化只拨时间列/文件 mtime，不改事实形状）
# --------------------------------------------------------------------------------------


async def _seed_conversation(
    session: Any, tenant: TenantContext, *, user_id: uuid.UUID | None = None
) -> Conversation:
    row = Conversation(
        tenant_id=tenant.tenant_id,
        user_id=user_id or tenant.platform_user_id,
        agent_id=tenant.agent_id,
        title="保留清理用例",
        status="ACTIVE",
        last_seq=0,
        channel_account_id=ENTRY,
        last_message_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


async def _seed_run(
    session: Any,
    tenant: TenantContext,
    conversation: Conversation,
    *,
    status: RunStatus = RunStatus.COMPLETED,
    user_id: uuid.UUID | None = None,
) -> RunRecord:
    row = RunRecord(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation.id,
        user_id=user_id or tenant.platform_user_id,
        agent_id=tenant.agent_id,
        snapshot_id=uuid.uuid4(),
        status=status,
        input_text="保留清理用例输入",
        trace_id=uuid.uuid4().hex,
    )
    session.add(row)
    await session.flush()
    return row


async def _append_event(
    session: Any,
    tenant: TenantContext,
    conversation: Conversation,
    run: RunRecord,
    *,
    event_type: str,
    stream_type: str | None = None,
    payload: dict[str, Any] | None = None,
    age: timedelta = OLD_HOURS,
) -> int:
    """真实 `EventWriter` 分配 seq，随后把 `create_time` 拨到过去（老化，不改事实）。"""
    seq = await EventWriter(session).append(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation.id,
        run_id=run.id,
        event_type=event_type,
        stream_type=stream_type,
        payload=payload if payload is not None else {"text": f"事件 {event_type}"},
    )
    await _age_event(session, conversation_id=conversation.id, seq=seq, age=age)
    return seq


async def _age_event(session: Any, *, conversation_id: uuid.UUID, seq: int, age: timedelta) -> None:
    await session.execute(
        sa.update(CanonicalEvent)
        .where(CanonicalEvent.conversation_id == conversation_id, CanonicalEvent.seq == seq)
        .values(create_time=datetime.now(UTC) - age)
    )


async def _event_rows(session: Any, conversation: Conversation) -> dict[int, tuple[str, str | None, bool]]:
    rows = (
        await session.execute(
            sa.select(
                CanonicalEvent.seq,
                CanonicalEvent.event_type,
                CanonicalEvent.stream_type,
                CanonicalEvent.is_deleted,
            )
            .where(CanonicalEvent.conversation_id == conversation.id)
            .order_by(CanonicalEvent.seq)
        )
    ).all()
    return {int(row.seq): (str(row.event_type), row.stream_type, bool(row.is_deleted)) for row in rows}


async def _event_deleted(session: Any, *, conversation_id: uuid.UUID, seq: int) -> bool:
    return bool(
        await session.scalar(
            sa.select(CanonicalEvent.is_deleted).where(
                CanonicalEvent.conversation_id == conversation_id, CanonicalEvent.seq == seq
            )
        )
    )


async def _seed_notification(
    session: Any,
    tenant: TenantContext,
    conversation: Conversation,
    run: RunRecord,
    *,
    delivery_key: str,
    delivery_state: str = DELIVERY_NOT_REQUIRED,
    age: timedelta = OLD_DAYS,
) -> ChannelNotification:
    receipt = await write_run_notification(
        session,
        run=run,
        kind=KIND_RUN_COMPLETED,
        delivery_key=delivery_key,
        delivery_state=delivery_state,
    )
    assert receipt is not None, "网页入口会话必须有入箱行"
    await session.execute(
        sa.update(ChannelNotification)
        .where(ChannelNotification.id == receipt.id)
        .values(create_time=datetime.now(UTC) - age)
    )
    row = await session.get(ChannelNotification, receipt.id)
    assert row is not None
    return row


async def _mark_delivered(tenant_id: str, delivery_key: str, *, outcome: str) -> None:
    """真实投递协议：reserve → ack（`DELIVERED`/`UNKNOWN` 都保留首次回执）。"""
    service = ChannelDeliveryService(
        get_session_factory, DeliveryPolicy(backoff_base_sec=1, replay_retention_days=30)
    )
    attempt_id = uuid.uuid4()
    lease = await service.reserve(tenant_id=tenant_id, delivery_key=delivery_key, attempt_id=attempt_id)
    await service.ack(
        tenant_id=tenant_id,
        notification_id=lease.notification_id,
        delivery_key=delivery_key,
        attempt_id=attempt_id,
        epoch=lease.lease_epoch,
        outcome=outcome,  # type: ignore[arg-type]
        result={"delivered": outcome == "DELIVERED"},
    )


async def _seed_staged_upload(
    session: Any,
    ctx: RetentionContext,
    tenant: TenantContext,
    *,
    age: timedelta,
    file_age: timedelta | None,
) -> ChannelUpload:
    """真实登记 + 真实暂存字节：行按 `age` 过期，文件 mtime 按 `file_age` 老化。"""
    storage_key = f"staging/{uuid.uuid4().hex}.txt"
    row = await register_staged_upload(
        session,
        tenant_id=tenant.tenant_id,
        user_id=tenant.platform_user_id,
        entry_id=ENTRY,
        storage_key=storage_key,
        kind="DOCUMENT",
        media_type="text/plain",
        checksum="sha256:" + hashlib.sha256(PAYLOAD).hexdigest(),
        filename="staged.txt",
        size=len(PAYLOAD),
        limits=ChannelUploadLimits(
            upload_max_bytes=1024 * 1024, upload_max_per_message=5, staging_ttl_sec=3600
        ),
    )
    # 老化必须同时满足库级 CHECK `expires_at > create_time`：先把登记时间拨得更早，再让 TTL 过期。
    moment = datetime.now(UTC)
    await session.execute(
        sa.update(ChannelUpload)
        .where(ChannelUpload.id == row.id)
        .values(create_time=moment - age - timedelta(hours=1), expires_at=moment - age)
    )
    path = NfsArtifactStore(ctx.root).resolve(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PAYLOAD)
    if file_age is not None:
        stamp = (datetime.now(UTC) - file_age).timestamp()
        os.utime(path, (stamp, stamp))
    await session.flush()
    return row


async def _publish_run_artifact(
    session: Any, ctx: RetentionContext, tenant: TenantContext, upload: ChannelUpload, run: RunRecord
) -> Artifact:
    """真实发布：硬链接到 Run 自己的键 + 落 artifact 行（元信息带 upload_id 引用）。"""
    storage_key = run_artifact_storage_key(run_id=run.id, upload_id=upload.id)
    publish_run_artifact(store=NfsArtifactStore(ctx.root), upload=upload, storage_key=storage_key)
    row = Artifact(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        run_id=run.id,
        task_id=None,
        conversation_id=run.conversation_id,
        artifact_type="INBOUND_DOCUMENT",
        storage_key=storage_key,
        media_type=upload.media_type,
        size=upload.size,
        checksum=upload.checksum,
        metadata_json={"kind": upload.kind, "upload_id": str(upload.id)},
    )
    session.add(row)
    await session.flush()
    return row


async def _seed_artifact_row(
    session: Any,
    tenant: TenantContext,
    conversation: Conversation,
    *,
    run: RunRecord | None,
    task_id: uuid.UUID | None,
    storage_key: str,
    age: timedelta,
) -> Artifact:
    row = Artifact(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        run_id=None if run is None else run.id,
        task_id=task_id,
        conversation_id=conversation.id,
        artifact_type="INBOUND_DOCUMENT",
        storage_key=storage_key,
        media_type="text/plain",
        size=len(PAYLOAD),
        checksum="sha256:" + hashlib.sha256(PAYLOAD).hexdigest(),
        metadata_json={},
        create_time=datetime.now(UTC) - age,
        update_time=datetime.now(UTC) - age,
    )
    session.add(row)
    await session.flush()
    return row


def _write_file(root: Path, storage_key: str, *, file_age: timedelta) -> Path:
    path = NfsArtifactStore(root).resolve(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PAYLOAD)
    stamp = (datetime.now(UTC) - file_age).timestamp()
    os.utime(path, (stamp, stamp))
    return path


async def _resolve_download(
    client: AsyncClient, tenant: TenantContext, artifact_id: uuid.UUID
) -> Any:
    return await client.get(
        f"/internal/webchat/artifacts/{artifact_id}",
        params={"user_id": str(tenant.platform_user_id)},
        headers={
            "X-Tenant-Id": tenant.tenant_id,
            "X-Internal-Service": SharedSettings().internal_service_token or "",
        },
    )


# --------------------------------------------------------------------------------------
# B-269 ①：保留期按清理边界取设置
# --------------------------------------------------------------------------------------


async def test_b269_retention_periods_come_from_settings_at_each_cleanup_boundary(
    retention_ctx: RetentionContext,
) -> None:
    """保留期按清理边界取设置：默认来自 contracts schema、显式设置生效、坏值显式失败。

    真实证据：同一行 30 小时前的流增量，在 48 小时保留期的清理边界下不动；下一轮清理边界把设置
    改成 1 小时（模拟"设置在此后变更"）即被回收——清理读的是**当次边界**的设置，不随 Run 冻结。
    """
    tenant = retention_ctx.tenant
    # 缺省分组回落 schema 默认（唯一来源在 contracts，不在本模块硬编码）
    default = retention_policy_from_settings(None)
    assert default.stream_replay_retention_hours == 24
    assert default.notification_retention_days == 30
    assert default.history_retention_days == 0, "0 = 不按时间自动删除可见聊天历史"
    # 显式设置组生效
    configured = retention_policy_from_settings(
        {
            "webchat": {
                "stream_replay_retention_hours": 48,
                "notification_retention_days": 7,
                "history_retention_days": 3,
            }
        }
    )
    assert configured == _policy(hours=48, days=7, history=3)
    # 非法设置显式失败（不静默回落默认）
    with pytest.raises(PlatformSettingsError):
        retention_policy_from_settings({"webchat": {"stream_replay_retention_hours": 0}})
    with pytest.raises(PlatformSettingsError):
        retention_policy_from_settings({"webchat": {"history_retention_days": 4000}})

    async with get_session_factory()() as session:
        conversation = await _seed_conversation(session, tenant)
        run = await _seed_run(session, tenant, conversation)
        seq = await _append_event(
            session,
            tenant,
            conversation,
            run,
            event_type="RUN_STAGE",
            stream_type="run.stage",
            age=OLD_HOURS,
        )
        await session.commit()

    async with get_session_factory()() as session:
        removed = await cleanup_stream_replay(
            session,
            policy=retention_policy_from_settings(
                {"webchat": {"stream_replay_retention_hours": 48}}
            ),
            tenant_id=tenant.tenant_id,
        )
        await session.commit()
    assert removed == 0, "48 小时保留期下 30 小时前的行仍在保留期内"
    async with get_session_factory()() as session:
        assert not await _event_deleted(session, conversation_id=conversation.id, seq=seq)

    async with get_session_factory()() as session:
        removed = await cleanup_stream_replay(
            session,
            policy=retention_policy_from_settings(
                {"webchat": {"stream_replay_retention_hours": 1}}
            ),
            tenant_id=tenant.tenant_id,
        )
        await session.commit()
    assert removed == 1, "清理边界重新取设置后，同一行必须按新保留期被回收"
    async with get_session_factory()() as session:
        assert await _event_deleted(session, conversation_id=conversation.id, seq=seq)


# --------------------------------------------------------------------------------------
# B-269 ② / B-07：不删唯一摘要、活跃 Run-job 引用
# --------------------------------------------------------------------------------------


async def test_b269_stream_replay_cleanup_keeps_active_runs_and_inflight_job_inputs(
    retention_ctx: RetentionContext,
) -> None:
    """流增量清理：活跃 Run 的重放数据与在飞 job 的输入边界内事件一律保留。

    - C1：终态 Run 的 4 条旧流增量 + 一份旧摘要；PENDING 记忆 job 的 `through_seq=2` ⇒ 前两条是
      它的输入（仍被 job 引用），后两条可回收；摘要不是流增量（`stream_type IS NULL`），永不候选。
    - C2：**活跃 Run** 的旧流增量 ⇒ 无安全快照/终态可接管之前不得先淘汰重放数据。
    """
    tenant = retention_ctx.tenant
    async with get_session_factory()() as session:
        c1 = await _seed_conversation(session, tenant)
        terminal = await _seed_run(session, tenant, c1, status=RunStatus.COMPLETED)
        seqs = []
        for index in range(4):
            seqs.append(
                await _append_event(
                    session,
                    tenant,
                    c1,
                    terminal,
                    event_type="ASSISTANT_DELTA",
                    stream_type="message.delta",
                    payload={"delta": f"第{index}段"},
                )
            )
        summary_seq = await record_summary(
            EventWriter(session),
            tenant_id=tenant.tenant_id,
            conversation_id=c1.id,
            run_id=terminal.id,
            covers_up_to_seq=seqs[-1],
            summary={"topic": "保留清理"},
            transcript_artifact_id=None,
            bytes_before=100,
            bytes_after=10,
        )
        await _age_event(session, conversation_id=c1.id, seq=summary_seq, age=OLD_HOURS)
        session.add(
            MemoryConsolidationJob(
                tenant_id=tenant.tenant_id,
                run_id=terminal.id,
                conversation_id=c1.id,
                user_id=terminal.user_id,
                snapshot_id=terminal.snapshot_id or uuid.uuid4(),
                through_seq=seqs[1],
                status="PENDING",
                enqueued_scope_revision=0,
            )
        )
        c2 = await _seed_conversation(session, tenant)
        active = await _seed_run(session, tenant, c2, status=RunStatus.RUNNING)
        active_seq = await _append_event(
            session,
            tenant,
            c2,
            active,
            event_type="ASSISTANT_DELTA",
            stream_type="message.delta",
            payload={"delta": "在飞正文"},
        )
        await session.commit()

    async with get_session_factory()() as session:
        removed = await cleanup_stream_replay(
            session, policy=_policy(), tenant_id=tenant.tenant_id
        )
        await session.commit()

    assert removed == 2, "只有 job 输入边界之外的旧流增量可回收"
    async with get_session_factory()() as session:
        rows = await _event_rows(session, c1)
        assert rows[seqs[0]][2] is False, "在飞 job 输入边界内的事件仍被 job 引用"
        assert rows[seqs[1]][2] is False
        assert rows[seqs[2]][2] is True
        assert rows[seqs[3]][2] is True
        assert rows[summary_seq][0] == CONTEXT_SUMMARY_EVENT
        assert rows[summary_seq][2] is False, "模型唯一摘要永远不是流增量清理的候选"
        active_rows = await _event_rows(session, c2)
        assert active_rows[active_seq][2] is False, "活跃 Run 的重放数据必须先保留"


async def test_b269_history_cleanup_never_deletes_the_only_context_source(
    retention_ctx: RetentionContext,
) -> None:
    """可见历史清理：只回收被最新摘要覆盖的旧消息；摘要、摘要之后的必需事件、活跃引用一律保留。

    真实证据：`history_retention_days=0` 时**一行都不删**（显式语义）；设为 1 天后仍不动
    无摘要会话的可见消息（那是唯一上下文来源）、摘要之后的消息（重建仍必需）、活跃 Run 的消息
    与在飞 job 输入边界内的消息。
    """
    tenant = retention_ctx.tenant
    async with get_session_factory()() as session:
        covered = await _seed_conversation(session, tenant)
        covered_run = await _seed_run(session, tenant, covered)
        covered_user = await _append_event(
            session,
            tenant,
            covered,
            covered_run,
            event_type="USER_MESSAGE",
            payload={"text": "被摘要覆盖的提问"},
        )
        covered_assistant = await _append_event(
            session,
            tenant,
            covered,
            covered_run,
            event_type="ASSISTANT_MESSAGE",
            payload={"text": "被摘要覆盖的回答"},
        )
        summary_seq = await record_summary(
            EventWriter(session),
            tenant_id=tenant.tenant_id,
            conversation_id=covered.id,
            run_id=covered_run.id,
            covers_up_to_seq=covered_assistant,
            summary={"topic": "覆盖前两条"},
            transcript_artifact_id=None,
            bytes_before=200,
            bytes_after=20,
        )
        await _age_event(session, conversation_id=covered.id, seq=summary_seq, age=OLD_DAYS)
        tail_user = await _append_event(
            session,
            tenant,
            covered,
            covered_run,
            event_type="USER_MESSAGE",
            payload={"text": "摘要之后的追问"},
        )
        tail_assistant = await _append_event(
            session,
            tenant,
            covered,
            covered_run,
            event_type="ASSISTANT_MESSAGE",
            payload={"text": "摘要之后的回答"},
        )
        await _age_event(session, conversation_id=covered.id, seq=tail_user, age=OLD_DAYS)
        await _age_event(session, conversation_id=covered.id, seq=tail_assistant, age=OLD_DAYS)

        # 无摘要的会话：可见消息就是唯一上下文来源
        plain = await _seed_conversation(session, tenant)
        plain_run = await _seed_run(session, tenant, plain)
        plain_user = await _append_event(
            session, tenant, plain, plain_run, event_type="USER_MESSAGE", payload={"text": "没有摘要的提问"}
        )
        await _age_event(session, conversation_id=plain.id, seq=plain_user, age=OLD_DAYS)

        # 活跃 Run 的消息（即使被摘要覆盖）仍被 Run 引用
        running = await _seed_conversation(session, tenant)
        running_run = await _seed_run(session, tenant, running, status=RunStatus.RUNNING)
        running_summary = await record_summary(
            EventWriter(session),
            tenant_id=tenant.tenant_id,
            conversation_id=running.id,
            run_id=running_run.id,
            covers_up_to_seq=0,
            summary={"topic": "空覆盖"},
            transcript_artifact_id=None,
            bytes_before=0,
            bytes_after=0,
        )
        running_user = await _append_event(
            session, tenant, running, running_run, event_type="USER_MESSAGE", payload={"text": "在飞提问"}
        )
        await _age_event(session, conversation_id=running.id, seq=running_user, age=OLD_DAYS)
        # 该消息同时被在飞 job 引用（through_seq 覆盖它）——两道保护都在
        session.add(
            MemoryConsolidationJob(
                tenant_id=tenant.tenant_id,
                run_id=running_run.id,
                conversation_id=running.id,
                user_id=running_run.user_id,
                snapshot_id=running_run.snapshot_id or uuid.uuid4(),
                through_seq=running_user,
                status="RUNNING",
                enqueued_scope_revision=0,
            )
        )
        # 该会话的摘要覆盖该消息，使"覆盖"不再构成删除理由，只有保护在起作用
        await session.execute(
            sa.update(CanonicalEvent)
            .where(CanonicalEvent.conversation_id == running.id, CanonicalEvent.seq == running_summary)
            .values(payload_json={"covers_up_to_seq": running_user, "summary": {}})
        )
        await session.commit()

    async with get_session_factory()() as session:
        kept = await cleanup_visible_history(
            session, policy=_policy(history=0), tenant_id=tenant.tenant_id
        )
        await session.commit()
    assert kept == 0, "history_retention_days=0 表示不按时间自动删除可见历史"

    async with get_session_factory()() as session:
        removed = await cleanup_visible_history(
            session, policy=_policy(history=1), tenant_id=tenant.tenant_id
        )
        await session.commit()

    assert removed == 2, "只回收被最新摘要覆盖的两条旧消息"
    async with get_session_factory()() as session:
        rows = await _event_rows(session, covered)
        assert rows[covered_user][2] is True
        assert rows[covered_assistant][2] is True
        assert rows[summary_seq][2] is False, "唯一摘要不得被删"
        assert rows[tail_user][2] is False, "摘要之后的事件仍是重建必需"
        assert rows[tail_assistant][2] is False
        plain_rows = await _event_rows(session, plain)
        assert plain_rows[plain_user][2] is False, "无摘要时可见消息是唯一上下文来源"
        running_rows = await _event_rows(session, running)
        assert running_rows[running_user][2] is False, "活跃 Run/job 引用的事件不得被删"


# --------------------------------------------------------------------------------------
# B-269 ③ / B-07：游标过期明确、feed 水位不重置、保留边界可观测
# --------------------------------------------------------------------------------------


async def test_b269_expired_cursor_is_explicit_and_the_boundary_is_observable(
    retention_ctx: RetentionContext, client: AsyncClient
) -> None:
    """重放数据被回收后：过期游标在真实 API 上显式 `CURSOR_EXPIRED` 并给出最早可用水位。

    "保留边界在 API 中明确可观测" 的落点：真实 `GET /v1/runs/{id}/events?after_seq=0` 返回
    410 + `earliest_seq`（不是静默夹取成空流）；同一 Run 的持久水位不因清理回退。
    """
    tenant = retention_ctx.tenant
    async with get_session_factory()() as session:
        conversation = await _seed_conversation(session, tenant)
        run = await _seed_run(session, tenant, conversation)
        stale = [
            await _append_event(
                session,
                tenant,
                conversation,
                run,
                event_type="ASSISTANT_DELTA",
                stream_type="message.delta",
                payload={"delta": f"旧增量{index}"},
            )
            for index in range(6)
        ]
        fresh = await _append_event(
            session,
            tenant,
            conversation,
            run,
            event_type="RUN_STAGE",
            stream_type="run.stage",
            age=timedelta(minutes=1),
        )
        await session.commit()

    hub = EventTailHub(fallback_poll_sec=30.0)
    try:
        # 清理前：游标可用（不误判过期）
        async with get_session_factory()() as session:
            through_before = await hub.check_cursor(
                tenant_id=tenant.tenant_id, conversation_id=conversation.id, after_seq=0
            )
        assert through_before == fresh

        async with get_session_factory()() as session:
            removed = await cleanup_stream_replay(
                session, policy=_policy(), tenant_id=tenant.tenant_id
            )
            await session.commit()
        assert removed == len(stale), "保留期外的流增量全部回收，保留期内的一条不动"

        # 真实 HTTP 边界：过期游标 410 + 最早可用水位（不静默夹取）
        response = await client.get(
            f"/v1/runs/{run.id}/events",
            params={"after_seq": 0},
            headers={"X-Tenant-Id": tenant.tenant_id},
        )
        assert response.status_code == 410, response.text
        payload = response.json()
        assert payload["code"] == "CURSOR_EXPIRED"
        assert payload["data"]["earliest_seq"] == fresh, "保留边界必须明确可观测"

        # 边界本身是可用游标：不因清理被"多夹一格"
        async with get_session_factory()() as session:
            through_after = await hub.check_cursor(
                tenant_id=tenant.tenant_id,
                conversation_id=conversation.id,
                after_seq=fresh - 1,
            )
            assert through_after == fresh
            checkpoint = await hub.checkpoint(
                tenant_id=tenant.tenant_id, conversation_id=conversation.id
            )
        assert checkpoint == fresh, "持久水位（checkpoint）不因清理回退、不重分配 seq"
        async with get_session_factory()() as session:
            with pytest.raises(AppError) as expired:
                await hub.check_cursor(
                    tenant_id=tenant.tenant_id,
                    conversation_id=conversation.id,
                    after_seq=0,
                )
        assert expired.value.code == ErrorCode.CURSOR_EXPIRED
        assert expired.value.data["earliest_seq"] == fresh
        assert hub.stats().cursor_expired >= 1, "游标过期必须是可观测事实"
    finally:
        await hub.aclose()


async def test_b269_notification_retention_keeps_pending_receipts_and_feed_watermark(
    retention_ctx: RetentionContext,
) -> None:
    """通知保留：只隐藏终态回执且超期的行；待投递/待核实/重放期内的回执与 feed 水位不动。

    真实证据：`DELIVERED`/`UNKNOWN` 由真实 reserve/ack 产生；隐藏行仍保唯一回执（同键 reserve
    命中回执而不是新投递）；真实收件箱读取只少掉被隐藏的行；feed `last_seq` 不回退。
    """
    tenant = retention_ctx.tenant
    scope = NotificationScope(tenant_id=tenant.tenant_id, user_id=tenant.platform_user_id)
    keys = {
        name: f"run:{uuid.uuid4()}:terminal"
        for name in ("delivered", "inbox", "pending", "unknown", "recent")
    }
    async with get_session_factory()() as session:
        conversation = await _seed_conversation(session, tenant)
        run = await _seed_run(session, tenant, conversation)
        delivered = await _seed_notification(
            session,
            tenant,
            conversation,
            run,
            delivery_key=keys["delivered"],
            delivery_state=DELIVERY_PENDING,
        )
        inbox = await _seed_notification(
            session,
            tenant,
            conversation,
            run,
            delivery_key=keys["inbox"],
            delivery_state=DELIVERY_NOT_REQUIRED,
        )
        pending = await _seed_notification(
            session, tenant, conversation, run, delivery_key=keys["pending"], delivery_state=DELIVERY_PENDING
        )
        unknown = await _seed_notification(
            session, tenant, conversation, run, delivery_key=keys["unknown"], delivery_state=DELIVERY_PENDING
        )
        recent = await _seed_notification(
            session, tenant, conversation, run, delivery_key=keys["recent"], delivery_state=DELIVERY_PENDING,
            age=timedelta(hours=1),
        )
        await session.commit()
    await _mark_delivered(tenant.tenant_id, keys["delivered"], outcome="DELIVERED")
    await _mark_delivered(tenant.tenant_id, keys["unknown"], outcome="UNKNOWN")
    await _mark_delivered(tenant.tenant_id, keys["recent"], outcome="DELIVERED")

    async with get_session_factory()() as session:
        feed_before = await session.scalar(
            sa.select(ChannelNotificationFeed.last_seq).where(
                ChannelNotificationFeed.tenant_id == tenant.tenant_id,
                ChannelNotificationFeed.user_id == tenant.platform_user_id,
            )
        )
        removed = await cleanup_notifications(
            session, policy=_policy(), tenant_id=tenant.tenant_id
        )
        await session.commit()
    assert feed_before == 5

    assert removed == 2, "只有「超期 + 终态回执」的两行可隐藏"
    async with get_session_factory()() as session:
        states = {
            row.id: bool(row.is_deleted)
            for row in (
                await session.scalars(
                    sa.select(ChannelNotification).where(
                        ChannelNotification.tenant_id == tenant.tenant_id
                    )
                )
            ).all()
        }
        assert states[delivered.id] is True
        assert states[inbox.id] is True
        assert states[pending.id] is False, "仍待投递（PENDING）的通知不得被隐藏"
        assert states[unknown.id] is False, "仍待核实（UNKNOWN）的回执不得被隐藏"
        assert states[recent.id] is False, "重放保留期内的回执不得被隐藏"
        feed_after = await session.scalar(
            sa.select(ChannelNotificationFeed.last_seq).where(
                ChannelNotificationFeed.tenant_id == tenant.tenant_id,
                ChannelNotificationFeed.user_id == tenant.platform_user_id,
            )
        )
        assert feed_after == feed_before, "feed 水位不因清理重置"

        page = await ChannelNotificationQueryService(session).list_notifications(
            scope, after_seq=0, page=1, page_size=10
        )
        assert page.through_seq == feed_before, "feed 水位仍是权威提交序号"
        assert {item.id for item in page.items} == {pending.id, unknown.id, recent.id}
        with pytest.raises(AppError) as ahead:
            await ChannelNotificationQueryService(session).list_notifications(
                scope, after_seq=feed_before + 1, page=1, page_size=10
            )
        assert ahead.value.code == ErrorCode.CURSOR_AHEAD

    # 隐藏行仍保唯一回执：同键 reserve 命中既有回执，不当成新投递
    service = ChannelDeliveryService(
        get_session_factory, DeliveryPolicy(backoff_base_sec=1, replay_retention_days=30)
    )
    replay = await service.reserve(
        tenant_id=tenant.tenant_id, delivery_key=keys["delivered"], attempt_id=uuid.uuid4()
    )
    assert replay.reused is True and replay.state == "DELIVERED"
    unknown_replay = await service.reserve(
        tenant_id=tenant.tenant_id, delivery_key=keys["unknown"], attempt_id=uuid.uuid4()
    )
    assert unknown_replay.reused is True and unknown_replay.state == "UNKNOWN"


# --------------------------------------------------------------------------------------
# B-269 ④ / B-07：暂存与 Run 字节独立、活跃引用与宽限保护
# --------------------------------------------------------------------------------------


async def test_b269_staging_cleanup_protects_active_references_grace_and_run_bytes(
    retention_ctx: RetentionContext,
) -> None:
    """暂存清理：仍被在飞 Run 引用或宽限期内的绝不删；清理暂存不删已受理 Run 自己的字节。

    真实文件证据：三份过期的暂存登记都有真实字节；被活跃 Run 引用的一份与文件很新的一份原地不动，
    第三份被清理（行软删 + 暂存文件删除）——但它所属 Run 的制品键仍可读（硬链接独立）。
    """
    ctx = retention_ctx
    tenant = ctx.tenant
    store = NfsArtifactStore(ctx.root)
    async with get_session_factory()() as session:
        active_conv = await _seed_conversation(session, tenant)
        active_run = await _seed_run(session, tenant, active_conv, status=RunStatus.RUNNING)
        active_upload = await _seed_staged_upload(
            session, ctx, tenant, age=timedelta(hours=2), file_age=OLD_DAYS
        )
        await _publish_run_artifact(session, ctx, tenant, active_upload, active_run)

        grace_conv = await _seed_conversation(session, tenant)
        grace_run = await _seed_run(session, tenant, grace_conv, status=RunStatus.COMPLETED)
        grace_upload = await _seed_staged_upload(
            session, ctx, tenant, age=timedelta(hours=2), file_age=timedelta(seconds=30)
        )
        await _publish_run_artifact(session, ctx, tenant, grace_upload, grace_run)

        removable_conv = await _seed_conversation(session, tenant)
        removable_run = await _seed_run(session, tenant, removable_conv, status=RunStatus.COMPLETED)
        removable_upload = await _seed_staged_upload(
            session, ctx, tenant, age=timedelta(hours=2), file_age=OLD_DAYS
        )
        run_artifact = await _publish_run_artifact(
            session, ctx, tenant, removable_upload, removable_run
        )
        await session.commit()

    async with get_session_factory()() as session:
        report = await cleanup_staging_uploads(
            session,
            artifact_root=ctx.root,
            grace_seconds=STAGING_GRACE_SECONDS,
            batch_size=RETENTION_BATCH_SIZE,
            tenant_id=tenant.tenant_id,
        )
        await session.commit()

    assert report.removed == 1
    assert report.skipped_active_reference == 1
    assert report.skipped_grace == 1
    assert {action.upload_id: action.action for action in report.actions} == {
        active_upload.id: ACTION_STAGING_ACTIVE_REFERENCE,
        grace_upload.id: ACTION_STAGING_GRACE,
        removable_upload.id: ACTION_STAGING_REMOVED,
    }
    assert len(report.lines()) == 3, "每条动作都要能逐行对账"
    assert all(str(action.upload_id) in action.line() for action in report.actions)

    async with get_session_factory()() as session:
        active_row = await session.get(ChannelUpload, active_upload.id)
        grace_row = await session.get(ChannelUpload, grace_upload.id)
        removable_row = await session.get(ChannelUpload, removable_upload.id)
        assert active_row is not None and active_row.is_deleted is False
        assert grace_row is not None and grace_row.is_deleted is False
        assert removable_row is not None and removable_row.is_deleted is True
        artifact_row = await session.get(Artifact, run_artifact.id)
        assert artifact_row is not None and artifact_row.is_deleted is False

    assert store.resolve(active_upload.storage_key).is_file(), "活跃引用保护下暂存文件不得删"
    assert store.resolve(grace_upload.storage_key).is_file(), "宽限期内暂存文件不得删"
    assert not store.resolve(removable_upload.storage_key).exists(), "过期且无保护的暂存文件应被清理"
    run_path = store.resolve(run_artifact.storage_key)
    assert run_path.is_file() and run_path.read_bytes() == PAYLOAD, (
        "清理暂存不得删已受理 Run 自己的制品字节（不同目录项）"
    )

    # 宽限期是唯一挡在第二份前面的东西：宽限期归零后同一轮清理必须把它带走
    async with get_session_factory()() as session:
        second = await cleanup_staging_uploads(
            session, artifact_root=ctx.root, grace_seconds=0.0, tenant_id=tenant.tenant_id
        )
        await session.commit()
    assert second.removed == 1
    assert not store.resolve(grace_upload.storage_key).exists()


# --------------------------------------------------------------------------------------
# B-269 ⑤ / B-07：Token 过期不删对话；Console 制品清理保护与 404
# --------------------------------------------------------------------------------------


async def test_b269_expired_chat_token_does_not_delete_conversations_or_history(
    retention_ctx: RetentionContext,
) -> None:
    """过期的 CHAT Token/会话不是删除对话或可见历史的理由：完整清理扫描后会话与水位不动。

    真实证据：`control.chat_access_token`/`control.chat_session` 是真实过期行；同一用户的会话、
    可见消息与持久水位在完整保留扫描（历史保留期已打开）之后逐项原样。
    """
    tenant = retention_ctx.tenant
    ctx = retention_ctx
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.platform_user (id, tenant_id, user_code, display_name, status)"
                " VALUES (:id, :tenant_id, :user_code, '保留清理用户', 'ACTIVE')"
            ),
            {
                "id": tenant.platform_user_id,
                "tenant_id": tenant.tenant_id,
                "user_code": f"retention-{uuid.uuid4().hex[:12]}",
            },
        )
        token_id, session_id = uuid.uuid4(), uuid.uuid4()
        await session.execute(
            sa.text(
                "INSERT INTO control.chat_access_token"
                " (id, tenant_id, platform_user_id, token_hash, revision, issued_at, expires_at, revoked_at)"
                " VALUES (:id, :tenant_id, :user_id, :hash, 1, now() - interval '40 days',"
                " now() - interval '39 days', NULL)"
            ),
            {
                "id": token_id,
                "tenant_id": tenant.tenant_id,
                "user_id": tenant.platform_user_id,
                "hash": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO control.chat_session"
                " (id, tenant_id, access_token_id, platform_user_id, token_hash,"
                " issued_at, expires_at, revoked_at)"
                " VALUES (:id, :tenant_id, :token_id, :user_id, :hash, now() - interval '40 days',"
                " now() - interval '40 days' + interval '12 hours', NULL)"
            ),
            {
                "id": session_id,
                "tenant_id": tenant.tenant_id,
                "token_id": token_id,
                "user_id": tenant.platform_user_id,
                "hash": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            },
        )
        conversation = await _seed_conversation(session, tenant)
        run = await _seed_run(session, tenant, conversation)
        user_seq = await _append_event(
            session,
            tenant,
            conversation,
            run,
            event_type="USER_MESSAGE",
            payload={"text": "过期 Token 之前的提问"},
        )
        assistant_seq = await _append_event(
            session,
            tenant,
            conversation,
            run,
            event_type="ASSISTANT_MESSAGE",
            payload={"text": "过期 Token 之前的回答"},
        )
        stream_seq = await _append_event(
            session,
            tenant,
            conversation,
            run,
            event_type="ASSISTANT_DELTA",
            stream_type="message.delta",
            payload={"delta": "旧增量"},
        )
        await _seed_notification(
            session,
            tenant,
            conversation,
            run,
            delivery_key=f"run:{run.id}:terminal",
            delivery_state=DELIVERY_NOT_REQUIRED,
        )
        await _seed_staged_upload(session, ctx, tenant, age=timedelta(hours=2), file_age=OLD_DAYS)
        await session.commit()

    async with get_session_factory()() as session:
        report = await run_retention_sweep(
            session,
            policy=_policy(history=1),
            artifact_root=ctx.root,
            grace_seconds=STAGING_GRACE_SECONDS,
            tenant_id=tenant.tenant_id,
        )
        await session.commit()

    assert report.stream_replay_removed == 1, "流增量按保留期回收"
    assert report.history_removed == 0, "没有摘要 ⇒ 可见消息是唯一上下文来源，一条都不删"
    assert report.notifications_removed == 1, "超期的终态回执入箱行被隐藏"
    assert report.staging_removed == 1

    async with get_session_factory()() as session:
        alive = await session.get(Conversation, conversation.id)
        assert alive is not None and alive.is_deleted is False, "Token 过期不删对话"
        assert int(alive.last_seq) == stream_seq, "会话持久水位不因清理回退"
        rows = await _event_rows(session, conversation)
        assert rows[user_seq][2] is False and rows[assistant_seq][2] is False, (
            "可见历史不因 Token 过期被删"
        )
        assert rows[stream_seq][2] is True
        token_rows = await session.scalar(
            sa.text("SELECT count(*) FROM control.chat_access_token WHERE tenant_id = :t"),
            {"t": tenant.tenant_id},
        )
        assert int(token_rows or 0) == 1, "保留清理不越界删 Console 拥有的凭据行"


async def test_b07_artifact_cleanup_protects_active_run_and_job_references(
    retention_ctx: RetentionContext, client: AsyncClient
) -> None:
    """Console 制品清理：尚被活跃 Run/job 引用的已受理制品不删；已清理字节的读取一律 404。

    真实边界：`runtime.run_record` 的活跃行与 `task.task_execution` 的排队行都是真实行；
    "读取"走真实内部产物解析路由（真实服务身份门控 + 真实归属核验）。
    """
    ctx = retention_ctx
    tenant = ctx.tenant
    store = NfsArtifactStore(ctx.root)
    async with get_session_factory()() as session:
        conversation = await _seed_conversation(session, tenant)
        active_run = await _seed_run(session, tenant, conversation, status=RunStatus.RUNNING)
        done_run = await _seed_run(session, tenant, conversation, status=RunStatus.COMPLETED)

        active_key = f"artifacts/{uuid.uuid4().hex}.txt"
        active_path = _write_file(ctx.root, active_key, file_age=OLD_DAYS)
        active_artifact = await _seed_artifact_row(
            session,
            tenant,
            conversation,
            run=active_run,
            task_id=None,
            storage_key=active_key,
            age=OLD_DAYS,
        )

        task_id = uuid.uuid4()
        session.add(
            TaskExecution(
                id=task_id,
                tenant_id=tenant.tenant_id,
                agent_id=tenant.agent_id,
                actor_user_id=tenant.platform_user_id,
                intent_key="retention-cleanup",
                skill_id=uuid.uuid4(),
                skill_artifact_id=uuid.uuid4(),
                trigger_type="MANUAL",
                execution_mode="ASYNC",
                status="QUEUED",
                input_json={},
                execution_snapshot_json={},
                snapshot_hash="sha256:" + "0" * 64,
                idempotency_key=f"retention-{uuid.uuid4().hex}",
                delivery_key=f"task:{task_id}:final",
            )
        )
        task_key = f"artifacts/{uuid.uuid4().hex}.txt"
        task_path = _write_file(ctx.root, task_key, file_age=OLD_DAYS)
        task_artifact = await _seed_artifact_row(
            session,
            tenant,
            conversation,
            run=None,
            task_id=task_id,
            storage_key=task_key,
            age=OLD_DAYS,
        )

        grace_key = f"artifacts/{uuid.uuid4().hex}.txt"
        grace_path = _write_file(ctx.root, grace_key, file_age=timedelta(seconds=30))
        grace_artifact = await _seed_artifact_row(
            session,
            tenant,
            conversation,
            run=done_run,
            task_id=None,
            storage_key=grace_key,
            age=OLD_DAYS,
        )

        removed_key = f"artifacts/{uuid.uuid4().hex}.txt"
        removed_path = _write_file(ctx.root, removed_key, file_age=OLD_DAYS)
        removed_artifact = await _seed_artifact_row(
            session,
            tenant,
            conversation,
            run=done_run,
            task_id=None,
            storage_key=removed_key,
            age=OLD_DAYS,
        )
        await session.commit()

    async with console_session_factory()() as session:
        report = await ArtifactCleanupService(
            session,
            store,
            retention_days=30,
            grace_seconds=STAGING_GRACE_SECONDS,
            limit=50,
            tenant_id=tenant.tenant_id,
        ).run(dry_run=False)
        await session.commit()

    actions = {action.artifact_id: action.action for action in report.actions}
    assert actions == {
        grace_artifact.id: ACTION_SKIPPED,
        removed_artifact.id: ACTION_REMOVED,
    }, "活跃 Run/job 引用的制品连候选都不是；宽限期内跳过、过期且无引用才删"
    assert report.count(ACTION_SKIPPED) == 1 and report.count(ACTION_REMOVED) == 1
    assert "removed=1" in report.summary() and "skipped=1" in report.summary()

    assert active_path.is_file() and task_path.is_file() and grace_path.is_file()
    assert not removed_path.exists(), "过期且无活跃引用的制品字节必须被删"

    async with get_session_factory()() as session:
        for artifact_id, alive in (
            (active_artifact.id, True),
            (task_artifact.id, True),
            (grace_artifact.id, True),
            (removed_artifact.id, False),
        ):
            row = await session.get(Artifact, artifact_id)
            assert (row is not None) is alive, f"制品行存活状态不符：{artifact_id}"

    # 已清理字节的读取一律 404（真实内部解析路由；不泄露存在性）
    cleaned = await _resolve_download(client, tenant, removed_artifact.id)
    assert cleaned.status_code == 404, cleaned.text
    assert cleaned.json()["code"] == "COMMON_NOT_FOUND"
    # 仍被引用的制品照常可解析（保护不是"整表停摆"）
    for artifact_id in (active_artifact.id, task_artifact.id, grace_artifact.id):
        kept = await _resolve_download(client, tenant, artifact_id)
        assert kept.status_code == 200, kept.text
        assert kept.json()["data"]["reference"]["storage_key"]
