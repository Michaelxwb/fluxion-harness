"""B-236：持久排队阶段与两入口状态投影（design §3.2 架构与执行边界 / §3.4 接口设计）。

真实边界（不 mock 存储或租约）：

- 真实 Runtime 业务路径：`RunService` 受理/取消、`claim_continuation` 领取、`try_wait` 工具等待、
  `PooledModelProvider`/`ModelAllocator` 模型等待与恢复、`expire_queued_runs` 排队过期；
- 隔离租户的真实 PostgreSQL：`runtime.run_record`/`canonical_event`/`runtime_snapshot`/
  `run_submission`/`conversation`/`run_admission_*`/`model_capacity` 与 `control.model_*`
  （只增删本租户行，不动共享库其它数据）；
- 真实 Console 应用（uvicorn + 真实 socket + 真实服务身份门控）提供 API-09 冻结引用解析；
- 真实模型 HTTP 探针（uvicorn + 真实 socket）：记录每次上游请求的型号/鉴权/正文。

关键断言（B-236 契约）：

1. WAITING_RUNTIME 随受理事务持久化（Run 行 + `run.stage` 事件同事务；被拒受理不留阶段事件）；
2. 领取结束排队：同一事务清排队投影并写 `RUNNING` 阶段事件（行锁阻塞期间两者都不可见）；
3. WAITING_MODEL 在等待**期间**已持久化：原因消费 `MODEL_CAPACITY_UNAVAILABLE.data.reason`，
   期限 = min(冻结等待预算, Run 绝对期限)，不输出预计时间/名次；额度可用后回到 RUNNING；
4. 工具阶段 WAITING_TOOL 同样持久化；恢复（新连接/新会话）后投影来自持久事实，
   与阶段事件载荷逐字段一致（Console API-15 与 Gateway API-07 同一事实、同一词表）；
5. 终态/取消后投影收敛（排队投影清空、阶段=终态、终态事件与行投影同一词表）；
6. 阶段文案不进入助手正文/下一轮历史（真实上游请求与真实上下文重建都没有阶段文案）。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import socket
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from muad_agent_core.agent.continuation import RunnerCheckpoint, WaitDecision
from muad_agent_runtime.application.async_tools.continuation_service import (
    ExecutionIdentity,
    claim_continuation,
    try_wait,
)
from muad_agent_runtime.application.async_tools.operations import reserve_operation
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.context_builder import (
    HISTORY_EVENT_TYPES,
    BudgetPolicy,
    DbBackedContextBuilder,
)
from muad_agent_runtime.application.execution_status import (
    persist_model_running,
    persist_model_wait,
    project_execution_status,
)
from muad_agent_runtime.application.executor import (
    ExecutorEvent,
    ExecutorFactory,
    ExecutorRequest,
    RunExecutor,
)
from muad_agent_runtime.application.ports import (
    NullPlatformSettingsClient,
    PlatformSettingsSnapshot,
)
from muad_agent_runtime.application.run_service import (
    RunService,
    RunStart,
    default_executor_factory,
    snapshot_policy,
)
from muad_agent_runtime.application.task_client import TaskSubmissionContext
from muad_agent_runtime.infrastructure.console_client import ConsoleCredentialsClient
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Conversation,
    RunRecord,
    RunSubmission,
    RuntimeSnapshot,
)
from muad_api import AppError
from muad_common import SharedSettings
from muad_contracts import (
    ChannelContext,
    CompletionMode,
    MessageInput,
    ResolvedAgent,
    ResolveDefinitionResponse,
    ResolvedModel,
    ResolvedSkill,
    RunRequest,
    RunStatus,
    SkillExecutionMode,
)
from muad_contracts.model_pool import derive_model_key_resource_id
from muad_contracts.webchat import WebchatActiveRun, WebchatExecutionStatus
from muad_contracts.webchat_events import WebchatStageEvent, parse_public_event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agent_runtime.conftest import CLEANUP_ORDER, FakeResolveClient, TenantContext

pytestmark = pytest.mark.integration

INSTANCE_ID = "runtime-b236"
PROVIDER_KEY = "openai"
MODEL_NAME = "gpt-4o-mini"
AGENT_KEY = "b236-agent"
WAIT_TIMEOUT_SEC = 30.0
LOCK_WAIT_TIMEOUT_SEC = 15.0
LISTEN_TIMEOUT_SEC = 15.0
RUN_QUEUE_TIMEOUT = "RUN_QUEUE_TIMEOUT"
QUEUE_STAGE_WAITING_RUNTIME = "WAITING_RUNTIME"
QUEUE_STAGE_WAITING_MODEL = "WAITING_MODEL"
STAGE_STREAM_TYPE = "run.stage"
#: 阶段载荷的全部字段（契约 `WebchatExecutionStatus`；多一个就是"预计时间/名次"式臆造）。
STAGE_PAYLOAD_KEYS = {"stage", "reason", "queued_at", "deadline_at", "started_at"}

# --------------------------------------------------------------------------------------
# 租户隔离与清理（只清本租户行，不动共享 dev 库其它数据）
# --------------------------------------------------------------------------------------

_PURGE_EXTRA_TABLES = (
    "run_admission_policy",
    "run_admission_counter",
    "model_invocation_audit",
    "model_dispatch_state",
    "model_call_lease",
    "model_capacity",
)
_CONTROL_TABLES = ("model_deployment", "model_credential", "model_definition")


async def _purge_tenant(tenant_id: str) -> None:
    # 先删引用 Run 的模型/准入行，再按既有清理顺序删 Run 及其上游行（外键顺序）。
    async with get_session_factory()() as session:
        for table in (*_PURGE_EXTRA_TABLES, *CLEANUP_ORDER):
            await session.execute(
                sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        for table in _CONTROL_TABLES:
            await session.execute(
                sa.text(f"DELETE FROM control.{table} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        await session.execute(
            sa.text("DELETE FROM control.platform_setting WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await session.commit()


@pytest.fixture
async def projection_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    try:
        yield tenant
    finally:
        await _purge_tenant(tenant.tenant_id)


# --------------------------------------------------------------------------------------
# 真实 PG 事实读取（证据）
# --------------------------------------------------------------------------------------


async def _read_run(tenant_id: str, run_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT status, queue_stage, queue_reason, queued_at, queue_deadline_at,"
                    " deadline_at, start_time, update_time, lease_owner, execution_epoch,"
                    " admission_released_at, error_code, conversation_id, cancel_requested"
                    " FROM runtime.run_record WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": tenant_id, "id": run_id},
            )
        ).mappings().one()
    return dict(row)


async def _run_record(tenant_id: str, run_id: uuid.UUID) -> RunRecord:
    """真实 ORM 行（阶段投影的输入就是这一行；读后 expunge，不依赖会话后续状态）。"""
    async with get_session_factory()() as session:
        row = await session.scalar(
            sa.select(RunRecord).where(
                RunRecord.id == run_id, RunRecord.tenant_id == tenant_id
            )
        )
        assert row is not None, f"run {run_id} must exist"
        session.expunge(row)
        return row


async def _stage_events(tenant_id: str, run_id: uuid.UUID) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT id, seq, conversation_id, payload_json, create_time"
                    " FROM runtime.canonical_event"
                    " WHERE tenant_id = :tenant_id AND run_id = :id AND stream_type = :stream"
                    " ORDER BY seq"
                ),
                {"tenant_id": tenant_id, "id": run_id, "stream": STAGE_STREAM_TYPE},
            )
        ).mappings().all()
    return [dict(row) for row in rows]


async def _stream_events(tenant_id: str, run_id: uuid.UUID) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT event_type, stream_type, payload_json, create_time"
                    " FROM runtime.canonical_event"
                    " WHERE tenant_id = :tenant_id AND run_id = :id AND stream_type IS NOT NULL"
                    " ORDER BY seq"
                ),
                {"tenant_id": tenant_id, "id": run_id},
            )
        ).mappings().all()
    return [dict(row) for row in rows]


async def _count_run_rows(tenant_id: str) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                sa.text("SELECT count(*) FROM runtime.run_record WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        )


async def _count_stage_events(tenant_id: str) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                sa.text(
                    "SELECT count(*) FROM runtime.canonical_event"
                    " WHERE tenant_id = :tenant_id AND stream_type = :stream"
                ),
                {"tenant_id": tenant_id, "stream": STAGE_STREAM_TYPE},
            )
        )


async def _count_conversations(tenant_id: str) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                sa.text("SELECT count(*) FROM runtime.conversation WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        )


async def _seed_counter(
    tenant_id: str, *, scope_type: str, scope_key: str, count: int
) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.run_admission_counter"
                " (tenant_id, scope_type, scope_key, open_run_count)"
                " VALUES (:tenant_id, :scope_type, :scope_key, :count)"
                " ON CONFLICT (tenant_id, scope_type, scope_key)"
                " DO UPDATE SET open_run_count = EXCLUDED.open_run_count"
            ).bindparams(
                tenant_id=tenant_id, scope_type=scope_type, scope_key=scope_key, count=count
            )
        )
        await session.commit()


# --------------------------------------------------------------------------------------
# 等待与锁观测
# --------------------------------------------------------------------------------------


async def _wait_for_status(
    tenant_id: str, run_id: uuid.UUID, statuses: tuple[str, ...], *, timeout: float = WAIT_TIMEOUT_SEC
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    observed: dict[str, Any] = {}
    while time.monotonic() < deadline:
        observed = await _read_run(tenant_id, run_id)
        if observed["status"] in statuses:
            return observed
        await asyncio.sleep(0.05)
    raise AssertionError(f"超时未到达 {statuses}：最后观测={observed}")


async def _wait_for_queue_stage(
    tenant_id: str, run_id: uuid.UUID, stage: str, *, timeout: float = WAIT_TIMEOUT_SEC
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    observed: dict[str, Any] = {}
    while time.monotonic() < deadline:
        observed = await _read_run(tenant_id, run_id)
        if observed["queue_stage"] == stage:
            return observed
        await asyncio.sleep(0.05)
    raise AssertionError(f"超时未观测到排队阶段 {stage}：最后观测={observed}")


@contextlib.asynccontextmanager
async def _held_row_lock(engine: AsyncEngine, statement: str, **params: Any) -> AsyncIterator[None]:
    """在独立连接上持有行锁直到退出（退出即回滚释放），用于制造真实锁等待。"""
    connection = await engine.connect()
    transaction = await connection.begin()
    try:
        await connection.execute(sa.text(statement).bindparams(**params))
        yield
    finally:
        with contextlib.suppress(Exception):
            await transaction.rollback()
        await connection.close()


async def _wait_for_lock_waiters(engine: AsyncEngine, *, like: str, count: int = 1) -> None:
    """等到至少 `count` 个会话真实阻塞在行锁上（`pg_stat_activity` 的 Lock 等待）。"""
    deadline = time.monotonic() + LOCK_WAIT_TIMEOUT_SEC
    observed: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    sa.text(
                        "SELECT a.pid, a.state, a.wait_event_type, a.query"
                        " FROM pg_stat_activity a"
                        " WHERE a.state = 'active' AND a.wait_event_type = 'Lock'"
                        " AND a.query LIKE :like"
                    ).bindparams(like=like)
                )
            ).mappings().all()
        observed = [dict(row) for row in rows]
        if len(observed) >= count:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"超时未观察到 {count} 个会话在 {like} 上等待：最后观测={observed}")


# --------------------------------------------------------------------------------------
# 两入口投影一致性断言（同一事实、同一词表）
# --------------------------------------------------------------------------------------


async def _assert_entries_agree(
    tenant_id: str, run_id: uuid.UUID, *, expect_stage: str, expect_reason: str | None = None
) -> WebchatExecutionStatus:
    """Console（API-15 active_run.execution_status）与 Gateway（API-07 run.stage 事件）同一投影。"""
    record = await _run_record(tenant_id, run_id)
    projected = project_execution_status(record)
    assert projected.stage == expect_stage, projected
    if expect_reason is not None:
        assert projected.reason == expect_reason, projected
    events = await _stage_events(tenant_id, run_id)
    assert events, "阶段事件必须已持久化"
    latest = events[-1]
    assert set(latest["payload_json"]) <= STAGE_PAYLOAD_KEYS, latest["payload_json"]
    from_events = WebchatExecutionStatus.model_validate(latest["payload_json"])
    assert from_events == projected, (from_events, projected)
    public = parse_public_event(
        {
            "type": STAGE_STREAM_TYPE,
            "event_id": str(latest["id"]),
            "conversation_id": str(latest["conversation_id"]),
            "run_id": str(run_id),
            "seq": latest["seq"],
            "timestamp": latest["create_time"].isoformat(),
            "data": latest["payload_json"],
        }
    )
    assert isinstance(public, WebchatStageEvent), public
    assert public.data == projected
    return projected


async def _assert_terminal_convergence(
    tenant_id: str, run_id: uuid.UUID, *, expect_stage: str
) -> WebchatExecutionStatus:
    """终态收敛：行投影 = 终态、排队投影清空；终态事件与行投影同一词表（Gateway 入口）。"""
    record = await _run_record(tenant_id, run_id)
    projected = project_execution_status(record)
    assert projected.stage == expect_stage, projected
    assert projected.queued_at is None and projected.deadline_at is None, projected
    streamed = await _stream_events(tenant_id, run_id)
    terminal = [event for event in streamed if event["stream_type"] in ("run.completed", "run.failed")]
    assert terminal, "终态事件必须已持久化"
    terminal_status = terminal[-1]["payload_json"]["status"]
    assert terminal_status == projected.stage, (terminal_status, projected.stage)
    return projected


# --------------------------------------------------------------------------------------
# Run 驱动：真实 RunService（替换的只有执行器本身：测试用受控片段）
# --------------------------------------------------------------------------------------


class _GatedExecutor:
    """受控执行片段：阻塞到 `release()`，让 Run 保持非终态（占着本地执行槽）。"""

    def __init__(self, gate: asyncio.Event) -> None:
        self._gate = gate

    async def run(self) -> AsyncIterator[ExecutorEvent]:
        await self._gate.wait()
        yield ExecutorEvent(type="message.delta", data={"delta": "ok"})


class _GatedExecutorFactory:
    def __init__(self) -> None:
        self._gate = asyncio.Event()
        self.requests: list[ExecutorRequest] = []

    def release(self) -> None:
        self._gate.set()

    async def __call__(self, request: ExecutorRequest) -> RunExecutor:
        self.requests.append(request)
        return _GatedExecutor(self._gate)


@pytest.fixture
def gated_executor() -> _GatedExecutorFactory:
    return _GatedExecutorFactory()


class _PgSettingsClient:
    """真实 PG 平台设置源（经 Console 设置服务读取当前版本；无行即 schema 默认）。"""

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        from muad_console_platform.application.platform_settings_service import (
            PlatformSettingsService,
        )

        async with get_session_factory()() as session:
            current = await PlatformSettingsService(session).read_current(tenant_id)
            return PlatformSettingsSnapshot(revision=current.revision, settings=asdict(current.settings))


def _request(
    tenant: TenantContext, *, text: str = "queue projection", conversation_id: uuid.UUID | None = None
) -> RunRequest:
    return RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        conversation_id=conversation_id,
        channel=ChannelContext(type="WECOM", bot_id=f"bot-{uuid.uuid4().hex[:8]}"),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text=text),
    )


async def _start(
    tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    supervisor: ExecutionSupervisor,
    *,
    key: str,
    executor_factory: ExecutorFactory | None = None,
    credentials_client: Any = None,
    request: RunRequest | None = None,
) -> RunStart:
    async with get_session_factory()() as session:
        service = RunService(
            session,
            FakeResolveClient(response=resolved),
            INSTANCE_ID,
            _PgSettingsClient(),
            executor_factory=executor_factory or default_executor_factory,
            credentials_client=credentials_client,
            supervisor=supervisor,
        )
        return await service.start(
            request if request is not None else _request(tenant),
            tenant.tenant_id,
            idempotency_key=key,
        )


async def _noop_executor(request: ExecutorRequest) -> RunExecutor:
    """只用于「本测试内绝不该被领取」的排队 Run：被领取即显式失败，不静默换语义。"""
    raise AssertionError("queued run must not be claimed in this test")


async def _seed_conversation(tenant: TenantContext) -> uuid.UUID:
    """真实会话行：每个 Run 显式绑定自己的会话（未带 conversation_id 时受理会复用最新会话）。"""
    conversation_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.conversation (id, tenant_id, user_id, agent_id, status, last_seq)"
                " VALUES (:id, :tenant_id, :user_id, :agent_id, 'ACTIVE', 0)"
            ).bindparams(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
            )
        )
        await session.commit()
    return conversation_id


def _short_deadline_resolve(
    resolved: ResolveDefinitionResponse, *, deadline_ms: int
) -> ResolveDefinitionResponse:
    agent = resolved.agent.model_copy(update={"runtime_config": {"deadline_ms": deadline_ms}})
    return resolved.model_copy(update={"agent": agent})


async def _cancel(tenant: TenantContext, run_id: uuid.UUID, resolved: ResolveDefinitionResponse) -> None:
    supervisor = ExecutionSupervisor()
    try:
        async with get_session_factory()() as session:
            service = RunService(
                session,
                FakeResolveClient(response=resolved),
                INSTANCE_ID,
                NullPlatformSettingsClient(),
                supervisor=supervisor,
            )
            await service.cancel_run(run_id, tenant.tenant_id)
    finally:
        await supervisor.close()


# --------------------------------------------------------------------------------------
# 1. WAITING_RUNTIME 随受理事务持久化（同事务 + 被拒受理不留阶段事件）
# --------------------------------------------------------------------------------------


async def test_b236_creation_persists_waiting_runtime_stage_with_its_event_atomically(
    projection_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-236] 受理事务内：Run 行 + `run.stage`(WAITING_RUNTIME) 一起出现；被拒受理一起消失。"""
    tenant = projection_tenant
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        # 绝对期限 1ms：领取被期限拒绝，Run 停在 CREATED（真实受理 + 真实冻结排队投影）。
        started = await _start(
            tenant,
            _short_deadline_resolve(resolved, deadline_ms=1),
            execution_supervisor_boundary,
            key="b236-create-1",
            executor_factory=_noop_executor,
        )
        row = await _read_run(tenant.tenant_id, started.run_id)
        assert row["status"] == "CREATED" and row["queue_stage"] == QUEUE_STAGE_WAITING_RUNTIME
        assert row["queued_at"] is not None and row["queue_deadline_at"] is not None
        # 冻结期限 = min(受理 + 配置 60s, 原 Run 绝对期限 1ms) ⇒ 等于绝对期限
        assert row["queue_deadline_at"] == row["deadline_at"]
        assert row["queue_deadline_at"] > row["queued_at"]

        projected = await _assert_entries_agree(
            tenant.tenant_id, started.run_id, expect_stage=QUEUE_STAGE_WAITING_RUNTIME
        )
        assert projected.reason is None and projected.queued_at == row["queued_at"]
        assert projected.deadline_at == row["queue_deadline_at"]
        # Console 入口的 active_run 形状（TASK-049/052 将返回的同一 DTO）
        active = WebchatActiveRun(
            id=started.run_id, status=row["status"], execution_status=projected
        )
        assert active.execution_status == projected
        # 受理事务的同一 now()：Run 行 update_time 与阶段事件 create_time 同值
        events = await _stage_events(tenant.tenant_id, started.run_id)
        assert row["update_time"] == events[-1]["create_time"]

        # 同事务：计数行锁被占时，Run 行与阶段事件都不可见（真实 Lock 等待）。
        await _seed_counter(
            tenant.tenant_id,
            scope_type="USER",
            scope_key=str(tenant.platform_user_id),
            count=0,
        )
        blocked_conversation = await _seed_conversation(tenant)
        conversations_before = await _count_conversations(tenant.tenant_id)
        async with _held_row_lock(
            engine,
            "SELECT open_run_count FROM runtime.run_admission_counter"
            " WHERE tenant_id = :tenant_id AND scope_type = 'USER' AND scope_key = :key FOR UPDATE",
            tenant_id=tenant.tenant_id,
            key=str(tenant.platform_user_id),
        ):
            blocked = asyncio.create_task(
                _start(
                    tenant,
                    _short_deadline_resolve(resolved, deadline_ms=1),
                    execution_supervisor_boundary,
                    key="b236-create-2",
                    executor_factory=_noop_executor,
                    request=_request(tenant, conversation_id=blocked_conversation),
                )
            )
            await _wait_for_lock_waiters(engine, like="%run_admission_counter%")
            # 受理事务未提交：新 Run 行与新阶段事件都还不可见（同事务）
            assert await _count_run_rows(tenant.tenant_id) == 1
            assert await _count_stage_events(tenant.tenant_id) == 1
            assert await _count_conversations(tenant.tenant_id) == conversations_before
        second = await blocked
        assert await _count_run_rows(tenant.tenant_id) == 2
        assert await _count_stage_events(tenant.tenant_id) == 2
        assert await _count_conversations(tenant.tenant_id) == conversations_before
        second_row = await _read_run(tenant.tenant_id, second.run_id)
        assert second_row["queue_stage"] == QUEUE_STAGE_WAITING_RUNTIME

        # 失败分支：准入拒绝（USER 计数已满）整事务回滚 ⇒ 不留半建 Run、也不留阶段事件。
        await _seed_counter(
            tenant.tenant_id,
            scope_type="USER",
            scope_key=str(tenant.platform_user_id),
            count=2,
        )
        await _seed_counter(
            tenant.tenant_id, scope_type="TENANT", scope_key=tenant.tenant_id, count=2
        )
        rejected_conversation = await _seed_conversation(tenant)
        with pytest.raises(AppError) as rejected:
            await _start(
                tenant,
                resolved,
                execution_supervisor_boundary,
                key="b236-create-3",
                executor_factory=_noop_executor,
                request=_request(tenant, conversation_id=rejected_conversation),
            )
        assert rejected.value.code == "RUN_CAPACITY_EXCEEDED"
        assert await _count_run_rows(tenant.tenant_id) == 2
        assert await _count_stage_events(tenant.tenant_id) == 2
        assert await _count_conversations(tenant.tenant_id) == conversations_before + 1
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------------------
# 2. 领取结束排队：同一事务清投影 + 写 RUNNING 事件
# --------------------------------------------------------------------------------------


