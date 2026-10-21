"""B-232：[integration] Runtime 执行槽与独立监督（design §3.2 / §4.1）。

真实边界（不 mock 存储或租约）：
- 真实 `ContinuationPump`/`ExecutionSupervisor`/`claim_continuation`/`RunService` 业务路径：
  领取、占槽、片段收尾、续租、回收、控制领取全部走真实代码；
- 隔离 PostgreSQL（空库 + `alembic upgrade head`，跑完即 drop）：会话/冻结快照/`CREATED` Run/
  `OPEN` 提交/事件/租约/工具操作/控制出箱都只落在本次独立库；
- 真实持久等待边界：`try_wait`（WAITING_TOOL，清租约）与 `release_lease_and_wait`
  （WAITING_INPUT）都写真实 PG；
- 真实 PG 行事实驱动心跳/回收/控制：`renew_execution`/`reap_abandoned_runs`/
  `ControlDispatcher.claim`，不替换存储或租约。

关键断言（B-232 契约）：
1. 本地有槽才 PG claim：没有空闲槽时 `claim_and_submit` **不调用** claim（排队行保持
   `CREATED`/无租约）；片段结束释放槽后按同一真实领取路径立即补齐；
2. 无界 gather 替换为有界监督：真实 `ContinuationPump` 的并发片段数恰为槽位数，重复 tick
   不再多领，只有槽位数那么多的 Run 真的被 PG 领取；
3. WAITING_TOOL/WAITING_INPUT 结束片段即释放槽（Run 仍非终态），释放出的槽立刻可用；
4. 心跳/回收/控制不排在长任务队尾：槽全忙时续租、回收、控制各自独立推进；
5. 断流不取消执行：HTTP 侧断开后受监督执行继续持有租约跑完并释放槽；
6. 独立回收：Run 失去归属（Reaper 接管/终态）后，卡死片段由独立监督循环按 PG 事实回收，
   归属仍在时不误回收。
"""

from __future__ import annotations

import asyncio
import json
import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from muad_agent_core.agent import AgentRunner
from muad_agent_core.agent.continuation import RunnerCheckpoint, WaitDecision
from muad_agent_core.model import OpenAICompatibleProvider
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry
from muad_agent_runtime.application.async_tools.continuation_pump import ContinuationPump
from muad_agent_runtime.application.async_tools.continuation_service import (
    ExecutionIdentity,
    claim_continuation,
    renew_execution,
    try_wait,
)
from muad_agent_runtime.application.async_tools.control_dispatcher import (
    ControlDispatcher,
    ControlDispatchPolicy,
)
from muad_agent_runtime.application.async_tools.operations import (
    add_cancel_command,
    reserve_operation,
)
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.checkpoint import release_lease_and_wait
from muad_agent_runtime.application.continuation_execution import continuation_executor
from muad_agent_runtime.application.executor import (
    AgentRunnerExecutor,
    ExecutorEvent,
    ExecutorRequest,
    RunExecutor,
)
from muad_agent_runtime.application.ports import NullPlatformSettingsClient
from muad_agent_runtime.application.run_service import (
    RunService,
    reap_abandoned_runs,
    snapshot_policy,
)
from muad_agent_runtime.application.task_client import TaskSubmissionContext
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Conversation,
    RunRecord,
    RunSubmission,
    RuntimeSnapshot,
)
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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_runtime.conftest import FakeResolveClient

pytestmark = pytest.mark.integration

INSTANCE = "runtime-slots-b232"
WAIT_TIMEOUT_SEC = 15.0
TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")


# --------------------------------------------------------------------------- #
# 隔离数据存储：本任务边界
# --------------------------------------------------------------------------- #


