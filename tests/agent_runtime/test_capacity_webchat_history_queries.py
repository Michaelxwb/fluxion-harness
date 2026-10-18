"""B-250 / B-04：历史列表、详情与固定水位分页（TASK-050）。

真实边界：真实 Runtime 应用（内部服务身份门控 + 真实路由）、真实 `WebchatHistoryService`、
隔离租户的真实 PostgreSQL（conversation/canonical_event/run_record/artifact 都是真实行，
canonical `seq` 由真实 `EventWriter` 在会话行锁内分配）；不 mock 存储、不触模型。

关键断言（design §3.4 API-09/14/15、§3.5 历史读取；B-250/B-04）：

1. 列表按 (tenant,user,agent,入口) 索引、`last_message_at/id` 倒序；list/count 同谓词
   **同快照**（单语句），活跃 Run 批量关联（无逐会话 N+1）。
2. 消息页**固定水位**：首屏冻结 `through_seq`，后页沿用；分页期间新消息不移动已固定水位；
   越界游标显式 `CURSOR_AHEAD`；页内正序、page 1 为最新窗口、向前加载旧页；总量可超 200。
3. 详情复用 TASK-049 的会话核属（tenant/user/agent/入口 同时成立），返回活跃 Run 的恢复身份
   （id/status/execution_status）与持久水位，不暴露内部快照；消息页水位不得当作已渲染正文的证明。
4. 历史投影只有 USER/ASSISTANT 可见消息与安全附件：工具参数/结果、SYSTEM、原始推理、未知事件
   与存储键一律不公开。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.db import get_engine, get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact, Conversation, RunRecord
from muad_common import SharedSettings
from muad_contracts.enums import RunStatus
from muad_contracts.webchat import (
    WebchatConversationDetail,
    WebchatConversationPage,
    WebchatMessagePage,
)
from sqlalchemy import event

from agent_runtime.conftest import CLEANUP_ORDER, TenantContext

pytestmark = pytest.mark.integration

#: 本套件的入口（WEBCHAT 稳定入口账号；真实核属维度之一，不建 Console 行）。
ENTRY = uuid.uuid4()
#: 清理顺序：制品先于 run_record/conversation（同 Owner 物理 FK），再走套件既有顺序。
PURGE_ORDER = ("artifact", *CLEANUP_ORDER)
BASE_TIME = datetime(2026, 10, 18, 8, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _PageSizeSettings:
    """显式声明历史页大小默认值：证明默认值来自平台设置，而不是实现里硬编码。"""

    page_size: int

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=0, settings={"webchat": {"history_page_size": self.page_size}}
        )


def _install_page_size(page_size: int) -> None:
    from muad_agent_runtime.api.deps import get_platform_settings_client
    from muad_agent_runtime.main import app

    app.dependency_overrides[get_platform_settings_client] = lambda: _PageSizeSettings(page_size)


# --------------------------------------------------------------------------------------
# 真实 PG 读写（只增删本租户的行）
# --------------------------------------------------------------------------------------


async def _purge_tenant(tenant_id: str) -> None:
    async with get_session_factory()() as session:
        for table in PURGE_ORDER:
            await session.execute(
                sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        await session.commit()


@pytest.fixture
async def history_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    try:
        yield tenant
    finally:
        await _purge_tenant(tenant.tenant_id)


async def _seed_conversation(
    tenant: TenantContext,
    *,
    entry_id: uuid.UUID | None = ENTRY,
    agent_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    title: str | None = None,
    last_message_at: datetime | None = BASE_TIME,
    status: str = "ACTIVE",
    is_deleted: bool = False,
) -> uuid.UUID:
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        user_id=user_id or tenant.platform_user_id,
        agent_id=agent_id or tenant.agent_id,
        title=title,
        status=status,
        last_seq=0,
        channel_account_id=entry_id,
        last_message_at=last_message_at,
        create_time=BASE_TIME,
        update_time=BASE_TIME,
        is_deleted=is_deleted,
    )
    async with get_session_factory()() as session:
        session.add(conversation)
        await session.commit()
    return conversation.id


async def _append_message(
    tenant_id: str,
    conversation_id: uuid.UUID,
    *,
    text: str,
    event_type: str = "USER_MESSAGE",
    run_id: uuid.UUID | None = None,
    attachment_ids: tuple[uuid.UUID, ...] = (),
) -> int:
    """真实 `EventWriter` 追加可见消息事件（`seq` 由会话行锁分配）。"""
    payload: dict[str, Any] = {"text": text}
    if attachment_ids:
        payload["attachments"] = [
            {
                "artifact_id": str(artifact_id),
                "kind": "DOCUMENT",
                "media_type": "text/plain",
                "filename": None,
                "size": 3,
            }
            for artifact_id in attachment_ids
        ]
    async with get_session_factory()() as session:
        seq = await EventWriter(session).append(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id or uuid.uuid4(),
            event_type=event_type,
            payload=payload,
        )
        await session.commit()
    return int(seq)


async def _append_messages_bulk(
    tenant_id: str, conversation_id: uuid.UUID, count: int
) -> list[int]:
    """批量追加可见消息（一条事务，仍是真实 `EventWriter` 逐条分配 seq）。"""
    seqs: list[int] = []
    async with get_session_factory()() as session:
        writer = EventWriter(session)
        for index in range(count):
            event_type = "USER_MESSAGE" if index % 2 == 0 else "ASSISTANT_MESSAGE"
            seqs.append(
                await writer.append(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    run_id=uuid.uuid4(),
                    event_type=event_type,
                    payload={"text": f"消息 {index:03d}"},
                )
            )
        await session.commit()
    return seqs


async def _append_internal_event(
    tenant_id: str, conversation_id: uuid.UUID, event_type: str, payload: dict[str, Any]
) -> int:
    async with get_session_factory()() as session:
        seq = await EventWriter(session).append(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=uuid.uuid4(),
            event_type=event_type,
            payload=payload,
        )
        await session.commit()
    return int(seq)


async def _seed_active_run(
    tenant: TenantContext,
    conversation_id: uuid.UUID,
    *,
    status: RunStatus = RunStatus.RUNNING,
    start_time: datetime | None = BASE_TIME,
    queue_stage: str | None = None,
    queued_at: datetime | None = None,
    queue_deadline_at: datetime | None = None,
) -> uuid.UUID:
    """真实 Run 行 + 会话 `last_run_id` 指针（与 `RunService._create_run` 同形）。"""
    run_id = uuid.uuid4()
    run = RunRecord(
        id=run_id,
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        status=status,
        input_text="seed",
        trace_id="seed-trace",
        start_time=start_time,
        queue_stage=queue_stage,
        queued_at=queued_at,
        queue_deadline_at=queue_deadline_at,
        create_time=BASE_TIME,
        update_time=BASE_TIME,
    )
    async with get_session_factory()() as session:
        session.add(run)
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        conversation.last_run_id = run_id
        await session.commit()
    return run_id


async def _seed_artifact(
    tenant: TenantContext,
    conversation_id: uuid.UUID,
    *,
    run_id: uuid.UUID,
    filename: str | None = "note.txt",
    is_deleted: bool = False,
) -> uuid.UUID:
    artifact = Artifact(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        run_id=run_id,
        task_id=None,
        conversation_id=conversation_id,
        artifact_type="INBOUND_DOCUMENT",
        storage_key=f"tenant/{tenant.tenant_id}/artifact-{uuid.uuid4().hex}",
        media_type="text/plain",
        size=3,
        checksum="sha256:" + "a" * 64,
        metadata_json={"kind": "DOCUMENT", "filename": filename},
        is_deleted=is_deleted,
        create_time=BASE_TIME,
        update_time=BASE_TIME,
    )
    async with get_session_factory()() as session:
        session.add(artifact)
        await session.commit()
    return artifact.id


async def _renew_run(tenant_id: str, run_id: uuid.UUID) -> None:
    """模拟后台续租/心跳：只改 Run 行租约列，不触碰会话最近活动。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.update(RunRecord)
            .where(RunRecord.id == run_id, RunRecord.tenant_id == tenant_id)
            .values(
                lease_owner="seed-owner",
                lease_until=BASE_TIME + timedelta(minutes=5),
                heartbeat_at=BASE_TIME + timedelta(seconds=30),
                update_time=BASE_TIME + timedelta(seconds=30),
            )
        )
        await session.commit()