async def test_b236_claim_ends_the_queue_and_projects_running_in_one_transaction(
    projection_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
) -> None:
    """[B-236] 槽饱和时排队 Run 停在 CREATED/WAITING_RUNTIME；真实领取后同事务转 RUNNING。"""
    tenant = projection_tenant
    engine = create_async_engine(SharedSettings().require_database_url())
    supervisor = ExecutionSupervisor(max_concurrency=1)
    try:
        first = await _start(
            tenant, resolved, supervisor, key="b236-claim-1", executor_factory=gated_executor
        )
        await _wait_for_status(tenant.tenant_id, first.run_id, ("RUNNING",))
        second = await _start(
            tenant,
            resolved,
            supervisor,
            key="b236-claim-2",
            executor_factory=_noop_executor,
            request=_request(tenant, conversation_id=await _seed_conversation(tenant)),
        )
        queued = await _read_run(tenant.tenant_id, second.run_id)
        assert queued["status"] == "CREATED" and queued["queue_stage"] == QUEUE_STAGE_WAITING_RUNTIME
        await _assert_entries_agree(
            tenant.tenant_id, second.run_id, expect_stage=QUEUE_STAGE_WAITING_RUNTIME
        )
        stages_before = await _stage_events(tenant.tenant_id, second.run_id)
        assert [event["payload_json"]["stage"] for event in stages_before] == [
            QUEUE_STAGE_WAITING_RUNTIME
        ]

        # 放行占槽片段：Run1 完成、槽释放；Run2 仍未被领取（本测试手工触发真实领取）。
        gated_executor.release()
        await _wait_for_status(tenant.tenant_id, first.run_id, ("COMPLETED",))
        assert supervisor.free_slots == 1

        # 行锁阻塞期间：Run 行仍是 CREATED、阶段事件仍只有 WAITING_RUNTIME（同一事务）。
        async with _held_row_lock(
            engine,
            "SELECT id FROM runtime.run_record WHERE id = :id FOR UPDATE",
            id=second.run_id,
        ):
            claim_task: asyncio.Task[RunRecord | None] = asyncio.create_task(
                claim_continuation(
                    get_session_factory(),
                    instance_id=INSTANCE_ID,
                    lease_sec=60,
                    run_id=second.run_id,
                )
            )
            await _wait_for_lock_waiters(engine, like="%runtime.run_record%")
            blocked = await _read_run(tenant.tenant_id, second.run_id)
            assert blocked["status"] == "CREATED" and blocked["queue_stage"] is not None
            assert len(await _stage_events(tenant.tenant_id, second.run_id)) == 1
        claimed = await claim_task
        assert claimed is not None and str(claimed.status) == "RUNNING"

        row = await _read_run(tenant.tenant_id, second.run_id)
        assert row["status"] == "RUNNING" and row["queue_stage"] is None
        assert row["execution_epoch"] == 1 and row["lease_owner"] == INSTANCE_ID
        stages = await _stage_events(tenant.tenant_id, second.run_id)
        assert [event["payload_json"]["stage"] for event in stages] == [
            QUEUE_STAGE_WAITING_RUNTIME,
            "RUNNING",
        ]
        projected = await _assert_entries_agree(
            tenant.tenant_id, second.run_id, expect_stage="RUNNING"
        )
        assert projected.started_at == row["start_time"]
        active = WebchatActiveRun(id=second.run_id, status=row["status"], execution_status=projected)
        assert active.execution_status.stage == "RUNNING"
    finally:
        gated_executor.release()
        await supervisor.close()
        await engine.dispose()


