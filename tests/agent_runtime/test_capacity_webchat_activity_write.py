"""B-263 / TASK-063：Run 完成和待确认的同事务用户通知（design §3.3/§3.6）。

真实边界（不 mock 存储或租约）：

- **隔离 PostgreSQL**（`async_tool_datastore`：每用例一个空库 + 迁移到 head，跑完 drop）：真实
  `runtime.channel_notification_feed` / `channel_notification` 行、真实完整唯一约束
  `(tenant_id,user_id,seq)` / `(tenant_id,delivery_key)`、真实行锁与真实终态事务；
- **真实 Runtime 业务服务**：`RunService.start/_finalize_run/cancel_run` 与
  `MemoryConfirmationService.propose/suspend` 全部走生产代码；执行器用进程内 fake（模型调用不在
  本契约的断言内），存储、租约、行锁、事件与 feed 水位都是真实组件；
- **真实故障注入**：`runtime.channel_notification` 上的 BEFORE INSERT 触发器（真实 DDL，只落在
  隔离库）让通知写入失败，验证终态/确认与通知**同事务**（通知写不进去 ⇒ 终态也不落库）。

关键断言（B-263）：

1. 终态（完成/失败/取消）与 feed seq/通知**同事务**：提交后 Run 行、feed 水位与通知行同时可见；
   注入通知写入失败时终态整体回滚（Run 仍 RUNNING、无终态事件、feed 水位不动、提交仍 OPEN）；
2. **feed 锁在 Conversation/Run 之后**：终态事务等 feed 行锁时**已经**持有 Run 行锁
   （第三条连接的 Run 行 UPDATE 被阻塞并超时）；
3. 待确认（interrupt/confirmation）走同一接缝：`kind=RUN_WAITING_INPUT`、delivery_key 绑定该
   中断身份、与 WAITING_INPUT/中断/事件同事务（同样的失败注入 ⇒ 全部回滚）；
4. 重复投递不增加未读：同 delivery_key 重写返回**原行/原 seq**，feed 水位与未读计数不变；
   隐藏（软删）通知保留唯一回执，重投不新发；
5. 隐藏会话（ARCHIVED）仍可发现：通知不依赖会话可见状态与当前订阅（socket）；
6. 不因 socket 写入就已读：终态事件被 SSE 读取路径重放后 `read_at` 仍为 NULL；通知不占用
   canonical seq（会话水位只被事件推进）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.channel_notifications import (
    DELIVERY_NOT_REQUIRED,
    KIND_RUN_COMPLETED,
    KIND_RUN_WAITING_INPUT,
    run_terminal_delivery_key,
    run_waiting_delivery_key,
    write_run_notification,
)
from muad_agent_runtime.application.executor import (
    ExecutorEvent,
    ExecutorFactory,
    ExecutorRequest,
    RunExecutor,
)
from muad_agent_runtime.application.memory_confirmation import MemoryConfirmationService
from muad_agent_runtime.application.ports import NullPlatformSettingsClient, PlatformSettingsSnapshot
from muad_agent_runtime.application.run_service import RunService, RunStart
from muad_agent_runtime.application.run_submission import RunSubmissionService
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    ChannelNotification,
    ChannelNotificationFeed,
    Conversation,
    RunInterrupt,
    RunRecord,
    RunSubmission,
)
from muad_contracts import (
    ChannelContext,
    MessageInput,
    ResolvedAgent,
    ResolveDefinitionResponse,
    ResolvedModel,
    RunRequest,
    RunStatus,
)
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from tests.agent_runtime.memory_confirmation_support import (
    candidate,
    complete_round,
)
from tests.agent_runtime.memory_confirmation_support import (
    service as confirmation_service,
)
from tests.agent_runtime.memory_confirmation_support import source as confirmation_source
from tests.async_tool_continuations import running_source

pytestmark = pytest.mark.integration

INSTANCE_ID = "activity-write"
ENTRY_ID = uuid.uuid4()
DRAIN_TIMEOUT_SEC = 30.0
WAIT_TIMEOUT_SEC = 15.0
MODEL_URL = "http://model.invalid/v1"


@dataclass(frozen=True, slots=True)
class ActivityTenant:
    tenant_id: str
    agent_id: uuid.UUID
    platform_user_id: uuid.UUID


# --------------------------------------------------------------------------------------
# 隔离 PostgreSQL / Redis + 真实 Runtime 进程环境
# --------------------------------------------------------------------------------------


@pytest.fixture
async def activity_tenant(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> AsyncIterator[ActivityTenant]:
    """隔离 PostgreSQL/Redis 号位 + Runtime 进程环境；跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", "http://127.0.0.1:9")
    await runtime_db.dispose_engine()
    try:
        yield ActivityTenant(
            tenant_id=f"activity-{uuid.uuid4().hex[:12]}",
            agent_id=uuid.uuid4(),
            platform_user_id=uuid.uuid4(),
        )
    finally:
        await runtime_db.dispose_engine()