async def _conversation_row(tenant_id: str, conversation_id: uuid.UUID) -> Conversation | None:
    async with get_session_factory()() as session:
        return await session.get(Conversation, conversation_id)


# --------------------------------------------------------------------------------------
# HTTP 驱动（真实应用 + 真实服务身份门控）
# --------------------------------------------------------------------------------------


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant.tenant_id,
        "X-Internal-Service": SharedSettings().internal_service_token or "",
    }


def _list_scope(tenant: TenantContext) -> dict[str, str]:
    return {"user_id": str(tenant.platform_user_id), "agent_id": str(tenant.agent_id)}


def _read_scope(tenant: TenantContext) -> dict[str, str]:
    """详情/消息读取的核属维度（入口与 Agent 都显式；TASK-052 从路由/入口快照解析）。"""
    return {
        "user_id": str(tenant.platform_user_id),
        "agent_id": str(tenant.agent_id),
        "entry_id": str(ENTRY),
    }


def _list_path(entry_id: uuid.UUID = ENTRY) -> str:
    return f"/internal/webchat/entries/{entry_id}/conversations"


def _detail_path(conversation_id: uuid.UUID) -> str:
    return f"/internal/webchat/conversations/{conversation_id}"


def _messages_path(conversation_id: uuid.UUID) -> str:
    return f"/internal/webchat/conversations/{conversation_id}/messages"