# --------------------------------------------------------------------------------------
# 2b. 投影写入的栅栏与原因规范化（失败/边界分支）
# --------------------------------------------------------------------------------------


async def test_b236_stage_persist_fences_foreign_leases_and_normalizes_reasons(
    projection_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
) -> None:
    """[B-236] 非本片段持有/终态 Run 不改写；非码原因不进入公开投影；同状态重放幂等。"""
    tenant = projection_tenant
    supervisor = ExecutionSupervisor(max_concurrency=1)
    try:
        started = await _start(
            tenant, resolved, supervisor, key="b236-fence", executor_factory=gated_executor
        )
        await _wait_for_status(tenant.tenant_id, started.run_id, ("RUNNING",))
        wait_until = datetime.now(UTC) + timedelta(seconds=5)

        # 别的实例持有租约：CAS 落败 ⇒ 不改行、不发事件
        assert (
            await persist_model_wait(
                get_session_factory,
                tenant_id=tenant.tenant_id,
                run_id=started.run_id,
                lease_owner="other-instance",
                reason="NO_CAPACITY",
                queued_at=datetime.now(UTC),
                deadline_at=wait_until,
            )
            is False
        )
        assert (await _read_run(tenant.tenant_id, started.run_id))["queue_stage"] is None
        assert len(await _stage_events(tenant.tenant_id, started.run_id)) == 2

        # 本片段持有但原因是垃圾：原因被规范化丢弃（不进公开投影），阶段照常持久化
        queued_at = datetime.now(UTC)
        assert (
            await persist_model_wait(
                get_session_factory,
                tenant_id=tenant.tenant_id,
                run_id=started.run_id,
                lease_owner=INSTANCE_ID,
                reason="not a code: secret-ish",
                queued_at=queued_at,
                deadline_at=wait_until,
            )
            is True
        )
        fenced = await _read_run(tenant.tenant_id, started.run_id)
        assert fenced["queue_stage"] == QUEUE_STAGE_WAITING_MODEL
        assert fenced["queue_reason"] is None
        stages = await _stage_events(tenant.tenant_id, started.run_id)
        assert stages[-1]["payload_json"]["reason"] is None
        # 同阶段+原因重放幂等：不重复发事件
        assert (
            await persist_model_wait(
                get_session_factory,
                tenant_id=tenant.tenant_id,
                run_id=started.run_id,
                lease_owner=INSTANCE_ID,
                reason=None,
                queued_at=queued_at,
                deadline_at=wait_until,
            )
            is False
        )
        assert len(await _stage_events(tenant.tenant_id, started.run_id)) == 3
        # 恢复：清投影 + RUNNING 事件
        assert (
            await persist_model_running(
                get_session_factory,
                tenant_id=tenant.tenant_id,
                run_id=started.run_id,
                lease_owner=INSTANCE_ID,
            )
            is True
        )
        assert (await _read_run(tenant.tenant_id, started.run_id))["queue_stage"] is None

        # 终态 Run：两个投影写入都拒绝（不把终态改回等待/运行）
        gated_executor.release()
        await _wait_for_status(tenant.tenant_id, started.run_id, ("COMPLETED",))
        assert (
            await persist_model_wait(
                get_session_factory,
                tenant_id=tenant.tenant_id,
                run_id=started.run_id,
                lease_owner=INSTANCE_ID,
                reason="NO_CAPACITY",
                queued_at=datetime.now(UTC),
                deadline_at=datetime.now(UTC) + timedelta(seconds=5),
            )
            is False
        )
        assert (
            await persist_model_running(
                get_session_factory,
                tenant_id=tenant.tenant_id,
                run_id=started.run_id,
                lease_owner=INSTANCE_ID,
            )
            is False
        )
    finally:
        gated_executor.release()
        await supervisor.close()