@pytest.fixture
def activity_database_url(async_tool_datastore: dict[str, str]) -> str:
    """旁路连接的库地址（行锁持有者与探针各自独立后端）。"""
    return async_tool_datastore["database_url"]


def _resolved(tenant: ActivityTenant) -> ResolveDefinitionResponse:
    return ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=tenant.agent_id, key="activity-agent", revision=1, instructions="", runtime_config={}
        ),
        model=ResolvedModel(
            id=uuid.uuid4(),
            revision=1,
            model_id="probe",
            base_url=MODEL_URL,
            api_key="sk-probe",
            params={},
        ),
        skills=[],
        mcp_servers=[],
    )


class _Resolver:
    """定义解析替身：只回答本套件固定定义；模型调用不在断言内。"""

    def __init__(self, response: ResolveDefinitionResponse) -> None:
        self._response = response

    async def resolve(self, request: Any, *, tenant_id: str, trace_id: str = "") -> Any:
        return self._response

    async def resolve_model(self, request: Any, *, tenant_id: str, trace_id: str = "") -> Any:
        from muad_api import AppError
        from muad_api.error_codes import ErrorCode

        raise AppError(ErrorCode.COMMON_NOT_FOUND)


class _ActivitySettings:
    """设置源：足够大的准入容量（本契约不验证准入阈值边界）。"""

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=0,
            settings={
                "capacity": {"max_open_runs_per_tenant": 100_000, "max_open_runs_per_user": 100}
            },
        )


# --------------------------------------------------------------------------------------
# 执行器替身（确定性增量 / 可闸门 / 显式失败）与真实受理驱动
# --------------------------------------------------------------------------------------


class _Executor:
    """进程内执行器：闸门放开后产出确定性增量；`failure` 非空即按执行失败收尾。"""

    def __init__(
        self,
        request: ExecutorRequest,
        *,
        release: asyncio.Event | None = None,
        failure: Exception | None = None,
    ) -> None:
        self._request = request
        self._release = release
        self._failure = failure

    async def run(self) -> AsyncIterator[ExecutorEvent]:
        if self._release is not None:
            await self._release.wait()
        if self._failure is not None:
            raise self._failure
        yield ExecutorEvent(type="message.delta", data={"delta": "最终正文"})


def _executor_factory(
    *, release: asyncio.Event | None = None, failure: Exception | None = None
) -> ExecutorFactory:
    async def factory(request: ExecutorRequest) -> RunExecutor:
        return _Executor(request, release=release, failure=failure)

    return factory


def _request(
    tenant: ActivityTenant,
    conversation_id: uuid.UUID,
    *,
    entry: uuid.UUID | None = ENTRY_ID,
    text: str = "写一条消息",
) -> RunRequest:
    return RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        conversation_id=conversation_id,
        channel=ChannelContext(
            type="WEBCHAT",
            bot_id=str(entry) if entry is not None else "console-agent",
            external_user_id="web-1",
        ),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text=text),
    )