async def _get(client: AsyncClient, path: str, tenant: TenantContext, **params: Any) -> Any:
    return await client.get(path, headers=_headers(tenant), params=params)


def _page_data(response: Any, contract: Any) -> Any:
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == "0"
    return contract.model_validate(payload["data"])


# --------------------------------------------------------------------------------------
# B-250：列表
# --------------------------------------------------------------------------------------


async def test_b250_list_is_entry_scoped_ordered_and_batches_active_runs(
    client: AsyncClient, history_tenant: TenantContext
) -> None:
    tenant = history_tenant
    newer = await _seed_conversation(tenant, title=None, last_message_at=BASE_TIME)
    busy = await _seed_conversation(
        tenant, title="忙碌会话", last_message_at=BASE_TIME - timedelta(minutes=1)
    )
    older = await _seed_conversation(
        tenant, title="旧会话", last_message_at=BASE_TIME - timedelta(minutes=5)
    )
    # 从未有过最近活动的历史行（last_message_at NULL）：读取层回落 create_time，排序在最后。
    legacy = await _seed_conversation(tenant, title="遗留", last_message_at=None)
    run_id = await _seed_active_run(tenant, busy, status=RunStatus.RUNNING)

    # 干扰行：其他用户/其他 Agent/其他入口/未归属（NULL）/软删/其他租户，全部不得出现在本列表。
    await _seed_conversation(
        tenant, user_id=uuid.uuid4(), last_message_at=BASE_TIME + timedelta(hours=1)
    )
    await _seed_conversation(
        tenant, agent_id=uuid.uuid4(), last_message_at=BASE_TIME + timedelta(hours=1)
    )
    await _seed_conversation(
        tenant, entry_id=uuid.uuid4(), last_message_at=BASE_TIME + timedelta(hours=1)
    )
    await _seed_conversation(tenant, entry_id=None, last_message_at=BASE_TIME + timedelta(hours=1))
    await _seed_conversation(tenant, last_message_at=BASE_TIME + timedelta(hours=1), is_deleted=True)
    foreign = TenantContext(
        tenant_id=f"test-{uuid.uuid4()}",
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
    )
    try:
        await _seed_conversation(foreign, last_message_at=BASE_TIME + timedelta(hours=1))

        response = await _get(client, _list_path(), tenant, **_list_scope(tenant))
        page = _page_data(response, WebchatConversationPage)

        assert [item.conversation_id for item in page.items] == [newer, busy, older, legacy]
        assert page.page == 1 and page.total == 4
        # 默认页大小来自平台设置（本套件设置源无 webchat 组 ⇒ schema 默认 50）。
        assert page.page_size == 50
        titles = {item.conversation_id: item.title for item in page.items}
        assert titles[newer] == "新会话" and titles[busy] == "忙碌会话"
        statuses = {item.conversation_id: item.active_run_status for item in page.items}
        assert statuses[busy] == "RUNNING"
        assert all(statuses[cid] is None for cid in (newer, older, legacy))
        by_id = {item.conversation_id: item for item in page.items}
        assert by_id[busy].last_run_id == run_id
        assert by_id[legacy].last_message_at == BASE_TIME  # NULL 回落 create_time
    finally:
        await _purge_tenant(foreign.tenant_id)