# --------------------------------------------------------------------------------------
# 3. WAITING_MODEL：等待期间持久化原因与真实期限，额度可用后回到 RUNNING
# --------------------------------------------------------------------------------------

@dataclass(slots=True)
class ProbeCall:
    model: str
    authorization: str
    messages: list[Any]


@dataclass(slots=True)
class ModelProbe:
    calls: list[ProbeCall] = field(default_factory=list)


def _probe_app(probe: ModelProbe) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completions(request: Request) -> Any:
        body = await request.json()
        probe.calls.append(
            ProbeCall(
                model=str(body.get("model")),
                authorization=request.headers.get("authorization", ""),
                messages=list(body.get("messages") or []),
            )
        )
        return JSONResponse(
            {
                "choices": [
                    {"finish_reason": "stop", "message": {"role": "assistant", "content": "pong"}}
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 5},
            }
        )

    return app


@contextlib.asynccontextmanager
async def _serve(app: FastAPI) -> AsyncIterator[str]:
    """真实 uvicorn 单进程监听 127.0.0.1 随机端口（真实 socket，非 ASGI 内存传输）。"""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        deadline = time.monotonic() + LISTEN_TIMEOUT_SEC
        while not server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert server.started, "服务未在超时内监听"
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        listener.close()


@pytest.fixture
async def model_probe() -> AsyncIterator[tuple[ModelProbe, str]]:
    probe = ModelProbe()
    async with _serve(_probe_app(probe)) as url:
        yield probe, url