@pytest.fixture
async def isolated_runtime(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """隔离 PostgreSQL/Redis 号位 + Runtime 进程环境；跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "slots-internal-token")
    await runtime_db.dispose_engine()
    try:
        yield get_session_factory()
    finally:
        # 退出时清缓存：后续套件按已还原的环境重建 engine，不指向已 drop 的库。
        await runtime_db.dispose_engine()


# --------------------------------------------------------------------------- #
# 真实来源：会话 + 冻结快照 + CREATED Run + OPEN 提交（领取谓词要求的全部事实）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _SeededRun:
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    tenant_id: str
    user_id: uuid.UUID
    agent_id: uuid.UUID
    skill: ResolvedSkill | None


@dataclass(frozen=True, slots=True)
class _RunState:
    status: str
    lease_owner: str | None
    lease_until: datetime | None
    execution_epoch: int
    error_code: str | None = None


def _async_skill() -> ResolvedSkill:
    return ResolvedSkill(
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


async def _seed_claimable_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    with_skill: bool = False,
    model_base_url: str = "http://model.invalid/v1",
    deadline_seconds: int = 600,
) -> _SeededRun:
    tenant_id, user_id, agent_id = f"slots-{uuid.uuid4().hex[:12]}", uuid.uuid4(), uuid.uuid4()
    skill = _async_skill() if with_skill else None
    agent = ResolvedAgent(id=agent_id, key="slots-agent", revision=1, instructions="", runtime_config={})
    model = ResolvedModel(id=uuid.uuid4(), revision=1, model_id="probe", base_url=model_base_url, params={})
    run_id = uuid.uuid4()
    async with factory() as session, session.begin():
        conversation = Conversation(tenant_id=tenant_id, user_id=user_id, agent_id=agent_id)
        session.add(conversation)
        await session.flush()
        snapshot = RuntimeSnapshot(
            run_id=run_id,
            tenant_id=tenant_id,
            agent_revision=agent.revision,
            model_revision=model.revision,
            agent_json=agent.model_dump(mode="json"),
            model_json=model.model_dump(mode="json", exclude={"api_key"}),
            skill_catalog_json=[skill.model_dump(mode="json")] if skill else [],
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
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                user_id=user_id,
                agent_id=agent_id,
                snapshot_id=snapshot.id,
                status=RunStatus.CREATED,
                input_text="slots",
                trace_id="slots-b232",
                deadline_at=datetime.now(UTC) + timedelta(seconds=deadline_seconds),
            )
        )
        session.add(
            RunSubmission(
                tenant_id=tenant_id,
                run_id=run_id,
                conversation_id=conversation.id,
                idempotency_key=uuid.uuid4().hex,
                endpoint="create-run",
                request_fingerprint="sha256:" + "c" * 64,
                actor_user_id=user_id,
            )
        )
        await session.flush()
        conversation_id = conversation.id
    return _SeededRun(run_id, conversation_id, tenant_id, user_id, agent_id, skill)


async def _run_state(factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> _RunState:
    async with factory() as session:
        row = await session.get(RunRecord, run_id)
        assert row is not None, f"run {run_id} must exist"
        return _RunState(
            str(row.status), row.lease_owner, row.lease_until, int(row.execution_epoch), row.error_code
        )


async def _leased_by(
    factory: async_sessionmaker[AsyncSession], seeded: list[_SeededRun], owner: str
) -> set[uuid.UUID]:
    async with factory() as session:
        rows = await session.scalars(
            sa.select(RunRecord.id).where(
                RunRecord.id.in_([item.run_id for item in seeded]),
                RunRecord.status == RunStatus.RUNNING,
                RunRecord.lease_owner == owner,
            )
        )
        return set(rows)


async def _wait_until(
    predicate: Callable[[], bool], *, timeout: float = WAIT_TIMEOUT_SEC
) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def _wait_state(
    factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    predicate: Callable[[_RunState], bool],
    *,
    timeout: float = WAIT_TIMEOUT_SEC,
) -> _RunState:
    last: _RunState | None = None
    try:
        async with asyncio.timeout(timeout):
            while True:
                last = await _run_state(factory, run_id)
                if predicate(last):
                    return last
                await asyncio.sleep(0.05)
    except TimeoutError as exc:
        raise AssertionError(f"run {run_id} never matched; last state={last}") from exc


def _resolve_response(agent_id: uuid.UUID) -> ResolveDefinitionResponse:
    return ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=agent_id, key="slots-agent", revision=1, instructions="", runtime_config={}
        ),
        model=ResolvedModel(
            id=uuid.uuid4(), revision=1, model_id="probe", base_url="http://model.invalid/v1", params={}
        ),
        skills=[],
        mcp_servers=[],
    )


def _context(seeded: _SeededRun, model: ResolvedModel | None = None) -> TaskSubmissionContext:
    assert seeded.skill is not None, "context needs a frozen skill"
    return TaskSubmissionContext(
        tenant_id=seeded.tenant_id,
        actor_user_id=seeded.user_id,
        agent=ResolvedAgent(
            id=seeded.agent_id, key="slots-agent", revision=1, instructions="", runtime_config={}
        ),
        model=model
        or ResolvedModel(
            id=uuid.uuid4(), revision=1, model_id="probe", base_url="http://model.invalid/v1", params={}
        ),
        skills=(seeded.skill,),
        source_run_id=seeded.run_id,
    )


# --------------------------------------------------------------------------- #
# 占槽片段与领取包装：制造长任务/卡死片段，不替换存储或租约
# --------------------------------------------------------------------------- #


class _BlockedSegments:
    """占槽到显式放行：单槽「卡死」只占自己的槽，放行/取消才结束片段。"""

    def __init__(self) -> None:
        self._gates: dict[uuid.UUID, asyncio.Event] = {}

    def register(self, run_id: uuid.UUID) -> None:
        """领取即登记（领取是同步事实，片段任务随后才被调度）。"""
        self._gates.setdefault(run_id, asyncio.Event())

    def _gate(self, run_id: uuid.UUID) -> asyncio.Event:
        self.register(run_id)
        return self._gates[run_id]

    async def __call__(self, run: RunRecord) -> None:
        await self._gate(run.id).wait()

    def release(self, run_id: uuid.UUID) -> None:
        self._gate(run_id).set()

    def release_all(self) -> None:
        for gate in self._gates.values():
            gate.set()

    def occupied(self) -> tuple[uuid.UUID, ...]:
        return tuple(self._gates)


class _CountingClaim:
    """真实 `claim_continuation` 的计数包装：证明「没有本地槽就没有 PG 领取」。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        instance_id: str,
        segments: _BlockedSegments,
        *,
        lease_sec: int = 60,
    ) -> None:
        self.factory, self.instance, self.segments = factory, instance_id, segments
        self.lease_sec = lease_sec
        self.calls: list[frozenset[uuid.UUID]] = []

    async def __call__(
        self, excluded: frozenset[uuid.UUID]
    ) -> tuple[uuid.UUID, Callable[[], Awaitable[object]]] | None:
        self.calls.append(excluded)
        run = await claim_continuation(
            self.factory, instance_id=self.instance, lease_sec=self.lease_sec, exclude_runs=excluded
        )
        if run is None:
            return None
        self.segments.register(run.id)
        return run.id, partial(self.segments, run)