async def test_b04_list_pages_are_deterministic_single_snapshot_and_not_n_plus_one(
    client: AsyncClient, history_tenant: TenantContext
) -> None:
    tenant = history_tenant
    seeded = [
        await _seed_conversation(
            tenant, title=f"会话{i}", last_message_at=BASE_TIME - timedelta(seconds=i)
        )
        for i in range(5)
    ]
    await _seed_active_run(tenant, seeded[0], status=RunStatus.RUNNING)
    await _seed_active_run(tenant, seeded[3], status=RunStatus.RUNNING)

    statements: list[str] = []
    engine = get_engine()

    def capture(*args: Any) -> None:
        statements.append(str(args[2]))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        pages = [
            _page_data(
                await _get(
                    client, _list_path(), tenant, **_list_scope(tenant), page=page, page_size=2
                ),
                WebchatConversationPage,
            )
            for page in (1, 2, 3)
        ]
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)

    # 确定顺序、无跳过/重复；越界页（page 3 只剩 1 条）总数仍与同谓词一致。
    observed = [item.conversation_id for page in pages for item in page.items]
    assert observed == seeded
    assert [page.total for page in pages] == [5, 5, 5]
    assert [[item.conversation_id for item in page.items] for page in pages] == [
        seeded[0:2],
        seeded[2:4],
        seeded[4:5],
    ]
    statuses = {item.conversation_id: item.active_run_status for item in pages[0].items}
    assert statuses == {seeded[0]: "RUNNING", seeded[1]: None}

    conversation_statements = [s for s in statements if "runtime.conversation" in s]
    run_statements = [s for s in statements if "runtime.run_record" in s]
    # list/count 同谓词同快照：每页恰一条语句（同一语句里同时有 count 与分页窗口）。
    assert len(conversation_statements) == 3
    assert all("count(*)" in statement for statement in conversation_statements)
    # 活跃 Run 状态按页批量关联：每页恰一次，不逐会话查询（无 N+1）。
    assert len(run_statements) == 3


async def test_b04_background_renewal_does_not_change_recency_order(
    client: AsyncClient, history_tenant: TenantContext
) -> None:
    tenant = history_tenant
    older = await _seed_conversation(
        tenant, title="旧", last_message_at=BASE_TIME - timedelta(minutes=5)
    )
    newer = await _seed_conversation(tenant, title="新", last_message_at=BASE_TIME)
    run_id = await _seed_active_run(tenant, older, status=RunStatus.RUNNING)

    await _renew_run(tenant.tenant_id, run_id)
    await _append_internal_event(
        tenant.tenant_id, older, "MEMORY_CONSOLIDATED", {"summary": "后台收尾"}
    )

    page = _page_data(
        await _get(client, _list_path(), tenant, **_list_scope(tenant)),
        WebchatConversationPage,
    )
    assert [item.conversation_id for item in page.items] == [newer, older]
    by_id = {item.conversation_id: item for item in page.items}
    # 后台续租/后台事件不刷新会话最近活动（排序依据仍是真实用户消息/终态正文时间）。
    assert by_id[older].last_message_at == BASE_TIME - timedelta(minutes=5)
    assert by_id[older].active_run_status == "RUNNING"
    row = await _conversation_row(tenant.tenant_id, older)
    assert row is not None and row.last_message_at == BASE_TIME - timedelta(minutes=5)