@pytest.fixture
async def console_url() -> AsyncIterator[str]:
    from muad_console_platform.infrastructure import db as console_db
    from muad_console_platform.main import app as console_app

    console_db.get_engine.cache_clear()
    console_db.get_session_factory.cache_clear()
    async with _serve(console_app) as url:
        yield url


@dataclass(frozen=True, slots=True)
class PoolTenant:
    tenant_id: str
    model_id: uuid.UUID
    agent_id: uuid.UUID
    platform_user_id: uuid.UUID
    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    digest: str
    api_key: str
    base_url: str

    def key_resource_id(self) -> uuid.UUID:
        return derive_model_key_resource_id(self.tenant_id, PROVIDER_KEY, self.digest)


async def _seed_pool_tenant(model_base_url: str) -> PoolTenant:
    """真实控制面行：一名成员（max_inflight=1），物理 Key 身份由真实摘要派生。"""
    tenant_id = f"test-{uuid.uuid4().hex[:20]}"
    model_id, agent_id, platform_user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    credential_id, deployment_id = uuid.uuid4(), uuid.uuid4()
    api_key = f"sk-b236-{uuid.uuid4().hex}"
    digest = hashlib.sha256(api_key.encode()).hexdigest()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.model_definition (id, tenant_id, key, name, model_id,"
                " provider_key, params_json) VALUES (:id, :tenant_id, :key, :name, :provider_model,"
                " :provider_key, CAST(:params AS jsonb))"
            ).bindparams(
                id=model_id,
                tenant_id=tenant_id,
                key=f"model-{uuid.uuid4().hex[:10]}",
                name="b236-model",
                provider_model=MODEL_NAME,
                provider_key=PROVIDER_KEY,
                params='{"temperature": 0.0, "max_tokens": 64}',
            )
        )
        await session.execute(
            sa.text(
                "INSERT INTO control.model_credential (id, tenant_id, key, provider_key, base_url,"
                " api_key, api_key_digest, max_inflight, enabled, revision)"
                " VALUES (:id, :tenant_id, :key, :provider_key, :base_url, :api_key, :digest, 1,"
                " true, 1)"
            ).bindparams(
                id=credential_id,
                tenant_id=tenant_id,
                key=f"key-{uuid.uuid4().hex[:10]}",
                provider_key=PROVIDER_KEY,
                base_url=f"{model_base_url}/v1",
                api_key=api_key,
                digest=digest,
            )
        )
        await session.execute(
            sa.text(
                "INSERT INTO control.model_deployment (id, tenant_id, key, model_id, credential_id,"
                " weight, enabled) VALUES (:id, :tenant_id, :key, :model_id, :credential_id, 1, true)"
            ).bindparams(
                id=deployment_id,
                tenant_id=tenant_id,
                key=f"member-{uuid.uuid4().hex[:10]}",
                model_id=model_id,
                credential_id=credential_id,
            )
        )
        await session.commit()
    return PoolTenant(
        tenant_id=tenant_id,
        model_id=model_id,
        agent_id=agent_id,
        platform_user_id=platform_user_id,
        deployment_id=deployment_id,
        credential_id=credential_id,
        digest=digest,
        api_key=api_key,
        base_url=f"{model_base_url}/v1",
    )