class _WaitBoundaryExecution:
    """真实持久等待边界后结束片段：WAITING_TOOL 走 `try_wait`，WAITING_INPUT 走租约释放。"""

    def __init__(
        self, factory: async_sessionmaker[AsyncSession], seeded: _SeededRun, boundary: str
    ) -> None:
        self.factory, self.seeded, self.boundary = factory, seeded, boundary

    async def __call__(self, run: RunRecord) -> None:
        if self.boundary == "WAITING_TOOL":
            await reserve_operation(
                self.factory,
                _context(self.seeded),
                skill=_context(self.seeded).skills[0],
                input_data={},
                call_id="call-1",
                completion_mode=CompletionMode.JOIN,
            )
            decision = await try_wait(
                self.factory,
                ExecutionIdentity.of(run),
                RunnerCheckpoint(turns=1),
                assistant_text="working",
            )
            assert decision == WaitDecision.WAIT
            return
        async with self.factory() as session, session.begin():
            await release_lease_and_wait(
                session, run_id=run.id, conversation_id=run.conversation_id
            )


# --------------------------------------------------------------------------- #
# 1. 本地有槽才 PG claim
# --------------------------------------------------------------------------- #


async def test_b232_local_slot_gates_pg_claim_and_reclaims_on_segment_end(
    isolated_runtime: async_sessionmaker[AsyncSession],
) -> None:
    factory = isolated_runtime
    seeded = [await _seed_claimable_run(factory) for _ in range(3)]
    supervisor = ExecutionSupervisor(max_concurrency=2)
    segments = _BlockedSegments()
    claim = _CountingClaim(factory, INSTANCE, segments)
    try:
        assert await supervisor.claim_and_submit(claim) is True
        assert await supervisor.claim_and_submit(claim) is True
        leased = await _leased_by(factory, seeded, INSTANCE)
        assert len(leased) == 2 and supervisor.execution_count == 2
        assert supervisor.slot_limit == 2 and supervisor.free_slots == 0
        calls_before = len(claim.calls)
        # 槽已满：第三次领取不得触碰 PG（claim 一次都不调用），排队行保持 CREATED/无租约
        assert await supervisor.claim_and_submit(claim) is False
        assert len(claim.calls) == calls_before, "没有本地槽时不得发起 PG 领取"
        queued = next(item.run_id for item in seeded if item.run_id not in leased)
        state = await _run_state(factory, queued)
        assert (state.status, state.lease_owner, state.execution_epoch) == ("CREATED", None, 0)
        # 片段结束即释放槽（Run 仍非终态）：同一真实领取路径立即补齐
        segments.release(next(iter(leased)))
        await _wait_until(lambda: supervisor.execution_count == 1)
        assert (await _run_state(factory, next(iter(leased)))).status == "RUNNING"
        assert await supervisor.claim_and_submit(claim) is True
        assert (await _run_state(factory, queued)).lease_owner == INSTANCE
        assert supervisor.execution_count == 2
    finally:
        segments.release_all()
        await supervisor.close()


