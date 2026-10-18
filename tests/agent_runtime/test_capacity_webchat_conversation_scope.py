"""B-249：[integration] 显式会话创建、入口核属与标题（design §3.3 / §3.4 / §3.6）。

真实边界（不 mock 存储或租约）：

- 真实 `RunService` **受理路径**：会话解析/核属、准入、Run/快照/提交/事件、入口指针切换
  全部走真实业务代码；
- 隔离租户的真实 PostgreSQL：`runtime.conversation` / `channel_conversation` /
  `canonical_event` / `run_record` / `memory_scope`（只增删本租户的行）；
- 模型调用是进程内替身（本任务不验证真实模型执行）；**标题推导不调用模型**由
  `FakeResolveClient` 的真实调用记录直接证明（只应出现本次受理的定义解析）。

关键断言（B-249 契约）：

1. 复用原 Conversation/Canonical/DB 上下文：显式会话续聊落在同一会话行、同一条 canonical
   序列上，不新建会话；
2. tenant/user/agent/entry **同时核属**：跨租户、越权用户/Agent、借其他入口（或不可归属入口
   借用已归属会话）一律 `COMMON_NOT_FOUND`，失败不留 Run/新会话；
3. 无显式会话的入口按 `channel_conversation` 五元组（tenant/user/agent/入口/外线线程）**锁内
   切换当前指针**：重复消息回同一会话、指针压过「用户最近发言」的旧口径、不同线程/入口互不
   串台、`/new` 后指针原地切换、指向已软删会话时原地改指可用会话；
4. 新建会话**不取消旧 Run**：旧会话的活跃 Run 保持 RUNNING、`cancel_requested` 不变；
5. 首条真实用户文本归一化后截取 80 字符成为 `conversation.title`，纯附件用本地化占位，
   空会话由读取层显示「新会话」；
6. 长期记忆继续按 (tenant,user) 共享：跨入口/会话的 scope 锁命中同一 `memory_scope` 行。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.channel_conversations import (
    display_title,
    entry_id_of,
)
from muad_agent_runtime.application.executor import (
    ExecutorEvent,
    ExecutorFactory,
    ExecutorRequest,
    RunExecutor,
)
from muad_agent_runtime.application.memory_commands import MemoryWriteContext
from muad_agent_runtime.application.memory_service import MemoryService
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.application.run_service import RunService, RunStart
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    ChannelConversation,
    Conversation,
    MemoryScope,
    RunRecord,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import AttachmentRef, ChannelContext, MessageInput, RunRequest

from agent_runtime.conftest import CLEANUP_ORDER, FakeResolveClient, TenantContext

pytestmark = pytest.mark.integration

INSTANCE_ID = "instance-b249"
WAIT_TIMEOUT_SEC = 30.0
TITLE_LIMIT = 80
THREAD_A = "thread-a"
THREAD_B = "thread-b"
#: 长文本首条消息：归一化后应为 `你好 世界 这是` + `长` * 72（前 80 个字符）。
MESSY_FIRST_TEXT = "  你好\n世界\t这是" + "长" * 100 + "   "
EXPECTED_FIRST_TITLE = "你好 世界 这是" + "长" * (TITLE_LIMIT - len("你好 世界 这是"))

#: 清理顺序：指针/制品先于 conversation/run_record（同 Owner 物理 FK），再走套件既有顺序。
PURGE_ORDER = ("channel_conversation", "artifact", *CLEANUP_ORDER)


class _ScopeSettings:
    """本套件设置源：足够大的准入容量；locale 可切换（标题占位本地化）。"""

    def __init__(self, *, default_locale: str | None = None) -> None:
        self._default_locale = default_locale

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        settings: dict[str, Any] = {
            "capacity": {
                "max_open_runs_per_tenant": 100_000,
                "max_open_runs_per_user": 100,
            }
        }
        if self._default_locale is not None:
            settings["locale"] = {"default_locale": self._default_locale}
        return PlatformSettingsSnapshot(revision=0, settings=settings)


class _GatedExecutor:
    """模型调用替身：阻塞到 `release()`，让 Run 保持非终态。"""

    def __init__(self, gate: asyncio.Event) -> None:
        self._gate = gate

    async def run(self) -> AsyncIterator[ExecutorEvent]:
        await self._gate.wait()
        yield ExecutorEvent(type="message.delta", data={"delta": "ok"})


class _GatedExecutorFactory:
    def __init__(self) -> None:
        self._gate = asyncio.Event()

    def release(self) -> None:
        self._gate.set()

    async def __call__(self, request: ExecutorRequest) -> RunExecutor:
        return _GatedExecutor(self._gate)


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
async def scope_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    try:
        yield tenant
    finally:
        await _purge_tenant(tenant.tenant_id)


async def _seed_conversation(
    tenant: TenantContext,
    *,
    entry_id: uuid.UUID | None = None,
    thread: str | None = None,
    user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
) -> uuid.UUID:
    conversation_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.conversation"
                " (id, tenant_id, user_id, agent_id, status, last_seq,"
                "  channel_account_id, external_thread_id)"
                " VALUES (:id, :tenant_id, :user_id, :agent_id, 'ACTIVE', 0,"
                "         :entry_id, :thread)"
            ).bindparams(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=user_id or tenant.platform_user_id,
                agent_id=agent_id or tenant.agent_id,
                entry_id=entry_id,
                thread=thread,
            )
        )
        await session.commit()
    return conversation_id


async def _append_user_message(
    tenant_id: str, conversation_id: uuid.UUID, text: str
) -> None:
    """独立事务追加真实 `USER_MESSAGE` 事件（真实 `EventWriter`，`seq` 由会话行锁分配）。"""
    async with get_session_factory()() as session:
        await EventWriter(session).append(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=uuid.uuid4(),
            event_type="USER_MESSAGE",
            payload={"text": text},
        )
        await session.commit()


async def _read_conversation(conversation_id: uuid.UUID) -> dict[str, Any] | None:
    async with get_session_factory()() as session:
        row = await session.get(Conversation, conversation_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "user_id": row.user_id,
            "agent_id": row.agent_id,
            "title": row.title,
            "status": row.status,
            "last_seq": row.last_seq,
            "last_run_id": row.last_run_id,
            "channel_account_id": row.channel_account_id,
            "external_thread_id": row.external_thread_id,
            "last_message_at": row.last_message_at,
            "is_deleted": row.is_deleted,
        }


async def _read_run(run_id: uuid.UUID) -> dict[str, Any] | None:
    async with get_session_factory()() as session:
        row = await session.get(RunRecord, run_id)
        if row is None:
            return None
        return {
            "status": str(row.status),
            "cancel_requested": bool(row.cancel_requested),
            "conversation_id": row.conversation_id,
        }


async def _read_pointer(
    tenant_id: str, *, user_id: uuid.UUID, agent_id: uuid.UUID,
    entry_id: uuid.UUID, thread: str,
) -> dict[str, Any] | None:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(ChannelConversation).where(
                    ChannelConversation.tenant_id == tenant_id,
                    ChannelConversation.user_id == user_id,
                    ChannelConversation.agent_id == agent_id,
                    ChannelConversation.channel_account_id == entry_id,
                    ChannelConversation.external_thread_id == thread,
                    ChannelConversation.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {"id": row.id, "conversation_id": row.conversation_id}


async def _conversation_rows(tenant_id: str, **filters: Any) -> list[uuid.UUID]:
    async with get_session_factory()() as session:
        conditions = [Conversation.tenant_id == tenant_id]
        for column, value in filters.items():
            conditions.append(getattr(Conversation, column) == value)
        return list(
            (
                await session.execute(sa.select(Conversation.id).where(*conditions))
            ).scalars()
        )


async def _user_message_seqs(tenant_id: str, conversation_id: uuid.UUID) -> list[int]:
    async with get_session_factory()() as session:
        return list(
            (
                await session.execute(
                    sa.select(CanonicalEvent.seq)
                    .where(
                        CanonicalEvent.tenant_id == tenant_id,
                        CanonicalEvent.conversation_id == conversation_id,
                        CanonicalEvent.event_type == "USER_MESSAGE",
                    )
                    .order_by(CanonicalEvent.seq)
                )
            ).scalars()
        )


async def _wait_for_run_status(run_id: uuid.UUID, statuses: tuple[str, ...]) -> None:
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    observed: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        observed = await _read_run(run_id)
        if observed is not None and observed["status"] in statuses:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"超时未到达 {statuses}：最后观测={observed}")


# --------------------------------------------------------------------------------------
# Run 受理驱动：真实 RunService（替换的只有模型调用与定义解析）
# --------------------------------------------------------------------------------------


def _request(
    tenant: TenantContext,
    *,
    text: str = "继续",
    conversation_id: uuid.UUID | None = None,
    entry_id: uuid.UUID | None = None,
    bot_id: str | None = None,
    user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    external_user_id: str = "ext-b249",
    external_conversation_id: str = THREAD_A,
    attachments: list[AttachmentRef] | None = None,
) -> RunRequest:
    return RunRequest(
        agent_id=agent_id or tenant.agent_id,
        platform_user_id=user_id or tenant.platform_user_id,
        conversation_id=conversation_id,
        channel=ChannelContext(
            type="WECOM",
            bot_id=bot_id if bot_id is not None else str(entry_id),
            external_user_id=external_user_id,
            external_conversation_id=external_conversation_id,
        ),
        message=MessageInput(
            id=f"msg-{uuid.uuid4()}",
            text=text,
            attachments=attachments or [],
        ),
    )


def _attachment() -> AttachmentRef:
    return AttachmentRef(
        storage_key=f"staging/{uuid.uuid4()}/report.pdf",
        kind="DOCUMENT",
        media_type="application/pdf",
        size=1024,
        filename="report.pdf",
        checksum="sha256:" + "a" * 64,
    )


async def _start(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    supervisor: ExecutionSupervisor,
    request: RunRequest,
    *,
    settings_client: Any = None,
    key: str | None = None,
) -> RunStart:
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            INSTANCE_ID,
            settings_client if settings_client is not None else _ScopeSettings(),
            executor_factory=executor_factory,
            supervisor=supervisor,
        )
        return await service.start(request, tenant.tenant_id, idempotency_key=key)


async def _create_conversation(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    supervisor: ExecutionSupervisor,
    *,
    entry_id: uuid.UUID | None = None,
    thread: str = "",
    user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    key: str | None = None,
) -> uuid.UUID:
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            INSTANCE_ID,
            _ScopeSettings(),
            supervisor=supervisor,
        )
        conversation = await service.create_conversation(
            agent_id or tenant.agent_id,
            user_id or tenant.platform_user_id,
            tenant.tenant_id,
            idempotency_key=key,
            entry_id=entry_id,
            external_thread_id=thread,
        )
        return conversation.id


async def _drain(*starts: RunStart) -> None:
    for started in starts:
        async for _ in started.events:
            pass


async def _rejects(start_coro: Any) -> AppError:
    with pytest.raises(AppError) as raised:
        await start_coro
    return raised.value


# --------------------------------------------------------------------------------------
# B-249 断言 1/2：显式会话核属 tenant/user/agent/entry
# --------------------------------------------------------------------------------------


async def test_b249_explicit_conversation_is_scoped_to_tenant_user_agent_entry(
    scope_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    tenant = scope_tenant
    entry = uuid.uuid4()
    conversation_id = await _seed_conversation(
        tenant, entry_id=entry, thread=THREAD_A
    )
    await _append_user_message(tenant.tenant_id, conversation_id, "历史消息")

    # ① 正常续聊：落在原会话行、同一条 canonical 序列（复用原 Conversation/Canonical/DB 上下文）
    start = await _start(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, conversation_id=conversation_id, entry_id=entry, text="继续"),
    )
    await _drain(start)
    assert start.conversation_id == conversation_id
    assert await _conversation_rows(tenant.tenant_id) == [conversation_id]
    assert await _user_message_seqs(tenant.tenant_id, conversation_id) == [1, 2]
    row = await _read_conversation(conversation_id)
    assert row is not None
    assert row["channel_account_id"] == entry
    assert row["last_run_id"] == start.run_id
    assert row["last_message_at"] is not None
    assert len(fake_resolve.calls) == 1, "标题推导不得额外调用定义解析"

    # ② 拒绝矩阵：每条都不得留下 Run / 新会话
    conversations_before = await _conversation_rows(tenant.tenant_id)
    other_entry = uuid.uuid4()
    cases: list[tuple[str, RunRequest]] = [
        (
            "借其他入口",
            _request(tenant, conversation_id=conversation_id, bot_id=str(other_entry)),
        ),
        (
            "入口不可归属却借用已归属会话",
            _request(tenant, conversation_id=conversation_id, bot_id="bot-wecom"),
        ),
        (
            "越权用户",
            _request(
                tenant, conversation_id=conversation_id, entry_id=entry, user_id=uuid.uuid4()
            ),
        ),
        (
            "越权 Agent",
            _request(
                tenant, conversation_id=conversation_id, entry_id=entry, agent_id=uuid.uuid4()
            ),
        ),
    ]
    for label, request in cases:
        error = await _rejects(
            _start(tenant, fake_resolve, executor_factory, execution_supervisor_boundary, request)
        )
        assert error.code == ErrorCode.COMMON_NOT_FOUND, label
    assert await _conversation_rows(tenant.tenant_id) == conversations_before
    assert await _user_message_seqs(tenant.tenant_id, conversation_id) == [1, 2]
    async with get_session_factory()() as session:
        runs = await session.scalar(
            sa.select(sa.func.count())
            .select_from(RunRecord)
            .where(RunRecord.tenant_id == tenant.tenant_id)
        )
    assert int(runs or 0) == 1, "拒绝路径不得留下 Run"

    # ③ 跨租户：另一个租户的会话在本租户一律不可见
    foreign = TenantContext(
        tenant_id=f"test-{uuid.uuid4()}", agent_id=uuid.uuid4(), platform_user_id=uuid.uuid4()
    )
    try:
        foreign_conversation = await _seed_conversation(
            foreign, entry_id=entry, thread=THREAD_A
        )
        error = await _rejects(
            _start(
                tenant,
                fake_resolve,
                executor_factory,
                execution_supervisor_boundary,
                _request(tenant, conversation_id=foreign_conversation, entry_id=entry),
            )
        )
        assert error.code == ErrorCode.COMMON_NOT_FOUND
    finally:
        await _purge_tenant(foreign.tenant_id)


# --------------------------------------------------------------------------------------
# B-249 断言 3：无显式会话的入口按入口指针选会话（锁内切换）
# --------------------------------------------------------------------------------------


async def test_b249_entry_pointer_switches_current_conversation_in_lock(
    scope_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    tenant = scope_tenant
    entry = uuid.uuid4()

    # ① 首次无显式会话：新建会话并登记指针（同一五元组）
    first = await _start(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, entry_id=entry, external_conversation_id=THREAD_A, text="第一条"),
    )
    await _drain(first)
    pointer = await _read_pointer(
        tenant.tenant_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        entry_id=entry,
        thread=THREAD_A,
    )
    assert pointer is not None and pointer["conversation_id"] == first.conversation_id
    created = await _read_conversation(first.conversation_id)
    assert created is not None
    assert created["channel_account_id"] == entry
    assert created["external_thread_id"] == THREAD_A

    # ② 指针压过「用户最近发言」旧口径：同用户/Agent 的新会话有更新的用户发言，仍回指针会话
    newer = await _seed_conversation(tenant, entry_id=entry, thread=THREAD_B)
    await _append_user_message(tenant.tenant_id, newer, "别处的更新发言")
    again = await _start(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, entry_id=entry, external_conversation_id=THREAD_A, text="第二条"),
    )
    await _drain(again)
    assert again.conversation_id == first.conversation_id
    assert set(await _conversation_rows(tenant.tenant_id, channel_account_id=entry)) == {
        first.conversation_id,
        newer,
    }, "重复消息不得新建会话"

    # ③ 不同外线线程 / 不同入口互不串台
    other_thread = await _start(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, entry_id=entry, external_conversation_id=THREAD_B, text="另一线程"),
    )
    await _drain(other_thread)
    assert other_thread.conversation_id == newer
    other_entry = uuid.uuid4()
    third_entry = await _start(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, entry_id=other_entry, external_conversation_id=THREAD_A, text="另一入口"),
    )
    await _drain(third_entry)
    assert third_entry.conversation_id not in {first.conversation_id, newer}
    entry_pointer = await _read_pointer(
        tenant.tenant_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        entry_id=other_entry,
        thread=THREAD_A,
    )
    assert entry_pointer is not None
    assert entry_pointer["conversation_id"] == third_entry.conversation_id

    # ④ 新建会话（/new 语义）：指针**原地切换**（同一行 id，不新建指针行）
    switched_id = await _create_conversation(
        tenant,
        fake_resolve,
        execution_supervisor_boundary,
        entry_id=entry,
        thread=THREAD_A,
        key="b249-new-1",
    )
    switched = await _read_pointer(
        tenant.tenant_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        entry_id=entry,
        thread=THREAD_A,
    )
    assert switched is not None
    assert switched["id"] == pointer["id"], "指针切换必须原地更新"
    assert switched["conversation_id"] == switched_id
    fresh = await _read_conversation(switched_id)
    assert fresh is not None
    assert fresh["title"] is None and fresh["last_seq"] == 0 and fresh["last_run_id"] is None
    assert fresh["last_message_at"] is not None
    next_message = await _start(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, entry_id=entry, external_conversation_id=THREAD_A, text="新建之后"),
    )
    await _drain(next_message)
    assert next_message.conversation_id == switched_id

    # ⑤ 指针指向已软删会话：原地改指可用会话（同一五元组最近会话）
    async with get_session_factory()() as session:
        await session.execute(
            sa.update(Conversation)
            .where(Conversation.id == switched_id)
            .values(is_deleted=True)
        )
        await session.commit()
    recovered = await _start(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, entry_id=entry, external_conversation_id=THREAD_A, text="会话被清理后"),
    )
    await _drain(recovered)
    repointed = await _read_pointer(
        tenant.tenant_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        entry_id=entry,
        thread=THREAD_A,
    )
    assert repointed is not None and repointed["id"] == pointer["id"]
    assert repointed["conversation_id"] != switched_id
    recovered_row = await _read_conversation(repointed["conversation_id"])
    assert recovered_row is not None and recovered_row["is_deleted"] is False
    assert recovered_row["channel_account_id"] == entry


# --------------------------------------------------------------------------------------
# B-249 断言 4：新建不取消旧 Run
# --------------------------------------------------------------------------------------


async def test_b249_new_conversation_never_cancels_the_old_run(
    scope_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    tenant = scope_tenant
    entry = uuid.uuid4()
    gated = _GatedExecutorFactory()
    running = await _start(
        tenant,
        fake_resolve,
        gated,
        execution_supervisor_boundary,
        _request(tenant, entry_id=entry, external_conversation_id=THREAD_A, text="进行中的任务"),
    )
    await _wait_for_run_status(running.run_id, ("RUNNING",))

    new_conversation = await _create_conversation(
        tenant,
        fake_resolve,
        execution_supervisor_boundary,
        entry_id=entry,
        thread=THREAD_A,
        key="b249-new-2",
    )
    assert new_conversation != running.conversation_id

    old_run = await _read_run(running.run_id)
    assert old_run is not None
    assert old_run["status"] == "RUNNING", "新建会话不得取消旧 Run"
    assert old_run["cancel_requested"] is False
    old_conversation = await _read_conversation(running.conversation_id)
    assert old_conversation is not None
    assert old_conversation["last_run_id"] == running.run_id
    new_row = await _read_conversation(new_conversation)
    assert new_row is not None and new_row["last_run_id"] is None

    # 新建后的下一条无显式会话消息落在新会话上，旧 Run 仍不受影响
    follow_up = await _start(
        tenant,
        fake_resolve,
        gated,
        execution_supervisor_boundary,
        _request(tenant, entry_id=entry, external_conversation_id=THREAD_A, text="新会话里的任务"),
    )
    assert follow_up.conversation_id == new_conversation
    old_run_after = await _read_run(running.run_id)
    assert old_run_after is not None and old_run_after["status"] == "RUNNING"
    assert old_run_after["cancel_requested"] is False

    gated.release()
    await _drain(running, follow_up)


# --------------------------------------------------------------------------------------
# B-249 断言 5：标题（首条文本 80 字符 / 纯附件本地化占位 / 无需模型）
# --------------------------------------------------------------------------------------


async def test_b249_first_message_title_is_normalized_and_needs_no_model(
    scope_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    tenant = scope_tenant
    entry = uuid.uuid4()

    first = await _start(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, entry_id=entry, external_conversation_id=THREAD_A, text=MESSY_FIRST_TEXT),
    )
    await _drain(first)
    row = await _read_conversation(first.conversation_id)
    assert row is not None
    assert row["title"] == EXPECTED_FIRST_TITLE
    assert len(row["title"]) == TITLE_LIMIT
    assert len(fake_resolve.calls) == 1, "标题不得额外调用定义解析"
    assert fake_resolve.model_calls == [], "标题不得调用模型（含摘要模型）"

    # 第二条消息不改写标题（首条为准）
    second = await _start(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, entry_id=entry, external_conversation_id=THREAD_A, text="换一个话题"),
    )
    await _drain(second)
    again = await _read_conversation(first.conversation_id)
    assert again is not None and again["title"] == EXPECTED_FIRST_TITLE

    # 纯附件首条消息：本地化占位（zh-CN 默认）
    attachment_only = await _start(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(
            tenant,
            entry_id=entry,
            external_conversation_id=THREAD_B,
            text="   ",
            attachments=[_attachment()],
        ),
    )
    await _drain(attachment_only)
    attachment_row = await _read_conversation(attachment_only.conversation_id)
    assert attachment_row is not None and attachment_row["title"] == "[附件]"

    # 空会话（无文本、无附件）：标题保持为空，读取层显示「新会话」
    empty = await _start(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, entry_id=entry, external_conversation_id="thread-empty", text=""),
    )
    await _drain(empty)
    empty_row = await _read_conversation(empty.conversation_id)
    assert empty_row is not None and empty_row["title"] is None
    assert display_title(empty_row["title"]) == "新会话"
    assert display_title(None) == "新会话"


async def test_b249_attachment_placeholder_follows_tenant_locale(
    scope_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    tenant = scope_tenant
    entry = uuid.uuid4()
    started = await _start(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(
            tenant,
            entry_id=entry,
            external_conversation_id=THREAD_A,
            text=" ",
            attachments=[_attachment()],
        ),
        settings_client=_ScopeSettings(default_locale="en-US"),
    )
    await _drain(started)
    row = await _read_conversation(started.conversation_id)
    assert row is not None and row["title"] == "[Attachment]"


# --------------------------------------------------------------------------------------
# B-249 断言 6：长期记忆继续按 (tenant,user) 共享
# --------------------------------------------------------------------------------------


async def test_b249_memory_scope_stays_shared_across_entries_and_conversations(
    scope_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    tenant = scope_tenant
    first_entry, second_entry = uuid.uuid4(), uuid.uuid4()
    first = await _start(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, entry_id=first_entry, external_conversation_id=THREAD_A, text="入口一"),
    )
    await _drain(first)
    second = await _start(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, entry_id=second_entry, external_conversation_id=THREAD_A, text="入口二"),
    )
    await _drain(second)
    assert first.conversation_id != second.conversation_id

    async with get_session_factory()() as session:
        first_scope = await MemoryService.lock_scope(
            session,
            MemoryWriteContext(
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                actor_type="USER",
                actor_id=str(tenant.platform_user_id),
                conversation_id=first.conversation_id,
            ),
        )
        second_scope = await MemoryService.lock_scope(
            session,
            MemoryWriteContext(
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                actor_type="USER",
                actor_id=str(tenant.platform_user_id),
                conversation_id=second.conversation_id,
            ),
        )
        other_user_scope = await MemoryService.lock_scope(
            session,
            MemoryWriteContext(
                tenant_id=tenant.tenant_id,
                user_id=uuid.uuid4(),
                actor_type="USER",
                actor_id="other-user",
                conversation_id=first.conversation_id,
            ),
        )
        first_scope_id, second_scope_id = first_scope.id, second_scope.id
        other_scope_id = other_user_scope.id
        await session.rollback()
    assert first_scope_id == second_scope_id, "跨入口/会话必须共享同一 (tenant,user) 记忆 scope"
    assert other_scope_id != first_scope_id
    assert set(MemoryScope.__table__.columns.keys()) == {
        "id", "is_deleted", "create_time", "update_time", "tenant_id", "user_id",
        "revision", "forget_epoch",
    }, "记忆 scope 不得长出会话/入口维度"


# --------------------------------------------------------------------------------------
# 入口键解析的显式边界
# --------------------------------------------------------------------------------------


def test_b249_entry_key_parsing_never_fabricates_an_entry_identity() -> None:
    entry = uuid.uuid4()
    assert entry_id_of(str(entry)) == entry
    assert entry_id_of("bot-wecom-string") is None
    assert entry_id_of("") is None