@pytest.fixture
async def pool_tenant(model_probe: tuple[ModelProbe, str]) -> AsyncIterator[PoolTenant]:
    _, model_url = model_probe
    tenant = await _seed_pool_tenant(model_url)
    try:
        yield tenant
    finally:
        await _purge_tenant(tenant.tenant_id)


async def _saturate_key_capacity(tenant: PoolTenant) -> None:
    """真实容量行占满该物理 Key（max_inflight=1）：分配器按 NO_CAPACITY 明确回压。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.model_capacity (tenant_id, resource_type, resource_id,"
                " capacity_revision, inflight, window_start, reserved_tokens, reserved_requests)"
                " VALUES (:tenant_id, 'KEY', :resource_id, 1, 1, now(), 0, 1)"
            ).bindparams(tenant_id=tenant.tenant_id, resource_id=tenant.key_resource_id())
        )
        await session.commit()


async def _free_key_capacity(tenant: PoolTenant) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.model_capacity SET inflight = 0, reserved_requests = 0,"
                " update_time = now() WHERE tenant_id = :tenant_id AND resource_type = 'KEY'"
                " AND resource_id = :resource_id"
            ).bindparams(tenant_id=tenant.tenant_id, resource_id=tenant.key_resource_id())
        )
        await session.commit()


def _pool_resolved_definition(tenant: PoolTenant) -> ResolveDefinitionResponse:
    return ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=tenant.agent_id,
            key=AGENT_KEY,
            revision=1,
            instructions="You are the queue projection agent.",
            runtime_config={"temperature": 0.0},
        ),
        model=ResolvedModel(
            id=tenant.model_id,
            revision=1,
            model_id=MODEL_NAME,
            base_url=tenant.base_url,
            api_key="sk-interim-definition-key",
            params={"temperature": 0.0, "max_tokens": 64},
        ),
    )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return str(content)


async def test_b236_model_wait_projects_reason_and_bounded_deadline_then_resumes(
    pool_tenant: PoolTenant,
    model_probe: tuple[ModelProbe, str],
    console_url: str,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-236] 等待期间：WAITING_MODEL + `data.reason` + 真实期限已落库；恢复后回到 RUNNING。"""
    probe, _ = model_probe
    tenant = pool_tenant
    tenant_ctx = TenantContext(
        tenant_id=tenant.tenant_id,
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
    )
    credentials = ConsoleCredentialsClient(
        console_url, service_token=SharedSettings().internal_service_token or ""
    )
    try:
        await _saturate_key_capacity(tenant)
        started = await _start(
            tenant_ctx,
            _pool_resolved_definition(tenant),
            execution_supervisor_boundary,
            key="b236-model-wait",
            credentials_client=credentials,
        )
        # 等待期间（容量仍被占）：阶段投影已持久化，且上游一次请求都还没发出。
        waiting = await _wait_for_queue_stage(
            tenant.tenant_id, started.run_id, QUEUE_STAGE_WAITING_MODEL
        )
        assert waiting["status"] == "RUNNING"
        assert waiting["queue_reason"] == "NO_CAPACITY"
        assert waiting["queued_at"] is not None and waiting["queue_deadline_at"] is not None
        assert waiting["queue_deadline_at"] > waiting["queued_at"]
        assert waiting["queue_deadline_at"] <= waiting["deadline_at"]
        # 期限 = min(等待起点 + 冻结等待预算 30s, Run 绝对期限)：不显示假预计时间/名次
        assert waiting["queue_deadline_at"] - waiting["queued_at"] <= timedelta(seconds=30)
        assert probe.calls == []
        projected = await _assert_entries_agree(
            tenant.tenant_id,
            started.run_id,
            expect_stage=QUEUE_STAGE_WAITING_MODEL,
            expect_reason="NO_CAPACITY",
        )
        stages = await _stage_events(tenant.tenant_id, started.run_id)
        # 载荷恰为契约字段（没有预计时间/名次等额外键）；排队阶段不写 started_at
        assert set(stages[-1]["payload_json"]) == STAGE_PAYLOAD_KEYS
        assert stages[-1]["payload_json"]["started_at"] is None
        # 同一事务：Run 行 update_time 与阶段事件 create_time 同值（同一 now()）
        assert waiting["update_time"] == stages[-1]["create_time"]
        active = WebchatActiveRun(
            id=started.run_id, status=waiting["status"], execution_status=projected
        )
        assert active.execution_status.reason == "NO_CAPACITY"

        # 释放额度：真实分配器下一轮拿到容量 → 上游收到真实请求 → 回到 RUNNING 并完成。
        await _free_key_capacity(tenant)
        await _wait_for_status(tenant.tenant_id, started.run_id, ("COMPLETED",))
        finished = await _read_run(tenant.tenant_id, started.run_id)
        assert finished["queue_stage"] is None
        stages = await _stage_events(tenant.tenant_id, started.run_id)
        assert [event["payload_json"]["stage"] for event in stages] == [
            QUEUE_STAGE_WAITING_RUNTIME,
            "RUNNING",
            QUEUE_STAGE_WAITING_MODEL,
            "RUNNING",
        ]
        assert len(probe.calls) == 1 and probe.calls[0].model == MODEL_NAME

        # 状态文案不变助手正文/下一轮历史：真实上游请求与真实上下文重建都没有阶段文案。
        request_text = " ".join(_content_text(item.get("content", "")) for item in probe.calls[0].messages)
        assert "WAITING" not in request_text and "排队" not in request_text
        assert "queue projection" in request_text
        assert "RUN_STAGE" not in HISTORY_EVENT_TYPES
        conversation_id = uuid.UUID(str(finished["conversation_id"]))
        history = await DbBackedContextBuilder(
            session_factory=get_session_factory, budget=BudgetPolicy()
        ).load_history(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
            user_id=tenant.platform_user_id,
        )
        assert history, "完成后的会话必须有真实历史"
        history_text = " ".join(_content_text(message.content) for message in history)
        assert "WAITING" not in history_text and "正在等待" not in history_text
        assert "queue projection" in history_text
        # 阶段事件不是助手正文：载荷没有正文/终态字段（契约 extra=forbid 已挡住未知键）
        assert all("text" not in event["payload_json"] for event in stages)
        terminal = next(
            event for event in await _stream_events(tenant.tenant_id, started.run_id)
            if event["stream_type"] == "run.completed"
        )
        assert terminal["payload_json"]["status"] == "COMPLETED"
    finally:
        await credentials.aclose()