async def _seed_conversation(
    tenant: ActivityTenant, *, entry: uuid.UUID | None = ENTRY_ID, status: str = "ACTIVE"
) -> uuid.UUID:
    async with runtime_db.get_session_factory()() as session:
        conversation = Conversation(
            tenant_id=tenant.tenant_id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            title="活动通知",
            status=status,
            last_seq=0,
            channel_account_id=entry,
            external_thread_id="web-1",
            last_message_at=datetime.now(UTC),
        )
        session.add(conversation)
        await session.commit()
        return conversation.id


async def _seed_created_run(tenant: ActivityTenant, conversation_id: uuid.UUID) -> RunRecord:
    """未领取的 CREATED Run（取消路径的真实行事实；与 `RunService._create_run` 同形）。"""
    now = datetime.now(UTC)
    run = RunRecord(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        status=RunStatus.CREATED,
        input_text="seed",
        trace_id="activity-seed",
        start_time=now,
        deadline_at=now + timedelta(seconds=600),
    )
    async with runtime_db.get_session_factory()() as session:
        session.add(run)
        await session.commit()
    return run


@asynccontextmanager
async def _service(
    tenant: ActivityTenant,
    supervisor: ExecutionSupervisor,
    *,
    executor_factory: ExecutorFactory | None = None,
    resolver: Any | None = None,
) -> AsyncIterator[RunService]:
    """真实 RunService 绑定一条显式生命周期的事务会话（用后即关，不泄漏连接）。"""
    session = runtime_db.get_session_factory()()
    try:
        yield RunService(
            session,
            resolver if resolver is not None else _Resolver(_resolved(tenant)),
            INSTANCE_ID,
            _ActivitySettings(),
            executor_factory=executor_factory or _executor_factory(),
            supervisor=supervisor,
            credentials_client=None,
        )
    finally:
        await session.close()


async def _start(
    tenant: ActivityTenant,
    conversation_id: uuid.UUID,
    supervisor: ExecutionSupervisor,
    *,
    executor_factory: ExecutorFactory,
    entry: uuid.UUID | None = ENTRY_ID,
) -> RunStart:
    async with _service(tenant, supervisor, executor_factory=executor_factory) as service:
        return await service.start(
            _request(tenant, conversation_id, entry=entry),
            tenant.tenant_id,
            idempotency_key=f"key-{uuid.uuid4()}",
        )


async def _drain(start: RunStart) -> list[ExecutorEvent]:
    events: list[ExecutorEvent] = []

    async def collect() -> None:
        async for event in start.events:
            events.append(event)

    await asyncio.wait_for(collect(), timeout=DRAIN_TIMEOUT_SEC)
    return events


# --------------------------------------------------------------------------------------
# 真实行事实读取与故障注入
# --------------------------------------------------------------------------------------


async def _run_row(run_id: uuid.UUID) -> RunRecord:
    async with runtime_db.get_session_factory()() as session:
        row = await session.get(RunRecord, run_id)
        assert row is not None
        return row


async def _notifications(tenant_id: str, user_id: uuid.UUID) -> list[ChannelNotification]:
    async with runtime_db.get_session_factory()() as session:
        return list(
            await session.scalars(
                sa.select(ChannelNotification)
                .where(
                    ChannelNotification.tenant_id == tenant_id,
                    ChannelNotification.user_id == user_id,
                )
                .order_by(ChannelNotification.seq)
            )
        )


async def _feed(tenant_id: str, user_id: uuid.UUID) -> ChannelNotificationFeed | None:
    async with runtime_db.get_session_factory()() as session:
        return await session.scalar(
            sa.select(ChannelNotificationFeed).where(
                ChannelNotificationFeed.tenant_id == tenant_id,
                ChannelNotificationFeed.user_id == user_id,
            )
        )


async def _unread_count(tenant_id: str, user_id: uuid.UUID) -> int:
    async with runtime_db.get_session_factory()() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ChannelNotification)
            .where(
                ChannelNotification.tenant_id == tenant_id,
                ChannelNotification.user_id == user_id,
                ChannelNotification.read_at.is_(None),
            )
        )
    return int(count or 0)