# --------------------------------------------------------------------------- #
# 2. 无界 gather 替换为有界监督
# --------------------------------------------------------------------------- #


async def test_b232_pump_bounds_concurrency_to_local_slots(
    isolated_runtime: async_sessionmaker[AsyncSession],
) -> None:
    factory = isolated_runtime
    seeded = [await _seed_claimable_run(factory) for _ in range(4)]
    supervisor = ExecutionSupervisor(max_concurrency=2)
    segments = _BlockedSegments()
    pump = ContinuationPump(
        factory, supervisor, segments, instance_id=INSTANCE, lease_sec=60, poll_sec=0.05
    )
    try:
        assert await pump.run_once() == 2
        assert supervisor.execution_count == 2
        # 有界：重复 tick 不再多领，PG 里只有两个槽位的 Run 真的被领取
        assert await pump.run_once() == 0
        assert supervisor.execution_count == 2
        leased = await _leased_by(factory, seeded, INSTANCE)
        assert len(leased) == 2
        queued = next(item.run_id for item in seeded if item.run_id not in leased)
        assert (await _run_state(factory, queued)).lease_owner is None
        # 片段结束（Run 仍非终态）⇒ 槽释放，pump 下一轮补齐
        segments.release(next(iter(leased)))
        await _wait_until(lambda: supervisor.execution_count == 1)
        assert await pump.run_once() == 1
        assert (await _run_state(factory, queued)).lease_owner == INSTANCE
        assert supervisor.execution_count == 2
    finally:
        segments.release_all()
        await supervisor.close()


# --------------------------------------------------------------------------- #
# 3. WAITING_TOOL/WAITING_INPUT 结束片段即释放槽
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("boundary", ["WAITING_TOOL", "WAITING_INPUT"])
async def test_b232_wait_boundary_releases_segment_slot(
    isolated_runtime: async_sessionmaker[AsyncSession], boundary: str
) -> None:
    factory = isolated_runtime
    waiting = await _seed_claimable_run(factory, with_skill=True)
    queued = await _seed_claimable_run(factory)
    supervisor = ExecutionSupervisor(max_concurrency=1)
    pump = ContinuationPump(
        factory,
        supervisor,
        _WaitBoundaryExecution(factory, waiting, boundary),
        instance_id=INSTANCE,
        lease_sec=60,
        poll_sec=0.05,
    )
    try:
        assert await pump.run_once() == 1
        state = await _wait_state(factory, waiting.run_id, lambda item: item.status == boundary)
        assert state.status not in TERMINAL_STATUSES
        assert state.lease_owner is None, "持久等待边界释放执行租约"
        # 片段槽已释放（与 Run 终态解耦），排队中的另一条 Run 立即被真实领取
        await _wait_until(lambda: supervisor.execution_count == 0)
        assert await pump.run_once() == 1
        assert (await _run_state(factory, queued.run_id)).lease_owner == INSTANCE
    finally:
        await supervisor.close()