# --------------------------------------------------------------------------------------
# 4. 工具阶段 WAITING_TOOL：持久化 + 恢复后投影来自持久事实
# --------------------------------------------------------------------------------------


async def _seed_claimable_run(tenant: TenantContext, skill: ResolvedSkill) -> dict[str, Any]:
    """真实行：会话 + 冻结快照 + CREATED Run + OPEN 提交（领取谓词要求的全部事实）。"""
    run_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    agent = ResolvedAgent(
        id=tenant.agent_id, key=AGENT_KEY, revision=1, instructions="", runtime_config={}
    )
    model = ResolvedModel(
        id=uuid.uuid4(), revision=1, model_id=MODEL_NAME, base_url="http://model.invalid/v1", params={}
    )
    async with get_session_factory()() as session, session.begin():
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
            )
        )
        snapshot = RuntimeSnapshot(
            run_id=run_id,
            tenant_id=tenant.tenant_id,
            agent_revision=agent.revision,
            model_revision=model.revision,
            agent_json=agent.model_dump(mode="json"),
            model_json=model.model_dump(mode="json", exclude={"api_key"}),
            skill_catalog_json=[skill.model_dump(mode="json")],
            mcp_catalog_json=[],
            policy_json=snapshot_policy(),
            prompt_template_version="1",
            schema_version=1,
            content_hash="sha256:" + "b" * 64,
        )
        session.add(snapshot)
        await session.flush()
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                snapshot_id=snapshot.id,
                status=RunStatus.CREATED,
                input_text="b236-tool",
                trace_id=uuid.uuid4().hex,
                start_time=datetime.now(UTC),
                deadline_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        session.add(
            RunSubmission(
                tenant_id=tenant.tenant_id,
                run_id=run_id,
                conversation_id=conversation_id,
                idempotency_key=uuid.uuid4().hex,
                endpoint="create-run",
                request_fingerprint="sha256:" + "c" * 64,
                actor_user_id=tenant.platform_user_id,
            )
        )
    return {"run_id": run_id, "conversation_id": conversation_id, "agent": agent, "model": model}


def _task_context(
    tenant: TenantContext, seeded: Mapping[str, Any], skill: ResolvedSkill
) -> TaskSubmissionContext:
    return TaskSubmissionContext(
        tenant_id=tenant.tenant_id,
        actor_user_id=tenant.platform_user_id,
        agent=seeded["agent"],
        model=seeded["model"],
        skills=(skill,),
        source_run_id=seeded["run_id"],
    )