async def _terminal_events(run_id: uuid.UUID) -> list[CanonicalEvent]:
    async with runtime_db.get_session_factory()() as session:
        return list(
            await session.scalars(
                sa.select(CanonicalEvent)
                .where(
                    CanonicalEvent.run_id == run_id,
                    CanonicalEvent.stream_type.in_(("run.completed", "run.failed")),
                )
                .order_by(CanonicalEvent.seq)
            )
        )


async def _fail_notification_inserts() -> None:
    """真实 DDL（隔离库）：通知行 INSERT 一律失败，用于同事务回滚证明。"""
    async with runtime_db.get_session_factory()() as session:
        await session.execute(
            sa.text(
                "CREATE FUNCTION runtime.fail_activity_notification() RETURNS trigger"
                " LANGUAGE plpgsql AS $$ BEGIN IF NEW.delivery_key LIKE 'run:%' THEN"
                " RAISE EXCEPTION 'activity_notification_failure'; END IF;"
                " RETURN NEW; END $$"
            )
        )
        await session.execute(
            sa.text(
                "CREATE TRIGGER fail_activity_notification BEFORE INSERT"
                " ON runtime.channel_notification FOR EACH ROW"
                " EXECUTE FUNCTION runtime.fail_activity_notification()"
            )
        )
        await session.commit()


async def _seed_feed(tenant: ActivityTenant, *, last_seq: int = 0) -> uuid.UUID:
    async with runtime_db.get_session_factory()() as session:
        feed = ChannelNotificationFeed(
            tenant_id=tenant.tenant_id, user_id=tenant.platform_user_id, last_seq=last_seq
        )
        session.add(feed)
        await session.commit()
        return feed.id


async def _entry_attributed(factory: async_sessionmaker[Any], conversation_id: uuid.UUID) -> None:
    """把真实会话归因到网页入口（通知的 `entry_id` 身份来自真实行事实）。"""
    async with factory.begin() as session:
        await session.execute(
            sa.text("UPDATE runtime.conversation SET channel_account_id = :entry WHERE id = :id"),
            {"entry": ENTRY_ID, "id": conversation_id},
        )


# --------------------------------------------------------------------------------------
# 终态：完成 / 失败 / 取消
# --------------------------------------------------------------------------------------