# --------------------------------------------------------------------------- #
# 4. 心跳/回收/控制不排在长任务队尾
# --------------------------------------------------------------------------- #


async def test_b232_heartbeat_reclaim_and_control_progress_while_slots_busy(
    isolated_runtime: async_sessionmaker[AsyncSession],
) -> None:
    factory = isolated_runtime
    busy_source = await _seed_claimable_run(factory, with_skill=True)
    supervisor = ExecutionSupervisor(max_concurrency=1)
    segments = _BlockedSegments()
    claim = _CountingClaim(factory, INSTANCE, segments)
    assert await supervisor.claim_and_submit(claim) is True
    busy = next(iter(segments.occupied()))
    assert busy == busy_source.run_id, "唯一可领取来源先占槽"
    # 另一条来源在占槽之后种入：用于真实回收路径，不参与本次领取竞争
    stale = await _seed_claimable_run(factory)
    async with factory() as session, session.begin():
        row = await session.get(RunRecord, stale.run_id)
        assert row is not None
        row.status, row.lease_owner, row.execution_epoch = RunStatus.RUNNING, "abandoned", 1
        row.lease_until = datetime.now(UTC) - timedelta(seconds=5)
    client = httpx.AsyncClient(trust_env=False)
    try:
        async with asyncio.timeout(WAIT_TIMEOUT_SEC):
            # 心跳：槽全忙时对占槽 Run 的真实续租仍然推进
            before = (await _run_state(factory, busy)).lease_until
            assert before is not None
            identity = ExecutionIdentity(busy_source.tenant_id, busy, INSTANCE, 1)
            assert await renew_execution(factory, identity, lease_sec=60) is True
            after = (await _run_state(factory, busy)).lease_until
            assert after is not None and after > before
            # 回收：另一条租约过期的 RUNNING Run 被真实 Reaper 接管
            assert await reap_abandoned_runs(factory) >= 1
            reaped = await _run_state(factory, stale.run_id)
            assert (reaped.status, reaped.lease_owner) == ("FAILED", None)
            # 控制：占槽 Run 的真实工具操作 + 取消命令仍被独立领取
            context = _context(busy_source)
            op = await reserve_operation(
                factory,
                context,
                skill=context.skills[0],
                input_data={},
                call_id="control-1",
                completion_mode=CompletionMode.JOIN,
            )
            async with factory() as session, session.begin():
                await add_cancel_command(session, op, datetime.now(UTC))
            dispatcher = ControlDispatcher(
                factory,
                client,
                "http://127.0.0.1:9",
                policy=ControlDispatchPolicy(poll_sec=0.05),
                instance_id=INSTANCE,
            )
            rows = await dispatcher.claim(datetime.now(UTC), operation_id=op.id)
            commands = {str(row.command) for row in rows}
            assert rows and all(row.operation_id == op.id for row in rows)
            assert "CANCEL_OPERATION" in commands, "控制命令在槽全忙时仍被独立领取"
        assert supervisor.execution_count == 1, "独立路径不抢占也不释放占槽片段"
    finally:
        await client.aclose()
        segments.release_all()
        await supervisor.close()


# --------------------------------------------------------------------------- #
# 5. 断流不取消执行
# --------------------------------------------------------------------------- #


def _run_request(
    *, agent_id: uuid.UUID, user_id: uuid.UUID, conversation_id: uuid.UUID | None
) -> RunRequest:
    return RunRequest(
        agent_id=agent_id,
        platform_user_id=user_id,
        conversation_id=conversation_id,
        channel=ChannelContext(type="WECOM", bot_id="bot-slots"),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text="keep running"),
    )