# --------------------------------------------------------------------------------------
# B-250 / B-04：固定水位分页
# --------------------------------------------------------------------------------------


async def test_b250_message_pages_freeze_the_watermark_and_reject_ahead_cursors(
    client: AsyncClient, history_tenant: TenantContext
) -> None:
    tenant = history_tenant
    conversation_id = await _seed_conversation(tenant, title="会话")
    await _append_messages_bulk(tenant.tenant_id, conversation_id, 5)

    first = _page_data(
        await _get(
            client, _messages_path(conversation_id), tenant, **_read_scope(tenant),
            page=1, page_size=3,
        ),
        WebchatMessagePage,
    )
    assert first.through_seq == 5 and first.total == 5
    assert [item.seq for item in first.items] == [3, 4, 5]  # page 1 = 最新窗口，页内正序

    # 分页期间新消息不移动已固定水位：沿用同一 through_seq 的页看不到新消息、也不跳过旧消息。
    await _append_message(tenant.tenant_id, conversation_id, text="新消息")
    frozen = _page_data(
        await _get(
            client, _messages_path(conversation_id), tenant, **_read_scope(tenant),
            page=1, page_size=3, through_seq=5,
        ),
        WebchatMessagePage,
    )
    assert frozen.through_seq == 5 and frozen.total == 5
    assert [item.seq for item in frozen.items] == [3, 4, 5]
    older_page = _page_data(
        await _get(
            client, _messages_path(conversation_id), tenant, **_read_scope(tenant),
            page=2, page_size=3, through_seq=5,
        ),
        WebchatMessagePage,
    )
    assert [item.seq for item in older_page.items] == [1, 2] and older_page.total == 5

    # 新首屏重新冻结水位：看到最新消息，且水位推进到新的持久水位。
    refreshed = _page_data(
        await _get(
            client, _messages_path(conversation_id), tenant, **_read_scope(tenant),
            page=1, page_size=3,
        ),
        WebchatMessagePage,
    )
    assert refreshed.through_seq == 6
    assert [item.seq for item in refreshed.items] == [4, 5, 6]

    # 越界游标显式失败：超过会话已持久水位（含不可见事件）一律 CURSOR_AHEAD。
    ahead = await _get(
        client, _messages_path(conversation_id), tenant, **_read_scope(tenant),
        page=1, page_size=3, through_seq=7,
    )
    assert ahead.status_code == 400
    assert ahead.json()["code"] == "CURSOR_AHEAD"

    # 后页必须沿用首屏冻结的水位（契约 WebchatHistoryQuery 同源）。
    missing = await _get(
        client, _messages_path(conversation_id), tenant, **_read_scope(tenant),
        page=2, page_size=3,
    )
    assert missing.status_code == 422
    assert missing.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_b250_history_supports_more_than_two_hundred_messages(
    client: AsyncClient, history_tenant: TenantContext
) -> None:
    tenant = history_tenant
    conversation_id = await _seed_conversation(tenant, title="长会话")
    await _append_messages_bulk(tenant.tenant_id, conversation_id, 250)

    first = _page_data(
        await _get(
            client, _messages_path(conversation_id), tenant, **_read_scope(tenant),
            page=1, page_size=100,
        ),
        WebchatMessagePage,
    )
    pages = [first]
    for page in (2, 3):
        pages.append(
            _page_data(
                await _get(
                    client, _messages_path(conversation_id), tenant, **_read_scope(tenant),
                    page=page, page_size=100, through_seq=first.through_seq,
                ),
                WebchatMessagePage,
            )
        )
    assert all(page.total == 250 for page in pages)  # 总量不被 200 条上限截断
    assert [page.page_size for page in pages] == [100, 100, 100]
    observed = [item.seq for page in pages for item in page.items]
    # 每页内正序、页间向前加载旧页；三页并集覆盖全部消息：无跳过、无重复。
    assert observed == (
        list(range(151, 251)) + list(range(51, 151)) + list(range(1, 51))
    )
    assert sorted(observed) == list(range(1, 251))
    assert [item.seq for item in pages[0].items] == list(range(151, 251))
    assert [item.seq for item in pages[2].items] == list(range(1, 51))
    assert all(page.through_seq == 250 for page in pages)

    # 默认页大小来自平台设置（这里显式声明 30），不是实现里的硬编码常量。
    _install_page_size(30)
    default_page = _page_data(
        await _get(
            client, _messages_path(conversation_id), tenant, **_read_scope(tenant), page=1
        ),
        WebchatMessagePage,
    )
    assert default_page.page_size == 30 and len(default_page.items) == 30

    # 页大小上限 100（契约 MAX_PAGE_SIZE）。
    over = await _get(
        client, _messages_path(conversation_id), tenant, **_read_scope(tenant),
        page=1, page_size=101,
    )
    assert over.status_code == 422
    assert over.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_b250_active_run_keeps_visible_body_and_recovery_identity(
    client: AsyncClient, history_tenant: TenantContext
) -> None:
    """活跃 Run 恢复：详情给出真实恢复身份与持久水位；历史页不丢任何可见正文。

    消息页的 `through_seq` 是**会话持久水位**（含活跃 Run 已落库的增量事件），不是"已渲染正文"
    的证明：客户端必须用活跃 Run 自己的游标/安全快照恢复增量（design §3.4/§3.6），本用例证明
    恢复身份与水位都是可观测的、可见正文一条不丢。
    """
    tenant = history_tenant
    conversation_id = await _seed_conversation(tenant, title="进行中")
    await _append_message(tenant.tenant_id, conversation_id, text="上一轮问题")
    await _append_message(
        tenant.tenant_id, conversation_id, text="上一轮回答", event_type="ASSISTANT_MESSAGE"
    )
    run_id = await _seed_active_run(tenant, conversation_id, status=RunStatus.RUNNING)
    await _append_message(tenant.tenant_id, conversation_id, text="本轮问题", run_id=run_id)
    await _append_internal_event(tenant.tenant_id, conversation_id, "MESSAGE_DELTA", {"delta": "已"})
    await _append_internal_event(
        tenant.tenant_id, conversation_id, "MESSAGE_DELTA", {"delta": "落库"}
    )

    row = await _conversation_row(tenant.tenant_id, conversation_id)
    assert row is not None and row.last_seq == 5

    page = _page_data(
        await _get(
            client, _messages_path(conversation_id), tenant, **_read_scope(tenant),
            page=1, page_size=50,
        ),
        WebchatMessagePage,
    )
    assert page.through_seq == 5  # 冻结在会话持久水位（覆盖增量事件），不是可见消息的最大 seq
    assert [item.seq for item in page.items] == [1, 2, 3]
    assert [item.text for item in page.items] == ["上一轮问题", "上一轮回答", "本轮问题"]

    detail = _page_data(
        await _get(client, _detail_path(conversation_id), tenant, **_read_scope(tenant)),
        WebchatConversationDetail,
    )
    assert detail.last_seq == 5 and detail.last_run_id == run_id
    assert detail.active_run is not None
    assert detail.active_run.id == run_id and detail.active_run.status == "RUNNING"
    assert detail.active_run.execution_status.stage == "RUNNING"
    assert detail.active_run.confirmation is None