async def test_b236_tool_wait_stage_is_persisted_and_recovered_from_persisted_facts(
    projection_tenant: TenantContext,
) -> None:
    """[B-236] WAITING_TOOL 与既有 `run.waiting_tool` 同事务落库；新连接读回同一投影。"""
    tenant = projection_tenant
    skill = ResolvedSkill(
        skill_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        key="async",
        name="Async",
        description="",
        version="1",
        checksum="sha256:" + "a" * 64,
        storage_key="skills/async.zip",
        execution_mode=SkillExecutionMode.ASYNC,
    )
    seeded = await _seed_claimable_run(tenant, skill)
    run_id = seeded["run_id"]
    claimed = await claim_continuation(
        get_session_factory(), instance_id=INSTANCE_ID, lease_sec=60, run_id=run_id
    )
    assert claimed is not None
    await reserve_operation(
        get_session_factory(),
        _task_context(tenant, seeded, skill),
        skill=skill,
        input_data={},
        call_id="call-1",
        completion_mode=CompletionMode.JOIN,
    )
    decision = await try_wait(
        get_session_factory(),
        ExecutionIdentity.of(claimed),
        RunnerCheckpoint(turns=1),
        assistant_text="working",
    )
    assert decision == WaitDecision.WAIT

    row = await _read_run(tenant.tenant_id, run_id)
    assert row["status"] == "WAITING_TOOL"
    assert row["queue_stage"] is None and row["lease_owner"] is None
    streamed = await _stream_events(tenant.tenant_id, run_id)
    assert [event["stream_type"] for event in streamed] == [
        STAGE_STREAM_TYPE,  # 领取：RUNNING
        "run.waiting_tool",
        STAGE_STREAM_TYPE,  # 工具等待：WAITING_TOOL
    ]
    assert [
        event["payload_json"]["stage"]
        for event in streamed
        if event["stream_type"] == STAGE_STREAM_TYPE
    ] == ["RUNNING", "WAITING_TOOL"]
    waiting_tool = next(event for event in streamed if event["stream_type"] == "run.waiting_tool")
    stage = streamed[-1]
    assert waiting_tool["payload_json"]["wait_generation"] >= 1
    # 同事务：等待事件与阶段事件共享同一 now()
    assert waiting_tool["create_time"] == stage["create_time"]
    projected = await _assert_entries_agree(tenant.tenant_id, run_id, expect_stage="WAITING_TOOL")
    assert projected.started_at == row["start_time"]
    assert projected.queued_at is None and projected.deadline_at is None

    # 恢复后状态真实：新连接/新会话读回同一持久投影（不依赖任何内存状态）。
    recovered = await _assert_entries_agree(tenant.tenant_id, run_id, expect_stage="WAITING_TOOL")
    assert recovered == projected


# --------------------------------------------------------------------------------------
# 5. 终态/取消后投影收敛（排队投影清空、阶段=终态、终态事件同一词表）
# --------------------------------------------------------------------------------------


async def test_b236_projection_converges_after_cancel_and_queue_timeout(
    projection_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
) -> None:
    """[B-236] 取消与排队过期后：队列投影清空、行投影=终态、终态事件同一词表。"""
    tenant = projection_tenant
    supervisor = ExecutionSupervisor(max_concurrency=1)
    try:
        first = await _start(
            tenant, resolved, supervisor, key="b236-final-1", executor_factory=gated_executor
        )
        await _wait_for_status(tenant.tenant_id, first.run_id, ("RUNNING",))
        second = await _start(
            tenant,
            resolved,
            supervisor,
            key="b236-final-2",
            executor_factory=_noop_executor,
            request=_request(tenant, conversation_id=await _seed_conversation(tenant)),
        )
        await _wait_for_queue_stage(
            tenant.tenant_id, second.run_id, QUEUE_STAGE_WAITING_RUNTIME
        )

        # 取消排队 Run：真实取消路径（CAS 终态 + 事件 + 释放同事务）。
        await _cancel(tenant, second.run_id, resolved)
        cancelled = await _read_run(tenant.tenant_id, second.run_id)
        assert cancelled["status"] == "CANCELLED"
        assert cancelled["queue_stage"] is None and cancelled["admission_released_at"] is not None
        projected = await _assert_terminal_convergence(
            tenant.tenant_id, second.run_id, expect_stage="CANCELLED"
        )
        assert projected.started_at == cancelled["start_time"]
        streamed = await _stream_events(tenant.tenant_id, second.run_id)
        terminal = next(event for event in streamed if event["stream_type"] == "run.completed")
        assert terminal["payload_json"]["status"] == "CANCELLED"
        # 排队起点事实保留（历史），终态收敛走 run.terminal（前端契约：EOF/阶段不等于完成）
        assert [
            event["payload_json"]["stage"]
            for event in streamed
            if event["stream_type"] == STAGE_STREAM_TYPE
        ] == [QUEUE_STAGE_WAITING_RUNTIME]

        # 排队过期：真实扫描把 Run 置 FAILED/RUN_QUEUE_TIMEOUT，排队投影一并清空。
        third = await _start(
            tenant,
            _short_deadline_resolve(resolved, deadline_ms=1),
            supervisor,
            key="b236-final-3",
            executor_factory=_noop_executor,
            request=_request(tenant, conversation_id=await _seed_conversation(tenant)),
        )
        assert (await _read_run(tenant.tenant_id, third.run_id))["queue_stage"] == (
            QUEUE_STAGE_WAITING_RUNTIME
        )
        from muad_agent_runtime.application.run_service import expire_queued_runs

        assert await expire_queued_runs(get_session_factory()) >= 1
        expired = await _read_run(tenant.tenant_id, third.run_id)
        assert expired["status"] == "FAILED"
        assert expired["queue_stage"] is None
        assert expired["queue_reason"] == RUN_QUEUE_TIMEOUT
        projected = await _assert_terminal_convergence(
            tenant.tenant_id, third.run_id, expect_stage="FAILED"
        )
        assert projected.reason is None
        streamed = await _stream_events(tenant.tenant_id, third.run_id)
        failed = next(event for event in streamed if event["stream_type"] == "run.failed")
        assert failed["payload_json"]["error_code"] == RUN_QUEUE_TIMEOUT
    finally:
        gated_executor.release()
        await supervisor.close()