async def test_b232_disconnected_stream_keeps_supervised_execution_running(
    isolated_runtime: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = isolated_runtime
    monkeypatch.setenv("RUN_HEARTBEAT_SEC", "1")
    tenant_id, agent_id, user_id = f"slots-{uuid.uuid4().hex[:12]}", uuid.uuid4(), uuid.uuid4()
    supervisor = ExecutionSupervisor(max_concurrency=1)
    gate = asyncio.Event()

    class _GatedExecutor:
        async def run(self) -> AsyncIterator[ExecutorEvent]:
            yield ExecutorEvent(type="message.delta", data={"delta": "started"})
            await gate.wait()
            yield ExecutorEvent(type="message.delta", data={"delta": "done"})

    async def _gated_factory(request: ExecutorRequest) -> RunExecutor:
        return _GatedExecutor()

    resolve = FakeResolveClient(response=_resolve_response(agent_id))
    try:
        async with factory() as session:
            service = RunService(
                session,
                resolve,
                INSTANCE,
                NullPlatformSettingsClient(),
                executor_factory=_gated_factory,
                supervisor=supervisor,
            )
            started = await service.start(
                _run_request(agent_id=agent_id, user_id=user_id, conversation_id=None), tenant_id
            )
            iterator = started.events
            first = await anext(iterator)
            assert first.type == "run.created"
            await iterator.aclose()  # HTTP 侧断开
        # 领取/消费在后台任务里启动：等监督器接管后，断流仍不取消执行
        async with asyncio.timeout(15):
            while supervisor.execution_count == 0:
                await asyncio.sleep(0.01)
        # 断流后：受监督执行仍在自己的槽里跑，Run 未取消
        assert supervisor.execution_count == 1
        state = await _run_state(factory, started.run_id)
        assert state.status == "RUNNING" and state.lease_owner == INSTANCE
        # 片段内心跳继续推进租约（执行不依赖订阅方存活）
        before = state.lease_until
        assert before is not None
        renewed = await _wait_state(
            factory, started.run_id, lambda item: bool(item.lease_until and item.lease_until > before)
        )
        assert renewed.lease_owner == INSTANCE
        gate.set()
        await _wait_until(lambda: supervisor.execution_count == 0)
        final = await _run_state(factory, started.run_id)
        assert (final.status, final.lease_owner) == ("COMPLETED", None)
    finally:
        gate.set()
        await supervisor.close()


# --------------------------------------------------------------------------- #
# 6. 独立回收：卡死槽按 PG 归属事实回收，归属仍在时不误回收
# --------------------------------------------------------------------------- #


async def test_b232_stuck_slot_is_reclaimed_after_ownership_is_lost(
    isolated_runtime: async_sessionmaker[AsyncSession],
) -> None:
    factory = isolated_runtime
    stuck_source = await _seed_claimable_run(factory)
    supervisor = ExecutionSupervisor(max_concurrency=1)
    segments = _BlockedSegments()
    claim = _CountingClaim(factory, INSTANCE, segments)
    from muad_agent_runtime.application.async_tools.supervisor import reclaim_stale_executions

    try:
        assert await supervisor.claim_and_submit(claim) is True
        stuck = next(iter(segments.occupied()))
        assert stuck == stuck_source.run_id, "唯一可领取来源先占槽"
        # 另一条来源在占槽之后种入：用于释放出的槽立即可复用
        other = await _seed_claimable_run(factory)
        # 归属仍在：监督循环不得误回收
        assert await reclaim_stale_executions(factory, supervisor, instance_id=INSTANCE) == 0
        assert supervisor.execution_count == 1
        # 真实回收路径让 Run 失去归属（租约过期 ⇒ Reaper 接管为 FAILED）
        async with factory() as session, session.begin():
            row = await session.get(RunRecord, stuck)
            assert row is not None
            row.lease_until = datetime.now(UTC) - timedelta(seconds=5)
        assert await reap_abandoned_runs(factory) >= 1
        assert (await _run_state(factory, stuck)).status == "FAILED"
        # 槽仍被卡死片段占着（槽释放与 Run 终态解耦）：独立监督按 PG 事实回收它
        assert supervisor.execution_count == 1
        assert await reclaim_stale_executions(factory, supervisor, instance_id=INSTANCE) == 1
        await _wait_until(lambda: supervisor.execution_count == 0)
        # 释放出的槽立即可用：真实领取路径补齐
        assert await supervisor.claim_and_submit(claim) is True
        assert (await _run_state(factory, other.run_id)).lease_owner == INSTANCE
    finally:
        segments.release_all()
        await supervisor.close()


# --------------------------------------------------------------------------- #
# 7. main.py 装配：槽位预算注入 + 独立监督循环
# --------------------------------------------------------------------------- #


async def test_b232_lifespan_injects_slot_budget_and_runs_slot_supervision(
    isolated_runtime: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNTIME_EXECUTION_CONCURRENCY", "3")
    monkeypatch.setenv("RUN_REAPER_INTERVAL_SEC", "1")
    from muad_agent_runtime import main

    gate = asyncio.Event()
    async with main.lifespan(main.app):
        supervisor = main.app.state.execution_supervisor
        assert isinstance(supervisor, ExecutionSupervisor)
        assert supervisor.slot_limit == 3, "启动配置注入执行槽预算"
        slot_task = main.app.state.execution_slot_task
        assert not slot_task.done()
        # 独立监督循环按 PG 归属事实回收：没有对应 Run 行的片段不属于本实例
        assert await supervisor.submit(uuid.uuid4(), gate.wait) is True
        assert supervisor.execution_count == 1
        await _wait_until(lambda: supervisor.execution_count == 0)
    assert slot_task.done()


# --------------------------------------------------------------------------- #
# 8. 有界槽的边界：非法预算与同一 Run 不重复占槽
# --------------------------------------------------------------------------- #


def test_b232_supervisor_rejects_invalid_slot_budget() -> None:
    for invalid in (0, 257, True):
        with pytest.raises(ValueError):
            ExecutionSupervisor(max_concurrency=invalid)  # type: ignore[arg-type]


async def test_b232_supervisor_never_double_books_a_run_slot() -> None:
    supervisor = ExecutionSupervisor(max_concurrency=1)
    gate = asyncio.Event()
    run_id = uuid.uuid4()
    try:
        assert await supervisor.submit(run_id, gate.wait) is True
        assert await supervisor.submit(run_id, gate.wait) is False, "同一 Run 不重复占槽"
        assert await supervisor.submit(uuid.uuid4(), gate.wait) is False, "无空闲槽不接收"
        assert supervisor.execution_count == 1 and supervisor.free_slots == 0
    finally:
        gate.set()
        await _wait_until(lambda: supervisor.execution_count == 0)
        await supervisor.close()


async def test_supervisor_propagates_claim_failure_and_releases_pending_slot() -> None:
    supervisor = ExecutionSupervisor(max_concurrency=1)
    failure = RuntimeError("claim failed")

    async def claim(_excluded: frozenset[uuid.UUID]) -> None:
        raise failure

    try:
        with pytest.raises(RuntimeError) as raised:
            await supervisor.claim_and_submit(claim)
        assert raised.value is failure
        assert supervisor.free_slots == 1
        assert await supervisor.submit(uuid.uuid4(), asyncio.Event().wait)
    finally:
        await supervisor.close()


async def test_supervisor_propagates_cancel_during_claim_and_releases_pending_slot() -> None:
    supervisor = ExecutionSupervisor(max_concurrency=1)
    entered = asyncio.Event()

    async def claim(_excluded: frozenset[uuid.UUID]) -> None:
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(supervisor.claim_and_submit(claim))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert supervisor.free_slots == 1
        assert await supervisor.submit(uuid.uuid4(), asyncio.Event().wait)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await supervisor.close()


async def test_supervisor_counts_pending_claims_without_serializing_their_io() -> None:
    supervisor = ExecutionSupervisor(max_concurrency=2)
    entered = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    calls = 0

    async def claim(_excluded: frozenset[uuid.UUID]) -> None:
        nonlocal calls
        index = calls
        calls += 1
        entered[index].set()
        await release.wait()

    tasks: list[asyncio.Task[bool]] = []
    try:
        for event in entered:
            tasks.append(asyncio.create_task(supervisor.claim_and_submit(claim)))
            await asyncio.wait_for(event.wait(), timeout=1)
        assert supervisor.free_slots == 0
        assert supervisor.execution_count == 0
        assert await supervisor.claim_and_submit(claim) is False
        assert await supervisor.submit(uuid.uuid4(), asyncio.Event().wait) is False
        assert calls == 2
        release.set()
        assert await asyncio.gather(*tasks) == [False, False]
        assert supervisor.free_slots == 2
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await supervisor.close()


# --------------------------------------------------------------------------- #
# 真实 HTTP 模型探针（供需要真实模型回合的边界使用）
# --------------------------------------------------------------------------- #


def _probe_app(requests: list[dict[str, Any]]) -> FastAPI:
    """真实模型探针：第 1 回合返回工具调用，第 2 回合返回正文收尾。"""
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completion(request: Request) -> Any:
        body = await request.json()
        requests.append(body)
        first = len(requests) == 1
        if body.get("stream"):

            async def chunks() -> AsyncIterator[str]:
                if first:
                    delta = {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "execute_skill", "arguments": "{}"},
                            }
                        ]
                    }
                    finish = "tool_calls"
                else:
                    delta, finish = {"content": "probe final"}, "stop"
                yield f'data: {{"choices":[{{"delta": {_json(delta)}}}]}}\n\n'
                yield f'data: {{"choices":[{{"delta": {{}}, "finish_reason": "{finish}"}}]}}\n\n'
                yield "data: [DONE]\n\n"

            return StreamingResponse(chunks(), media_type="text/event-stream")
        message: dict[str, Any] = {"role": "assistant", "content": "" if first else "probe final"}
        if first:
            message["tool_calls"] = [
                {"id": "call-1", "type": "function", "function": {"name": "execute_skill", "arguments": "{}"}}
            ]
        return JSONResponse(
            {
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if first else "stop",
                        "message": message,
                    }
                ]
            }
        )

    return app


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value)