# --------------------------------------------------------------------------------------
# B-250：详情核属与公开投影
# --------------------------------------------------------------------------------------


async def test_b250_detail_is_attributed_and_exposes_no_internal_snapshot(
    client: AsyncClient, history_tenant: TenantContext
) -> None:
    tenant = history_tenant
    conversation_id = await _seed_conversation(tenant, title="详情会话")
    await _append_message(tenant.tenant_id, conversation_id, text="问题")
    await _append_message(
        tenant.tenant_id, conversation_id, text="回答", event_type="ASSISTANT_MESSAGE"
    )

    response = await _get(
        client, _detail_path(conversation_id), tenant, **_read_scope(tenant)
    )
    detail = _page_data(response, WebchatConversationDetail)
    assert detail.conversation_id == conversation_id
    assert detail.entry_id == ENTRY and detail.agent_id == tenant.agent_id
    assert detail.title == "详情会话" and detail.status == "ACTIVE"
    assert detail.last_seq == 2 and detail.active_run is None
    assert set(json.loads(response.text)["data"]) == {
        "conversation_id",
        "entry_id",
        "agent_id",
        "title",
        "status",
        "last_seq",
        "last_run_id",
        "active_run",
    }

    # 归属拒绝矩阵（复用 TASK-049 的 tenant/user/agent/入口 核属）：一律 404 不泄露存在性。
    base = _read_scope(tenant)
    rejections = [
        {**base, "user_id": str(uuid.uuid4())},
        {**base, "agent_id": str(uuid.uuid4())},
        {**base, "entry_id": str(uuid.uuid4())},
    ]
    for params in rejections:
        denied = await _get(client, _detail_path(conversation_id), tenant, **params)
        assert denied.status_code == 404, denied.text
        assert denied.json()["code"] == "COMMON_NOT_FOUND"

    # 不可归属入口不得借用已归属会话：未归属会话也不作为网页历史暴露。
    unattributed = await _seed_conversation(tenant, entry_id=None)
    denied = await _get(client, _detail_path(unattributed), tenant, **base)
    assert denied.status_code == 404

    # 跨租户：真实行存在，但本租户读不到。
    foreign = TenantContext(
        tenant_id=f"test-{uuid.uuid4()}",
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
    )
    try:
        foreign_conversation = await _seed_conversation(foreign)
        denied = await _get(client, _detail_path(foreign_conversation), tenant, **base)
        assert denied.status_code == 404
    finally:
        await _purge_tenant(foreign.tenant_id)