async def test_b263_completed_run_notifies_in_terminal_transaction(
    activity_tenant: ActivityTenant,
):
    """终态行、feed 水位与通知行同一事务提交；通知不占用 canonical seq。"""
    tenant = activity_tenant
    supervisor = ExecutionSupervisor()
    conversation_id = await _seed_conversation(tenant)
    started = await _start(
        tenant, conversation_id, supervisor, executor_factory=_executor_factory()
    )
    events = await _drain(started)
    assert events[-1].type == "run.completed"

    run = await _run_row(started.run_id)
    assert run.status == RunStatus.COMPLETED
    assert run.lease_owner is None and run.lease_until is None

    rows = await _notifications(tenant.tenant_id, tenant.platform_user_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.run_id == started.run_id and row.conversation_id == conversation_id
    assert row.entry_id == ENTRY_ID and row.user_id == tenant.platform_user_id
    assert row.kind == KIND_RUN_COMPLETED
    assert row.delivery_key == run_terminal_delivery_key(started.run_id)
    assert row.delivery_state == DELIVERY_NOT_REQUIRED
    assert row.read_at is None, "入箱写入不得代替用户已读"
    assert row.artifact_ids == [] and row.task_id is None

    feed = await _feed(tenant.tenant_id, tenant.platform_user_id)
    assert feed is not None and feed.last_seq == 1
    assert row.seq == feed.last_seq, "通知 seq 与 feed 水位同事务分配"
    assert await _unread_count(tenant.tenant_id, tenant.platform_user_id) == 1

    terminal = await _terminal_events(started.run_id)
    assert [str(event.stream_type) for event in terminal] == ["run.completed"]
    assert row.event_id == terminal[0].id, "通知引用产生它的终态事件"
    async with runtime_db.get_session_factory()() as session:
        last_seq = await session.scalar(
            sa.select(Conversation.last_seq).where(Conversation.id == conversation_id)
        )
    assert last_seq == terminal[0].seq, "通知不分配 canonical seq（会话水位只被事件推进）"


async def test_b263_failed_run_notifies_terminal(activity_tenant: ActivityTenant):
    """执行失败同样写终态通知（失败/取消与完成共用稳定终态交付键）。"""
    tenant = activity_tenant
    supervisor = ExecutionSupervisor()
    conversation_id = await _seed_conversation(tenant)
    started = await _start(
        tenant,
        conversation_id,
        supervisor,
        executor_factory=_executor_factory(failure=RuntimeError("model exploded")),
    )
    events = await _drain(started)
    assert events[-1].type == "run.failed"

    assert (await _run_row(started.run_id)).status == RunStatus.FAILED
    rows = await _notifications(tenant.tenant_id, tenant.platform_user_id)
    assert len(rows) == 1
    assert rows[0].kind == KIND_RUN_COMPLETED
    assert rows[0].delivery_key == run_terminal_delivery_key(started.run_id)
    assert rows[0].read_at is None


async def test_b263_cancelled_run_notifies_terminal(activity_tenant: ActivityTenant):
    """未开始执行的取消：CAS 终态、取消事件与通知同一事务。"""
    tenant = activity_tenant
    supervisor = ExecutionSupervisor()
    conversation_id = await _seed_conversation(tenant)
    run = await _seed_created_run(tenant, conversation_id)
    async with _service(tenant, supervisor) as service:
        cancelled = await service.cancel_run(run.id, tenant.tenant_id)
    assert cancelled.status == RunStatus.CANCELLED

    rows = await _notifications(tenant.tenant_id, tenant.platform_user_id)
    assert len(rows) == 1
    assert rows[0].run_id == run.id and rows[0].kind == KIND_RUN_COMPLETED
    assert rows[0].delivery_key == run_terminal_delivery_key(run.id)
    assert rows[0].read_at is None
    terminal = await _terminal_events(run.id)
    assert [str(event.payload_json["status"]) for event in terminal] == ["CANCELLED"]
    assert rows[0].event_id == terminal[0].id


async def test_b263_notification_failure_rolls_back_terminal_state(
    activity_tenant: ActivityTenant,
):
    """通知写不进去 ⇒ 终态整体回滚：Run 仍 RUNNING、无终态事件、feed 水位不动、提交仍 OPEN。"""
    tenant = activity_tenant
    factory, _context, _skill, run, identity = await running_source(runtime_db.get_engine())
    await _entry_attributed(factory, run.conversation_id)
    async with factory.begin() as session:
        submission = await RunSubmissionService(lambda: factory).record_in(
            session,
            tenant_id=run.tenant_id,
            idempotency_key=str(uuid.uuid4()),
            endpoint="create-run",
            actor_user_id=run.user_id,
            run_id=run.id,
            conversation_id=run.conversation_id,
            request_fingerprint="sha256:" + "a" * 64,
        )
    await _fail_notification_inserts()

    async with factory() as session:
        source = await session.get(RunRecord, run.id)
        assert source is not None
        service = RunService(
            session,
            _Resolver(_resolved(tenant)),
            identity.owner,
            NullPlatformSettingsClient(),
            supervisor=ExecutionSupervisor(),
        )
        with pytest.raises(sa.exc.DBAPIError):
            await service._finalize_run(source, submission.id, "done", agent_key="activity")

    saved = await _run_row(run.id)
    assert saved.status == RunStatus.RUNNING, "终态不得先于通知落库"
    assert saved.lease_owner == identity.owner and saved.end_time is None
    assert await _terminal_events(run.id) == []
    assert await _notifications(run.tenant_id, run.user_id) == []
    assert await _feed(run.tenant_id, run.user_id) is None
    async with factory() as session:
        opened = await session.get(RunSubmission, submission.id)
        assert opened is not None and opened.status == "OPEN"


async def _wait_for_lock_wait(engine: AsyncEngine, holder_pid: int) -> int:
    """等到某个后端在等 holder 的行锁（终态事务已进入 feed 锁等待）。"""
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    while time.monotonic() < deadline:
        async with engine.connect() as connection:
            pid = await connection.scalar(
                sa.text(
                    "SELECT pid FROM pg_stat_activity"
                    " WHERE datname = current_database() AND pid <> :holder"
                    " AND wait_event_type = 'Lock' AND :holder = ANY(pg_blocking_pids(pid))"
                    " LIMIT 1"
                ),
                {"holder": holder_pid},
            )
        if pid is not None:
            return int(pid)
        await asyncio.sleep(0.05)
    raise AssertionError("终态事务从未在 feed 行锁上等待")


async def test_b263_feed_lock_is_acquired_after_run_lock(
    activity_tenant: ActivityTenant, activity_database_url: str
):
    """终态事务等 feed 行锁时已持有 Run 行锁：第三条连接的 Run 行 UPDATE 被阻塞。"""
    tenant = activity_tenant
    supervisor = ExecutionSupervisor()
    conversation_id = await _seed_conversation(tenant)
    await _seed_feed(tenant)
    release = asyncio.Event()
    started = await _start(
        tenant, conversation_id, supervisor, executor_factory=_executor_factory(release=release)
    )

    side = create_async_engine(activity_database_url)
    try:
        async with side.connect() as holder:
            holder_pid = int(await holder.scalar(sa.text("SELECT pg_backend_pid()")))
            locked = await holder.scalar(
                sa.text(
                    "SELECT id FROM runtime.channel_notification_feed"
                    " WHERE tenant_id = :tenant AND user_id = :user FOR UPDATE"
                ),
                {"tenant": tenant.tenant_id, "user": tenant.platform_user_id},
            )
            assert locked is not None
            release.set()
            waiting_pid = await _wait_for_lock_wait(side, holder_pid)
            assert waiting_pid != holder_pid

            async with side.connect() as probe:
                await probe.execute(sa.text("SET LOCAL statement_timeout = '400ms'"))
                with pytest.raises(sa.exc.DBAPIError) as raised:
                    await probe.execute(
                        sa.text(
                            "UPDATE runtime.run_record SET update_time = update_time"
                            " WHERE id = :run"
                        ),
                        {"run": started.run_id},
                    )
                assert getattr(raised.value.orig, "sqlstate", None) == "57014", (
                    "终态事务必须在等待 feed 锁时仍持有 Run 行锁（锁序 Run → feed）"
                )
            await holder.rollback()

        row = await _wait_for_notification(
            tenant.tenant_id, tenant.platform_user_id, started.run_id
        )
        assert row.delivery_key == run_terminal_delivery_key(started.run_id)
        assert (await _run_row(started.run_id)).status == RunStatus.COMPLETED
        async with side.connect() as control:
            await control.execute(
                sa.text("UPDATE runtime.run_record SET update_time = update_time WHERE id = :run"),
                {"run": started.run_id},
            )
            await control.rollback()
    finally:
        release.set()
        await side.dispose()


async def _wait_for_notification(
    tenant_id: str, user_id: uuid.UUID, run_id: uuid.UUID
) -> ChannelNotification:
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    while time.monotonic() < deadline:
        rows = [row for row in await _notifications(tenant_id, user_id) if row.run_id == run_id]
        if rows:
            return rows[0]
        await asyncio.sleep(0.05)
    raise AssertionError("终态通知未在期限内落库")


# --------------------------------------------------------------------------------------
# 待确认（interrupt/confirmation）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StagedConfirmation:
    factory: Any
    run: RunRecord
    identity: Any
    memory: MemoryConfirmationService


async def _staged_confirmation(tenant: ActivityTenant) -> StagedConfirmation:
    """真实待确认暂存：真实 proposal（含真实 submission/事件）已就绪，等待 `suspend` 事务。"""
    factory, run, identity, submission_id = await confirmation_source(
        runtime_db.get_engine(), tenant=tenant.tenant_id, user=tenant.platform_user_id
    )
    await _entry_attributed(factory, run.conversation_id)
    memory = confirmation_service(factory, identity, submission_id)
    await memory.propose(candidate(), call_id="remember-1")
    await complete_round(factory, run)
    return StagedConfirmation(factory=factory, run=run, identity=identity, memory=memory)


async def test_b263_waiting_input_notifies_in_pause_transaction(activity_tenant: ActivityTenant):
    """待确认与通知同事务：WAITING_INPUT、中断、确认事件与通知行同时可见。"""
    tenant = activity_tenant
    staged = await _staged_confirmation(tenant)
    factory, run = staged.factory, staged.run
    await staged.memory.suspend(RunnerCheckpoint(turns=1, tool_calls=1))

    async with factory() as session:
        current = await session.get(RunRecord, run.id)
        assert current is not None and current.status == RunStatus.WAITING_INPUT
        interrupt = await session.scalar(sa.select(RunInterrupt).where(RunInterrupt.run_id == run.id))
        assert interrupt is not None and interrupt.status == "WAITING"

    rows = await _notifications(tenant.tenant_id, tenant.platform_user_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == KIND_RUN_WAITING_INPUT
    assert row.run_id == run.id and row.conversation_id == run.conversation_id
    assert row.delivery_key == run_waiting_delivery_key(run.id, interrupt.id)
    assert row.delivery_state == DELIVERY_NOT_REQUIRED and row.read_at is None
    feed = await _feed(tenant.tenant_id, tenant.platform_user_id)
    assert feed is not None and feed.last_seq == 1 and row.seq == 1
    async with factory() as session:
        event = await session.scalar(
            sa.select(CanonicalEvent).where(
                CanonicalEvent.run_id == run.id,
                CanonicalEvent.event_type == "MEMORY_CONFIRMATION_REQUIRED",
            )
        )
        assert event is not None and row.event_id == event.id


async def test_b263_waiting_input_notification_failure_rolls_back_pause(
    activity_tenant: ActivityTenant,
):
    """通知写不进去 ⇒ 待确认挂起整体回滚：Run 仍 RUNNING、无中断、无通知、无 feed。"""
    from muad_api import AppError

    tenant = activity_tenant
    staged = await _staged_confirmation(tenant)
    factory, run, identity = staged.factory, staged.run, staged.identity
    await _fail_notification_inserts()

    with pytest.raises(AppError):
        await staged.memory.suspend(RunnerCheckpoint(turns=1, tool_calls=1))

    async with factory() as session:
        current = await session.get(RunRecord, run.id)
        assert current is not None and current.status == RunStatus.RUNNING
        assert current.lease_owner == identity.owner
        interrupts = list(
            await session.scalars(sa.select(RunInterrupt).where(RunInterrupt.run_id == run.id))
        )
        assert interrupts == []
        assert (
            await session.scalar(
                sa.select(CanonicalEvent.id).where(
                    CanonicalEvent.run_id == run.id,
                    CanonicalEvent.event_type == "MEMORY_CONFIRMATION_REQUIRED",
                )
            )
            is None
        )
    assert await _notifications(run.tenant_id, run.user_id) == []
    assert await _feed(run.tenant_id, run.user_id) is None


# --------------------------------------------------------------------------------------
# 重复投递、隐藏通知、隐藏会话、无入口会话、已读独立
# --------------------------------------------------------------------------------------


async def test_b263_duplicate_delivery_reuses_receipt_without_new_unread(
    activity_tenant: ActivityTenant,
):
    """同 delivery_key 重投（崩溃重放/补投）返回原行原 seq：不新占 seq、不新增未读。"""
    factory, _context, _skill, run, _identity = await running_source(runtime_db.get_engine())
    await _entry_attributed(factory, run.conversation_id)
    key = run_terminal_delivery_key(run.id)

    async with factory() as session:
        source = await session.get(RunRecord, run.id)
        assert source is not None
        first = await write_run_notification(
            session, run=source, kind=KIND_RUN_COMPLETED, delivery_key=key
        )
        await session.commit()
    assert first is not None and first.created is True

    async with factory() as session:
        source = await session.get(RunRecord, run.id)
        assert source is not None
        replay = await write_run_notification(
            session, run=source, kind=KIND_RUN_COMPLETED, delivery_key=key
        )
        await session.commit()
    assert replay is not None and replay.created is False
    assert replay.id == first.id and replay.seq == first.seq

    feed = await _feed(run.tenant_id, run.user_id)
    assert feed is not None and feed.last_seq == 1
    assert await _unread_count(run.tenant_id, run.user_id) == 1
    assert len(await _notifications(run.tenant_id, run.user_id)) == 1

    async with factory.begin() as session:
        await session.execute(
            sa.text("UPDATE runtime.channel_notification SET is_deleted = true WHERE id = :id"),
            {"id": first.id},
        )
    async with factory() as session:
        source = await session.get(RunRecord, run.id)
        assert source is not None
        hidden_replay = await write_run_notification(
            session, run=source, kind=KIND_RUN_COMPLETED, delivery_key=key
        )
        await session.commit()
    assert hidden_replay is not None and hidden_replay.created is False
    assert hidden_replay.id == first.id and hidden_replay.seq == first.seq
    assert len(await _notifications(run.tenant_id, run.user_id)) == 1, "隐藏通知不得放回唯一回执"
    feed = await _feed(run.tenant_id, run.user_id)
    assert feed is not None and feed.last_seq == 1


async def test_b263_archived_conversation_notification_stays_discoverable(
    activity_tenant: ActivityTenant,
):
    """隐藏（ARCHIVED）会话的终态仍进收件箱：未读扫描按用户发现，不依赖会话可见状态。"""
    tenant = activity_tenant
    supervisor = ExecutionSupervisor()
    conversation_id = await _seed_conversation(tenant, status="ARCHIVED")
    started = await _start(
        tenant, conversation_id, supervisor, executor_factory=_executor_factory()
    )
    # 继续历史会话会恢复 ACTIVE（会话上限语义）；本用例验证的是「终态写入时会话已归档
    # 也照样进收件箱」，因此在执行结束前重新归档，保持被验证的事实。
    async with runtime_db.get_session_factory()() as session:
        await session.execute(
            sa.update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(status="ARCHIVED")
        )
        await session.commit()
    await _drain(started)

    async with runtime_db.get_session_factory()() as session:
        status = await session.scalar(
            sa.select(Conversation.status).where(Conversation.id == conversation_id)
        )
    assert status == "ARCHIVED"
    rows = await _notifications(tenant.tenant_id, tenant.platform_user_id)
    assert [row.run_id for row in rows] == [started.run_id]
    assert await _unread_count(tenant.tenant_id, tenant.platform_user_id) == 1
    assert rows[0].read_at is None


async def test_b263_socket_replay_does_not_mark_read(activity_tenant: ActivityTenant):
    """终态事件被 SSE 读取路径重放后通知仍未读：socket 写入不等于用户已读。"""
    tenant = activity_tenant
    supervisor = ExecutionSupervisor()
    conversation_id = await _seed_conversation(tenant)
    started = await _start(
        tenant, conversation_id, supervisor, executor_factory=_executor_factory()
    )
    await _drain(started)

    async with _service(tenant, supervisor) as service:
        replay = await service.tail_run_events(started.run_id, tenant.tenant_id)
    events = await _drain(replay)
    assert events[-1].type == "run.completed"

    rows = await _notifications(tenant.tenant_id, tenant.platform_user_id)
    assert len(rows) == 1 and rows[0].read_at is None
    assert await _unread_count(tenant.tenant_id, tenant.platform_user_id) == 1


async def test_b263_conversation_without_entry_has_no_inbox_row(
    activity_tenant: ActivityTenant,
):
    """无入口归属的会话不进网页收件箱：entry_id 是 DTO 必填身份，不编造 UUID。"""
    tenant = activity_tenant
    supervisor = ExecutionSupervisor()
    conversation_id = await _seed_conversation(tenant, entry=None)
    started = await _start(
        tenant, conversation_id, supervisor, executor_factory=_executor_factory(), entry=None
    )
    events = await _drain(started)
    assert events[-1].type == "run.completed"
    assert (await _run_row(started.run_id)).status == RunStatus.COMPLETED
    assert await _notifications(tenant.tenant_id, tenant.platform_user_id) == []
    assert await _feed(tenant.tenant_id, tenant.platform_user_id) is None