@pytest.fixture
async def model_probe() -> AsyncIterator[tuple[list[dict[str, Any]], str]]:
    requests: list[dict[str, Any]] = []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(_probe_app(requests), log_level="error", lifespan="off"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        yield requests, f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=10)
        listener.close()


# --------------------------------------------------------------------------- #
# 9. 真实模型回合（真实 HTTP 探针）在工具等待处结束片段并释放槽
# --------------------------------------------------------------------------- #


async def test_b232_real_model_round_releases_slot_at_tool_wait(
    isolated_runtime: async_sessionmaker[AsyncSession],
    model_probe: tuple[list[dict[str, Any]], str],
) -> None:
    requests, probe_url = model_probe
    factory = isolated_runtime
    source = await _seed_claimable_run(factory, with_skill=True, model_base_url=f"{probe_url}/v1")
    assert source.skill is not None
    registry = ToolRegistry()

    async def submit(arguments: dict[str, Any], *, call_id: str) -> str:
        op = await reserve_operation(
            factory,
            _context(source),
            skill=source.skill,
            input_data={},
            call_id=call_id,
            completion_mode=CompletionMode.JOIN,
        )
        return json.dumps(
            {"operation_id": str(op.id), "submission_status": "PENDING", "task_id": None}
        )

    registry.register(
        ToolDefinition(
            "execute_skill", "submit", {"type": "object", "properties": {}}, ToolEffect.EXTERNAL, submit
        )
    )

    async def executor_factory(request: ExecutorRequest) -> RunExecutor:
        provider = OpenAICompatibleProvider(
            base_url=request.model.base_url,
            model=request.model.model_id,
            api_key=request.model.api_key or None,
        )
        runner = AgentRunner(provider=provider, registry=registry, runtime_events=request.runtime_events)
        return AgentRunnerExecutor(runner=runner, request=request, close=provider.aclose)

    supervisor = ExecutionSupervisor(max_concurrency=1)
    # 生产装配路径：真实 continuation_executor 读冻结快照并跑真实 runner 回合。
    execute = continuation_executor(
        FakeResolveClient(response=_resolve_response(source.agent_id)),
        None,  # type: ignore[arg-type] —— 无 API-09 客户端：模型密钥取快照（本用例不校验鉴权）
        NullPlatformSettingsClient(),
        supervisor,
        executor_factory,
        instance_id=INSTANCE,
    )
    pump = ContinuationPump(
        factory, supervisor, execute, instance_id=INSTANCE, lease_sec=60, poll_sec=0.05
    )
    try:
        assert await pump.run_once() == 1
        state = await _wait_state(factory, source.run_id, lambda item: item.status == "WAITING_TOOL")
        assert len(requests) == 2, "真实 HTTP 模型探针收到两个回合（工具回合 + 等待判定回合）"
        assert state.status not in TERMINAL_STATUSES and state.lease_owner is None
        await _wait_until(lambda: supervisor.execution_count == 0)
        assert await pump.run_once() == 0, "等待中的 Run 不再占槽、也不被重复领取"
    finally:
        await supervisor.close()