async def test_b250_waiting_input_detail_reports_the_real_run_state(
    client: AsyncClient, history_tenant: TenantContext
) -> None:
    tenant = history_tenant
    conversation_id = await _seed_conversation(tenant, title="待确认")
    run_id = await _seed_active_run(tenant, conversation_id, status=RunStatus.WAITING_INPUT)
    detail = _page_data(
        await _get(client, _detail_path(conversation_id), tenant, **_read_scope(tenant)),
        WebchatConversationDetail,
    )
    assert detail.active_run is not None
    assert detail.active_run.id == run_id and detail.active_run.status == "WAITING_INPUT"
    assert detail.active_run.execution_status.stage == "WAITING_INPUT"


# --------------------------------------------------------------------------------------
# B-04：公开投影（白名单 + 附件安全）
# --------------------------------------------------------------------------------------


async def test_b04_projection_only_exposes_visible_messages_and_safe_attachments(
    client: AsyncClient, history_tenant: TenantContext
) -> None:
    tenant = history_tenant
    conversation_id = await _seed_conversation(tenant, title="投影")
    run_id = uuid.uuid4()
    available = await _seed_artifact(tenant, conversation_id, run_id=run_id, filename="note.txt")
    removed = await _seed_artifact(
        tenant, conversation_id, run_id=run_id, filename="gone.txt", is_deleted=True
    )
    user_seq = await _append_message(
        tenant.tenant_id,
        conversation_id,
        text="用户正文",
        run_id=run_id,
        attachment_ids=(available, removed),
    )
    assistant_seq = await _append_message(
        tenant.tenant_id,
        conversation_id,
        text="助手正文",
        event_type="ASSISTANT_MESSAGE",
        run_id=run_id,
    )
    # 内部事件：工具参数/结果、原始推理、系统提示、未知事件与记忆收尾都不得进入公开历史。
    await _append_internal_event(
        tenant.tenant_id,
        conversation_id,
        "TOOL_CALL",
        {"tool_call_id": "t1", "tool_name": "secret-tool", "arguments": {"canary": "CANARY_TOOL"}},
    )
    await _append_internal_event(
        tenant.tenant_id,
        conversation_id,
        "ASSISTANT_TURN",
        {"text": "CANARY_REASONING", "reasoning_content": "CANARY_REASONING"},
    )
    await _append_internal_event(
        tenant.tenant_id, conversation_id, "SYSTEM_PROMPT", {"content": "CANARY_SYSTEM"}
    )
    await _append_internal_event(
        tenant.tenant_id, conversation_id, "UNKNOWN_INTERNAL_EVENT", {"secret": "CANARY_UNKNOWN"}
    )
    await _append_internal_event(
        tenant.tenant_id, conversation_id, "MEMORY_CONSOLIDATED", {"summary": "CANARY_MEMORY"}
    )

    statements: list[str] = []
    engine = get_engine()

    def capture(*args: Any) -> None:
        statements.append(str(args[2]))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        response = await _get(
            client, _messages_path(conversation_id), tenant, **_read_scope(tenant),
            page=1, page_size=50,
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    page = _page_data(response, WebchatMessagePage)

    # 消息 list/count 同谓词同快照（单语句）；附件元信息按页批量取（不逐条回查，无 N+1）。
    message_statements = [s for s in statements if "runtime.canonical_event" in s]
    artifact_statements = [s for s in statements if "runtime.artifact" in s]
    assert len(message_statements) == 1 and "count(*)" in message_statements[0]
    assert len(artifact_statements) == 1

    assert [item.seq for item in page.items] == [user_seq, assistant_seq]
    assert [item.role for item in page.items] == ["USER", "ASSISTANT"]
    assert [item.text for item in page.items] == ["用户正文", "助手正文"]
    assert page.items[1].attachments == []

    attachment = page.items[0].attachments[0]
    assert attachment.artifact_id == available
    assert attachment.checksum == "sha256:" + "a" * 64
    assert attachment.filename == "note.txt" and attachment.available is True
    assert page.items[0].attachments[1].artifact_id == removed
    assert page.items[0].attachments[1].available is False  # 失效附件明确提示，不伪造可用

    wire = response.text
    for canary in (
        "CANARY_TOOL",
        "CANARY_REASONING",
        "CANARY_SYSTEM",
        "CANARY_UNKNOWN",
        "CANARY_MEMORY",
        "secret-tool",
        "storage_key",
    ):
        assert canary not in wire, canary
    assert set(json.loads(wire)["data"]) == {"items", "page", "page_size", "total", "through_seq"}
    assert set(json.loads(wire)["data"]["items"][0]) == {
        "event_id",
        "seq",
        "run_id",
        "role",
        "text",
        "attachments",
    }


async def test_b250_internal_history_endpoints_require_service_identity(
    client: AsyncClient, history_tenant: TenantContext
) -> None:
    tenant = history_tenant
    conversation_id = await _seed_conversation(tenant)
    path = _messages_path(conversation_id)
    params = {"user_id": str(tenant.platform_user_id), "entry_id": str(ENTRY)}
    for headers in (
        {"X-Tenant-Id": tenant.tenant_id},
        {"X-Tenant-Id": tenant.tenant_id, "X-Internal-Service": "wrong-token"},
        {"X-Tenant-Id": tenant.tenant_id, "X-Internal-Service": ""},
    ):
        denied = await client.get(path, headers=headers, params=params)
        assert denied.status_code == 403

    # 缺受信主体字段（user_id/agent_id/entry_id）在入口显式拒绝。
    missing = await _get(client, path, tenant, entry_id=str(ENTRY))
    assert missing.status_code == 422
