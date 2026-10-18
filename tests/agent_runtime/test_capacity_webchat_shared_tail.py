"""B-260 / B-06：共享事件追尾、提交后唤醒及补查（TASK-060；design §3.2 / §3.6 / §3.7）。

真实边界（不 mock 存储或租约）：

- **真实 Runtime 业务服务**：`RunService.start` 受理 + 真实 `AgentRunnerExecutor` →
  `AgentRunner` → `OpenAICompatibleProvider` 调用链；装配只决定模型端点在哪儿，不替换
  存储、租约或执行器；写入侧就是生产路径（`_persist_event` / `_finalize_run` 提交后唤醒）。
- **真实 HTTP 模型探针**：进程内 uvicorn 的 `text/event-stream` 端点，按脚本逐块下发正文；
  块间节拍与闸门都发生在真实 socket 上，不是 provider 替身。
- **隔离 PostgreSQL**（空库 + `alembic upgrade head`，跑完即 drop）与隔离 Redis 号位：
  会话/Run/canonical 事件/租约都是真实行，`seq` 由真实 `EventWriter` 在会话行锁内分配；
  Redis 用真实连接（含第二个连接跨进程发布提示），故障用不可达端口模拟。
- **真实连接池事实**：`yield` 是否占用数据库连接直接读真实引擎池的 `checkedout()`。

关键断言（契约逐项）：

1. **同 Run/会话共享上游追尾**：观察者数量不线性放大上游读取轮次（1 个 vs 3 个订阅者对同一
   事件序列的共享读取轮次不随观察者增长）；每个订阅者各自拿到完整且不重复的 seq 序列。
2. **提交后唤醒**：等待中的订阅者在远小于补查周期（30s）的预算内收到提交后的行——到达只能
   由"提交后唤醒"解释，不是轮询空转。
3. **补查**：断线重连按水位补拉缺失区间（不丢不重）；Redis hint 故障（不可达）时按 PG 有界
   补查继续投递，降级可见（`fallback_polls` / `hint_degraded`）；真实 Redis Pub/Sub 的另一
   连接发布提示同样能唤醒（跨进程语义）。
4. **退出 session 才 yield**：订阅者暂停消费时，进程池里没有本模块占用的连接；继续消费能拿到
   期间新提交的行。**心跳不推进业务游标**：SSE 心跳帧与业务序号无关，心跳期间提交的行仍按
   连续 seq 送出（不跳号）。
5. **过滤跳号 / 重复 seq / checkpoint / 游标分型**：内部事件被过滤造成的 canonical 跳号是合法
   事实（不判丢包、不补拉循环，也不是游标过期）；`seq <= 游标` 的行一律不重复产出；
   `checkpoint` 只回真实持久水位、不另分配 canonical seq；超前游标 `CURSOR_AHEAD`、已过期
   游标 `CURSOR_EXPIRED` 分别显式失败。
6. **重建快照不混 Run / 终态不先于最后持久正文**：Run 作用域的流不夹带同会话其它 Run 的行；
   终态行排在全部已持久正文之后；Run 行已终态而缺终态行（Reaper 路径）时，合成终态仍在全部
   已持久正文之后。
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from muad_agent_core.agent import AgentRunner
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.model import OpenAICompatibleProvider
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.event_tail_hub import (
    WAKEUP_CHANNEL,
    CanonicalEventView,
    EventTailHub,
    RedisWakeupHint,
    TailBatch,
)
from muad_agent_runtime.application.executor import (
    AgentRunnerExecutor,
    ExecutorEvent,
    ExecutorFactory,
    ExecutorRequest,
    RunExecutor,
)
from muad_agent_runtime.application.ports import NullPlatformSettingsClient
from muad_agent_runtime.application.public_events import project_public_stream_event
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.application.run_service import RunService, RunStart
from muad_agent_runtime.application.sse import HEARTBEAT_FRAME, iter_sse_frames
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.db import get_engine, get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, Conversation, RunRecord
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    ChannelContext,
    MessageInput,
    ResolvedAgent,
    ResolveDefinitionResponse,
    ResolvedModel,
    RunRequest,
    RunStatus,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import QueuePool

from agent_runtime.conftest import FakeResolveClient

pytestmark = pytest.mark.integration

WAIT_TIMEOUT_SEC = 20.0
POLL_SEC = 0.05
#: 唤醒预算：补查周期被故意设成 30s，任何小于它的到达都只能由"提交后唤醒"解释。
WAKEUP_BUDGET_SEC = 5.0
#: 远端补查周期（用例内构造的 hub）：远大于唤醒预算，专门用来区分唤醒与轮询。
FAR_FALLBACK_SEC = 30.0
#: 本地有界补查周期（hint 故障退化路径）：比唤醒预算小一个量级。
NEAR_FALLBACK_SEC = 0.2
DELTA_STREAM_TYPE = "message.delta"
TERMINAL_STREAM_TYPES = ("run.completed", "run.failed")
INTERNAL_STREAM_TYPE = "model.started"


# --------------------------------------------------------------------------- #
# 隔离数据存储与真实 HTTP 模型探针
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TailTenant:
    tenant_id: str
    agent_id: uuid.UUID
    user_id: uuid.UUID


@pytest.fixture
async def tail_tenant(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[TailTenant]:
    """隔离 PostgreSQL/Redis 号位 + Runtime 进程环境；跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", "http://127.0.0.1:9")
    await runtime_db.dispose_engine()
    try:
        yield TailTenant(
            tenant_id=f"tail-{uuid.uuid4().hex[:12]}",
            agent_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
        )
    finally:
        await runtime_db.dispose_engine()


@dataclass
class ModelProbeScript:
    """真实 SSE 探针脚本：起始闸门 + 正文块序列 + 块间节拍 + "发完第 N 块后等放行"。"""

    chunks: tuple[str, ...]
    gap_sec: float = 0.0
    hold_after: int = 0
    start_gate: asyncio.Event | None = None
    release: asyncio.Event = field(default_factory=asyncio.Event)
    sent: int = 0

    @property
    def full_text(self) -> str:
        return "".join(self.chunks)


def _chunk_frame(text: str, *, finish_reason: str | None = None) -> str:
    choice: dict[str, Any] = {"index": 0, "delta": {"content": text} if text else {}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return "data: " + json.dumps({"choices": [choice]}) + "\n\n"


async def _probe_frames(script: ModelProbeScript) -> AsyncIterator[str]:
    if script.start_gate is not None:
        await script.start_gate.wait()
    for index, text in enumerate(script.chunks, start=1):
        if index > 1 and script.gap_sec > 0:
            await asyncio.sleep(script.gap_sec)
        script.sent = index
        yield _chunk_frame(text)
        if index == script.hold_after:
            await script.release.wait()
    yield _chunk_frame("", finish_reason="stop")
    yield "data: [DONE]\n\n"


def _probe_app(script: ModelProbeScript) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completions(request: Request) -> StreamingResponse:
        await request.json()
        return StreamingResponse(_probe_frames(script), media_type="text/event-stream")

    return app


@asynccontextmanager
async def model_probe(script: ModelProbeScript) -> AsyncIterator[str]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(_probe_app(script), log_level="error", lifespan="off"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{listener.getsockname()[1]}/v1"
    finally:
        script.release.set()
        if script.start_gate is not None:
            script.start_gate.set()
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=10)
        listener.close()


# --------------------------------------------------------------------------- #
# 真实业务服务与真实订阅者
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class StartedRun:
    service: RunService
    supervisor: ExecutionSupervisor
    session: AsyncSession
    start: RunStart

    @property
    def run_id(self) -> uuid.UUID:
        return self.start.run_id

    @property
    def conversation_id(self) -> uuid.UUID:
        return self.start.conversation_id


def _resolve_response(tenant: TailTenant, base_url: str) -> ResolveDefinitionResponse:
    return ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=tenant.agent_id,
            key="tail-agent",
            revision=1,
            instructions="fixed instructions",
            runtime_config={},
        ),
        model=ResolvedModel(
            id=uuid.uuid4(),
            revision=1,
            model_id="probe",
            base_url=base_url,
            api_key="probe-credential",
            params={"temperature": 0.0, "max_tokens": 256},
        ),
        skills=[],
        mcp_servers=[],
    )


def _run_request(tenant: TailTenant) -> RunRequest:
    return RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.user_id,
        channel=ChannelContext(type="WECOM", bot_id="bot-1"),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text="共享追尾正文"),
    )


def _real_executor_factory() -> ExecutorFactory:
    async def factory(request: ExecutorRequest) -> RunExecutor:
        provider = OpenAICompatibleProvider(
            base_url=request.model.base_url,
            model=request.model.model_id,
            api_key=request.model.api_key or None,
        )
        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry(),
            hooks=HookPipeline(),
            runtime_events=request.runtime_events,
        )
        return AgentRunnerExecutor(runner=runner, request=request, close=provider.aclose)

    return factory


def _run_service(
    tenant: TailTenant, hub: EventTailHub, *, base_url: str
) -> tuple[RunService, AsyncSession, ExecutionSupervisor]:
    """真实 RunService：注入本用例的共享追尾 hub；执行器仍是生产真实链路。"""
    session = get_session_factory()()
    supervisor = ExecutionSupervisor()
    service = RunService(
        session,
        FakeResolveClient(response=_resolve_response(tenant, base_url)),
        "shared-tail-instance",
        NullPlatformSettingsClient(),
        executor_factory=_real_executor_factory(),
        supervisor=supervisor,
        tail_hub=hub,
    )
    return service, session, supervisor


@asynccontextmanager
async def started_run(
    tenant: TailTenant, base_url: str, *, hub: EventTailHub, release: asyncio.Event | None = None
) -> AsyncIterator[StartedRun]:
    service, session, supervisor = _run_service(tenant, hub, base_url=base_url)
    try:
        start = await service.start(_run_request(tenant), tenant.tenant_id)
        yield StartedRun(service=service, supervisor=supervisor, session=session, start=start)
    finally:
        if release is not None:
            release.set()
        await supervisor.close()
        await session.close()


class TailRecorder:
    """真实订阅者：后台消费 `hub.tail` 的批次；行/滞后/checkpoint 全部留痕，不打断生成器。"""

    def __init__(
        self,
        hub: EventTailHub,
        *,
        tenant_id: str,
        conversation_id: uuid.UUID,
        after_seq: int = 0,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._hub = hub
        self._tenant_id = tenant_id
        self._conversation_id = conversation_id
        self._after_seq = after_seq
        self._gate = gate
        self.rows: list[CanonicalEventView] = []
        self.batches = 0
        self.caught_up_batches = 0
        self.lagged_batches = 0
        self.checkpoints: list[int] = []
        self.error: BaseException | None = None
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        try:
            async for batch in self._hub.tail(
                tenant_id=self._tenant_id,
                conversation_id=self._conversation_id,
                after_seq=self._after_seq,
            ):
                self.batches += 1
                if batch.lagged:
                    self.lagged_batches += 1
                if batch.caught_up:
                    self.caught_up_batches += 1
                    self.checkpoints.append(batch.through_seq)
                self.rows.extend(batch.rows)
                self._queue.put_nowait(batch)
                if self._gate is not None:
                    # 消费者暂停：生成器悬停在 yield，谁都不请求下一批（真实慢消费者）。
                    await self._gate.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 订阅异常留给用例显式断言，不静默
            self.error = exc
            self._queue.put_nowait(exc)

    async def _next_batch(self, *, timeout: float) -> TailBatch:
        item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        if isinstance(item, BaseException):
            raise AssertionError(f"订阅失败：{item!r}") from item
        return item

    async def wait_rows(self, minimum: int, *, timeout: float = WAIT_TIMEOUT_SEC) -> None:
        deadline = time.monotonic() + timeout
        while len(self.rows) < minimum:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pytest.fail(f"追尾等待超时：已收 {len(self.rows)} 行，期望至少 {minimum} 行")
            await self._next_batch(timeout=remaining)

    async def wait_caught_up(self, *, timeout: float = WAIT_TIMEOUT_SEC) -> None:
        """等到订阅者追平水位：此刻唤醒已注册，之后提交的行由唤醒而非轮询送到。"""
        if self.caught_up_batches:
            return
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pytest.fail("追尾等待超时：订阅者没有追平水位")
            batch = await self._next_batch(timeout=remaining)
            if batch.caught_up:
                return

    async def wait_through_seq(self, seq: int, *, timeout: float = WAIT_TIMEOUT_SEC) -> None:
        """等到已提交到 `seq` 的行被送达（补查路径的确定性条件）。"""
        deadline = time.monotonic() + timeout
        while not (self.rows and self.rows[-1].seq >= seq):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pytest.fail(f"追尾等待超时：未送达 seq {seq}（已到 {self.seqs[-1:] or '无'}）")
            await self._next_batch(timeout=remaining)

    async def close(self) -> None:
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task

    @property
    def seqs(self) -> list[int]:
        return [row.seq for row in self.rows]


# --------------------------------------------------------------------------- #
# 通用等待/读取辅助（全部读真实 PG 事实）
# --------------------------------------------------------------------------- #


async def _wait_for(
    predicate: Callable[[], bool], *, what: str, timeout: float = WAIT_TIMEOUT_SEC
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(POLL_SEC)
    pytest.fail(f"等待超时（{timeout}s）：{what}")


async def _wait_until(
    predicate: Callable[[], Awaitable[bool]], *, what: str, timeout: float = WAIT_TIMEOUT_SEC
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(POLL_SEC)
    pytest.fail(f"等待超时（{timeout}s）：{what}")


async def _drain_run(start: RunStart) -> list[ExecutorEvent]:
    return [event async for event in start.events]


async def _collect_events(
    events: AsyncIterator[ExecutorEvent], *, limit: int, timeout: float = WAIT_TIMEOUT_SEC
) -> list[ExecutorEvent]:
    """按上限收集事件后主动关闭流（Run 仍在执行时流本应保持打开）。"""
    collected: list[ExecutorEvent] = []

    async def consume() -> None:
        async for event in events:
            collected.append(event)
            if len(collected) >= limit:
                return

    try:
        await asyncio.wait_for(consume(), timeout=timeout)
    finally:
        await events.aclose()
    return collected


async def _watermark(hub: EventTailHub, tenant: TailTenant, conversation_id: uuid.UUID) -> int:
    return await hub.watermark(tenant_id=tenant.tenant_id, conversation_id=conversation_id)


async def _run_status(run_id: uuid.UUID) -> str | None:
    async with get_session_factory()() as session:
        return await session.scalar(sa.select(RunRecord.status).where(RunRecord.id == run_id))


async def _wait_run_terminal(run_id: uuid.UUID) -> None:
    async def terminal() -> bool:
        status = await _run_status(run_id)
        return status is not None and status in {str(item) for item in _TERMINAL_STATUSES}

    await _wait_until(terminal, what="Run 行进入终态（终态行与状态同事务提交）")


_TERMINAL_STATUSES = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)


async def _wait_all_caught_up(
    recorders: Sequence[TailRecorder],
    hub: EventTailHub,
    tenant: TailTenant,
    conversation_id: uuid.UUID,
) -> int:
    async def caught_up() -> bool:
        watermark = await _watermark(hub, tenant, conversation_id)
        return all(rec.rows and rec.rows[-1].seq >= watermark for rec in recorders)

    await _wait_until(caught_up, what="全部订阅者追平真实持久水位")
    return await _watermark(hub, tenant, conversation_id)


async def _wait_delta_committed(
    recorders: Sequence[TailRecorder],
    hub: EventTailHub,
    tenant: TailTenant,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    """本 Run 的第一块增量已提交且全部订阅者已追平：此刻"流式中"事实成立，可以开始计量。"""

    async def committed() -> bool:
        async with get_session_factory()() as session:
            count = await session.scalar(
                sa.select(sa.func.count())
                .select_from(CanonicalEvent)
                .where(
                    CanonicalEvent.run_id == run_id,
                    CanonicalEvent.stream_type == DELTA_STREAM_TYPE,
                    CanonicalEvent.is_deleted.is_(False),
                )
            )
        return bool(count) and await _all_caught_up(recorders, hub, tenant, conversation_id)

    await _wait_until(committed, what="本 Run 第一块增量已提交且全部订阅者追平")


async def _all_caught_up(
    recorders: Sequence[TailRecorder],
    hub: EventTailHub,
    tenant: TailTenant,
    conversation_id: uuid.UUID,
) -> bool:
    watermark = await _watermark(hub, tenant, conversation_id)
    return all(rec.rows and rec.rows[-1].seq >= watermark for rec in recorders)


def _checked_out_connections() -> int:
    engine = get_engine()
    pool = cast(QueuePool, engine.sync_engine.pool)
    return pool.checkedout()


async def _seed_conversation(
    tenant: TailTenant,
    *,
    status: str = "RUNNING",
    conversation_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """真实会话 + Run 行（本用例只验证追尾对既有持久事实的读取，不重跑受理事务）。"""
    async with get_session_factory()() as session:
        if conversation_id is None:
            conversation = Conversation(
                tenant_id=tenant.tenant_id,
                user_id=tenant.user_id,
                agent_id=tenant.agent_id,
                status="ACTIVE",
                last_seq=0,
            )
            session.add(conversation)
            await session.flush()
            conversation_id = conversation.id
        run = RunRecord(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
            user_id=tenant.user_id,
            agent_id=tenant.agent_id,
            status=status,
            input_text="seed",
            deadline_at=datetime.now(UTC) + timedelta(minutes=5),
            trace_id=uuid.uuid4().hex,
            cancel_requested=False,
        )
        session.add(run)
        await session.commit()
        return conversation_id, run.id


async def _append_rows(
    tenant: TailTenant,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    rows: list[tuple[str, str | None, dict[str, Any]]],
) -> list[int]:
    """真实 `EventWriter`：会话行锁内分配 seq（与生产写入同一条路径）。"""
    async with get_session_factory()() as session:
        writer = EventWriter(session)
        seqs = [
            await writer.append(
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                run_id=run_id,
                event_type=event_type,
                stream_type=stream_type,
                payload=payload,
            )
            for event_type, stream_type, payload in rows
        ]
        await session.commit()
    return seqs


async def _soft_delete_through(conversation_id: uuid.UUID, seq: int) -> None:
    """保留策略的真实状态：最早可用行被淘汰（TASK-069 的清理动作在本用例只建立状态）。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.update(CanonicalEvent)
            .where(CanonicalEvent.conversation_id == conversation_id, CanonicalEvent.seq <= seq)
            .values(is_deleted=True)
        )
        await session.commit()


async def _set_run_status(run_id: uuid.UUID, status: str) -> None:
    """Reaper 路径的真实状态转换（本用例只建立状态，不重跑回收循环）。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.update(RunRecord)
            .where(RunRecord.id == run_id)
            .values(status=status, error_code="RUN_ABANDONED", update_time=sa.func.now())
        )
        await session.commit()


async def _canonical_row_count(conversation_id: uuid.UUID) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(CanonicalEvent)
                .where(CanonicalEvent.conversation_id == conversation_id)
            )
        )


def _assert_complete_and_unique(recorder: TailRecorder, watermark: int) -> None:
    """每个订阅者都拿到 {1..watermark} 恰好一次：不丢（无缺口）、不重（无重复 seq）。"""
    seqs = recorder.seqs
    assert seqs == sorted(seqs), f"seq 必须递增：{seqs}"
    assert len(seqs) == len(set(seqs)), f"同一订阅者不得重复收到 seq：{seqs}"
    assert seqs == list(range(1, watermark + 1)), f"应无缺口地覆盖 {watermark} 行，实际 {seqs}"


# --------------------------------------------------------------------------- #
# 场景 1：同会话共享上游追尾 + 提交后唤醒
# --------------------------------------------------------------------------- #


async def _measure_shared_rounds(
    tenant: TailTenant, hub: EventTailHub, *, subscribers: int, chunks: tuple[str, ...]
) -> tuple[int, int]:
    """一个真实 Run（首块后闸门）配 N 个订阅者：返回（上游读取轮次增量，真实水位）。"""
    script = ModelProbeScript(chunks=chunks, hold_after=1)
    async with model_probe(script) as base_url, started_run(
        tenant, base_url, hub=hub, release=script.release
    ) as running:
        recorders = [
            TailRecorder(
                hub,
                tenant_id=tenant.tenant_id,
                conversation_id=running.conversation_id,
                after_seq=0,
            )
            for _ in range(subscribers)
        ]
        try:
            await asyncio.gather(*(rec.wait_caught_up() for rec in recorders))
            await _wait_delta_committed(
                recorders, hub, tenant, running.conversation_id, running.run_id
            )
            assert script.sent == 1 and not script.release.is_set(), "探针停在第二块之前"
            before = hub.stats().upstream_rounds
            started = time.monotonic()
            script.release.set()
            await _wait_run_terminal(running.run_id)
            watermark = await _wait_all_caught_up(
                recorders, hub, tenant, running.conversation_id
            )
            elapsed = time.monotonic() - started
        finally:
            await asyncio.gather(*(rec.close() for rec in recorders))
        assert elapsed < WAKEUP_BUDGET_SEC, (
            f"{subscribers} 个订阅者在 {elapsed:.2f}s 内追平；补查周期是 {FAR_FALLBACK_SEC}s，"
            "只能由提交后唤醒解释"
        )
        for recorder in recorders:
            _assert_complete_and_unique(recorder, watermark)
        return hub.stats().upstream_rounds - before, watermark


async def test_b260_shared_tail_reads_do_not_grow_with_observers(
    tail_tenant: TailTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """契约：同 Run/会话共享上游追尾（观察者不放大查询）+ 提交后唤醒（不轮询空转）。"""
    monkeypatch.setenv("STREAM_DELTA_FLUSH_INTERVAL_MS", "50")
    monkeypatch.setenv("STREAM_DELTA_FLUSH_BYTES", "65536")
    hub = EventTailHub(fallback_poll_sec=FAR_FALLBACK_SEC)
    chunks = ("甲段正文。", "乙段正文。", "丙段正文。")
    try:
        single, watermark = await _measure_shared_rounds(
            tail_tenant, hub, subscribers=1, chunks=chunks
        )
        shared, shared_watermark = await _measure_shared_rounds(
            tail_tenant, hub, subscribers=3, chunks=chunks
        )
    finally:
        await hub.aclose()

    assert single >= 1 and shared >= 1, "共享上游必须真的读过上游"
    assert shared <= single + 3, (
        f"上游读取轮次不得随观察者数量线性放大：1 个订阅者 {single} 轮，3 个 {shared} 轮"
    )
    assert shared < 3 * max(watermark, 1), (
        f"共享读取必须远小于「订阅者×事件数」：{shared} vs 3×{watermark}"
    )
    assert shared_watermark >= len(chunks), "共享追尾不得丢事件"
    assert watermark >= len(chunks)


# --------------------------------------------------------------------------- #
# 场景 2：断线重连按水位补拉缺失区间
# --------------------------------------------------------------------------- #


async def test_b260_reconnect_backfills_missing_interval_without_loss_or_duplication(
    tail_tenant: TailTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """契约：断线后按水位补拉缺失区间——不丢、不重、不跳号。"""
    monkeypatch.setenv("STREAM_DELTA_FLUSH_INTERVAL_MS", "50")
    monkeypatch.setenv("STREAM_DELTA_FLUSH_BYTES", "65536")
    hub = EventTailHub(fallback_poll_sec=FAR_FALLBACK_SEC)
    script = ModelProbeScript(chunks=("第一段正文。", "第二段正文。"), hold_after=1)
    try:
        async with model_probe(script) as base_url, started_run(
            tail_tenant, base_url, hub=hub, release=script.release
        ) as running:
            first = TailRecorder(
                hub,
                tenant_id=tail_tenant.tenant_id,
                conversation_id=running.conversation_id,
                after_seq=0,
            )
            try:
                await first.wait_caught_up()
                await _wait_delta_committed(
                    [first], hub, tail_tenant, running.conversation_id, running.run_id
                )
                assert script.sent == 1, "断线前模型只发过第一块"
                applied = first.rows[-1].seq
                assert applied >= 1
            finally:
                await first.close()  # 断线：订阅者停止消费（连接关闭）

            script.release.set()  # 断线期间继续提交（无人订阅）
            await _drain_run(running.start)
            await _wait_run_terminal(running.run_id)
            watermark = await _watermark(hub, tail_tenant, running.conversation_id)
            assert watermark > applied, "断线期间确实有新事件提交"

            resumed = TailRecorder(
                hub,
                tenant_id=tail_tenant.tenant_id,
                conversation_id=running.conversation_id,
                after_seq=applied,
            )
            try:
                await resumed.wait_rows(watermark - applied)
                await _wait_all_caught_up(
                    [resumed], hub, tail_tenant, running.conversation_id
                )
                resumed_rows = list(resumed.rows)
            finally:
                await resumed.close()
    finally:
        await hub.aclose()

    assert resumed_rows[0].seq == applied + 1, "重连从已应用游标之后开始，不重复已确认行"
    assert [row.seq for row in resumed_rows] == list(range(applied + 1, watermark + 1))
    assert first.seqs == list(range(1, applied + 1))
    assert first.seqs + [row.seq for row in resumed_rows] == list(range(1, watermark + 1)), (
        "两段拼接恰好覆盖 {1..水位}：不丢不重"
    )


# --------------------------------------------------------------------------- #
# 场景 3：Redis hint 故障退化 + 真实跨进程唤醒
# --------------------------------------------------------------------------- #


async def test_b260_hint_failure_keeps_writes_and_local_wakeup(
    tail_tenant: TailTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """契约：Redis 仅 hint——不可达时写入路径与本地唤醒不受影响，降级显式可见。"""
    monkeypatch.setenv("STREAM_DELTA_FLUSH_INTERVAL_MS", "50")
    monkeypatch.setenv("STREAM_DELTA_FLUSH_BYTES", "65536")
    hub = EventTailHub(
        fallback_poll_sec=NEAR_FALLBACK_SEC, hint=RedisWakeupHint("redis://127.0.0.1:1/0")
    )
    script = ModelProbeScript(chunks=("退化一。", "退化二。"))
    try:
        async with model_probe(script) as base_url, started_run(
            tail_tenant, base_url, hub=hub
        ) as running:
            recorder = TailRecorder(
                hub,
                tenant_id=tail_tenant.tenant_id,
                conversation_id=running.conversation_id,
                after_seq=0,
            )
            try:
                await recorder.wait_caught_up()
                await _drain_run(running.start)
                await _wait_run_terminal(running.run_id)
                watermark = await _wait_all_caught_up(
                    [recorder], hub, tail_tenant, running.conversation_id
                )
                _assert_complete_and_unique(recorder, watermark)
            finally:
                await recorder.close()
    finally:
        await hub.aclose()

    stats = hub.stats()
    assert stats.hint_degraded, "Redis 不可达必须显式降级（可观测），不是静默丢弃唤醒"
    assert stats.hint_failures >= 1, "降级必须有失败计数（可观测）"
    assert stats.wakeups >= 1, "hint 故障不得让本进程内的提交后唤醒失效"


async def test_b260_lost_hint_falls_back_to_bounded_pg_catch_up(
    tail_tenant: TailTenant,
) -> None:
    """契约：唤醒提示丢失（写入方未发布/跨进程提示未达）时按 PG 有界补查继续投递。"""
    hub = EventTailHub(
        fallback_poll_sec=NEAR_FALLBACK_SEC, hint=RedisWakeupHint("redis://127.0.0.1:1/0")
    )
    conversation_id, run_id = await _seed_conversation(tail_tenant)
    recorder = TailRecorder(
        hub,
        tenant_id=tail_tenant.tenant_id,
        conversation_id=conversation_id,
        after_seq=0,
    )
    try:
        await recorder.wait_caught_up()  # 空会话追平并注册等待
        started = time.monotonic()
        # 提示整体丢失：只提交事实，不发布任何唤醒（等价写入方在别的进程且提示未达）。
        seqs = await _append_rows(
            tail_tenant,
            conversation_id,
            run_id,
            [(DELTA_STREAM_TYPE, DELTA_STREAM_TYPE, {"delta": "无提示提交。", "reply_generation": 0})],
        )
        await recorder.wait_through_seq(seqs[-1])
        elapsed = time.monotonic() - started
    finally:
        await recorder.close()
        await hub.aclose()

    assert recorder.seqs == seqs, "补查拿到的是已提交事实"
    assert hub.stats().fallback_polls >= 1, "退化路径必须真的走过超时补查"
    assert hub.stats().hint_degraded, "不可达的 hint 必须显式降级"
    assert elapsed < WAKEUP_BUDGET_SEC, (
        f"提示丢失后仍必须在有界补查预算内投递（{elapsed:.2f}s）"
    )


async def test_b260_redis_hint_wakes_subscriber_published_from_another_connection(
    tail_tenant: TailTenant, async_tool_datastore: dict[str, str]
) -> None:
    """契约：Redis Pub/Sub 只作通知——另一连接（等价另一个进程）发布提示即可唤醒。"""
    from redis.asyncio import Redis

    hub = EventTailHub(
        fallback_poll_sec=FAR_FALLBACK_SEC,
        hint=RedisWakeupHint(async_tool_datastore["redis_url"]),
    )
    publisher = Redis.from_url(async_tool_datastore["redis_url"], decode_responses=True)
    conversation_id, run_id = await _seed_conversation(tail_tenant)
    try:
        recorder = TailRecorder(
            hub,
            tenant_id=tail_tenant.tenant_id,
            conversation_id=conversation_id,
            after_seq=0,
        )
        try:
            await recorder.wait_caught_up()
            # 监听订阅就绪后才发布：真实 Redis 订阅者计数是确定性前提。
            for _ in range(200):
                counts = await publisher.pubsub_numsub(WAKEUP_CHANNEL)
                if sum(int(count) for _, count in counts) >= 1:
                    break
                await asyncio.sleep(POLL_SEC)
            counts = await publisher.pubsub_numsub(WAKEUP_CHANNEL)
            assert sum(int(count) for _, count in counts) >= 1, "hub 必须真实订阅唤醒通道"

            seqs = await _append_rows(
                tail_tenant,
                conversation_id,
                run_id,
                [(DELTA_STREAM_TYPE, DELTA_STREAM_TYPE, {"delta": "远端提交", "reply_generation": 0})],
            )
            started = time.monotonic()
            await publisher.publish(WAKEUP_CHANNEL, f"{tail_tenant.tenant_id}|{conversation_id}")
            await recorder.wait_rows(len(seqs))
            elapsed = time.monotonic() - started
        finally:
            await recorder.close()
    finally:
        await publisher.aclose()
        await hub.aclose()

    assert elapsed < WAKEUP_BUDGET_SEC, (
        f"跨进程提示必须唤醒等待中的订阅者（{elapsed:.2f}s < {FAR_FALLBACK_SEC}s）"
    )
    assert recorder.seqs == seqs, "唤醒后读水位追平：拿到的是已提交事实"


# --------------------------------------------------------------------------- #
# 场景 4：退出 session 才 yield + 心跳不推进业务游标
# --------------------------------------------------------------------------- #


async def test_b260_yields_outside_session_so_slow_subscriber_holds_no_connection(
    tail_tenant: TailTenant,
) -> None:
    """契约：退出读取事务后才 yield——慢消费者暂停时生成器悬停在 yield，不占数据库连接。"""
    hub = EventTailHub(fallback_poll_sec=FAR_FALLBACK_SEC)
    conversation_id, run_id = await _seed_conversation(tail_tenant)
    await _append_rows(
        tail_tenant,
        conversation_id,
        run_id,
        [
            ("USER_MESSAGE", None, {"text": "在吗"}),
            (DELTA_STREAM_TYPE, DELTA_STREAM_TYPE, {"delta": "在的。", "reply_generation": 0}),
        ],
    )
    baseline = _checked_out_connections()
    gate = asyncio.Event()
    recorder = TailRecorder(
        hub,
        tenant_id=tail_tenant.tenant_id,
        conversation_id=conversation_id,
        after_seq=0,
        gate=gate,
    )
    try:
        await recorder.wait_rows(2)
        await asyncio.sleep(0.1)  # 让 pump 停在闸门上（消费者暂停）
        paused = _checked_out_connections()
        await _append_rows(
            tail_tenant,
            conversation_id,
            run_id,
            [(DELTA_STREAM_TYPE, DELTA_STREAM_TYPE, {"delta": "继续。", "reply_generation": 0})],
        )
        await hub.publish(tenant_id=tail_tenant.tenant_id, conversation_id=conversation_id)
        paused_after_commit = _checked_out_connections()
        gate.set()
        await recorder.wait_rows(3)
    finally:
        await recorder.close()
        await hub.aclose()

    assert baseline == 0, f"用例自身不得占连接（基线 {baseline}）"
    assert paused == baseline, f"yield 前必须已归还连接：暂停时 {paused}"
    assert paused_after_commit == baseline, (
        f"暂停期间提交/唤醒也不得占连接：{paused_after_commit}"
    )
    assert recorder.seqs == [1, 2, 3], "恢复消费能拿到暂停期间新提交的行，且不跳号"


async def test_b260_slow_subscriber_rebuilds_from_persisted_watermark(
    tail_tenant: TailTenant,
) -> None:
    """契约：共享窗口有界——慢消费者被淘汰后按持久水位补查重建，不丢不重（降级可见）。"""
    hub = EventTailHub(fallback_poll_sec=FAR_FALLBACK_SEC, page_size=2, window_rows=2)
    conversation_id, run_id = await _seed_conversation(tail_tenant)
    await _append_rows(
        tail_tenant,
        conversation_id,
        run_id,
        [
            (DELTA_STREAM_TYPE, DELTA_STREAM_TYPE, {"delta": f"第{index}段。", "reply_generation": 0})
            for index in range(1, 7)
        ],
    )
    gate = asyncio.Event()
    slow = TailRecorder(
        hub,
        tenant_id=tail_tenant.tenant_id,
        conversation_id=conversation_id,
        after_seq=0,
        gate=gate,
    )
    fast = TailRecorder(
        hub,
        tenant_id=tail_tenant.tenant_id,
        conversation_id=conversation_id,
        after_seq=0,
    )
    try:
        await slow.wait_rows(1)
        await asyncio.sleep(0.1)  # 慢消费者停在闸门上（不再请求下一批）
        await fast.wait_rows(6)  # 快消费者把共享窗口推过慢消费者的游标
        await _wait_all_caught_up([fast], hub, tail_tenant, conversation_id)
        gate.set()
        await slow.wait_rows(6)
        await _wait_all_caught_up([slow], hub, tail_tenant, conversation_id)
    finally:
        await slow.close()
        await fast.close()
        await hub.aclose()

    assert slow.lagged_batches >= 1, "窗口淘汰后必须走按水位补查的重建路径（可观测）"
    assert slow.seqs == list(range(1, 7)), f"重建后不丢不重：{slow.seqs}"
    assert fast.seqs == list(range(1, 7))


async def test_b260_heartbeat_does_not_advance_business_cursor(
    tail_tenant: TailTenant,
) -> None:
    """契约：心跳不推进业务游标——心跳帧与业务序号无关，后续行仍连续。"""
    hub = EventTailHub(fallback_poll_sec=FAR_FALLBACK_SEC)
    conversation_id, run_id = await _seed_conversation(tail_tenant)
    await _append_rows(
        tail_tenant,
        conversation_id,
        run_id,
        [
            (DELTA_STREAM_TYPE, DELTA_STREAM_TYPE, {"delta": "第一段。", "reply_generation": 0}),
            (DELTA_STREAM_TYPE, DELTA_STREAM_TYPE, {"delta": "第二段。", "reply_generation": 0}),
        ],
    )
    service, session, supervisor = _run_service(
        tail_tenant, hub, base_url="http://127.0.0.1:9/v1"
    )
    frames: list[str] = []

    async def pump(frames_in: AsyncIterator[str]) -> None:
        async for frame in frames_in:
            frames.append(frame)

    try:
        tail = await service.tail_run_events(run_id, tail_tenant.tenant_id, after_seq=0)
        task = asyncio.create_task(
            pump(iter_sse_frames(tail.events, run_id=run_id, heartbeat_sec=0.05))
        )
        try:
            await _wait_for(
                lambda: _business_seqs(frames) == [1, 2],
                what="心跳期间先追平已提交行",
            )
            await _wait_for(
                lambda: sum(1 for frame in frames if frame == HEARTBEAT_FRAME) >= 2,
                what="长连接上真实出现心跳帧",
            )
            assert _business_seqs(frames) == [1, 2], "心跳不得推进业务游标"

            await _append_rows(
                tail_tenant,
                conversation_id,
                run_id,
                [(DELTA_STREAM_TYPE, DELTA_STREAM_TYPE, {"delta": "第二段。", "reply_generation": 0})],
            )
            await hub.publish(tenant_id=tail_tenant.tenant_id, conversation_id=conversation_id)
            await _wait_for(
                lambda: _business_seqs(frames) == [1, 2, 3],
                what="心跳之后按连续 seq 送出后续行",
            )
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    finally:
        await supervisor.close()
        await session.close()
        await hub.aclose()

    assert _business_seqs(frames) == [1, 2, 3], "心跳不推进游标：后续 seq 连续，无跳号"


def _business_seqs(frames: list[str]) -> list[int]:
    """从真实 SSE 帧里取业务序号（心跳帧没有 data，不参与业务游标）。"""
    seqs: list[int] = []
    for frame in frames:
        for line in frame.splitlines():
            if not line.startswith("data:"):
                continue
            payload = json.loads(line[len("data:") :].lstrip())
            seqs.append(int(payload["seq"]))
    return seqs


# --------------------------------------------------------------------------- #
# 场景 5：过滤跳号 / 重复 seq / checkpoint / 游标分型
# --------------------------------------------------------------------------- #


async def test_b06_filtered_gaps_duplicate_cursor_and_checkpoint_are_distinct(
    tail_tenant: TailTenant,
) -> None:
    """契约：过滤跳号合法、重复 seq 不重投、checkpoint 只回真实水位、超前/过期分别处理。"""
    hub = EventTailHub(fallback_poll_sec=FAR_FALLBACK_SEC)
    conversation_id, run_id = await _seed_conversation(tail_tenant)
    await _append_rows(
        tail_tenant,
        conversation_id,
        run_id,
        [
            ("USER_MESSAGE", None, {"text": "提问"}),
            (DELTA_STREAM_TYPE, DELTA_STREAM_TYPE, {"delta": "公开一。", "reply_generation": 0}),
            ("MODEL_CALL_STARTED", INTERNAL_STREAM_TYPE, {"attempt": 1}),
            (DELTA_STREAM_TYPE, DELTA_STREAM_TYPE, {"delta": "公开二。", "reply_generation": 0}),
            ("TOOL_CALL", "tool.completed", {"tool": "internal"}),
            ("ASSISTANT_MESSAGE", None, {"text": "公开一。公开二。"}),
        ],
    )
    try:
        watermark = await _watermark(hub, tail_tenant, conversation_id)
        assert watermark == 6

        # 过滤跳号：内部事件被公开投影过滤 → 公开 seq 允许不连续，但**不是**游标过期。
        recorder = TailRecorder(
            hub,
            tenant_id=tail_tenant.tenant_id,
            conversation_id=conversation_id,
            after_seq=0,
        )
        try:
            await recorder.wait_rows(6)
        finally:
            await recorder.close()
        public_seqs = [
            row.seq
            for row in recorder.rows
            if project_public_stream_event(row, {}) is not None
        ]
        assert public_seqs == [2, 4], f"过滤后的公开序号允许跳号：{public_seqs}"
        assert recorder.seqs == list(range(1, 7)), "追尾仍按 seq 连续覆盖全部 canonical 行"

        # 重复 seq：游标是排他的——`seq <= 游标` 一律不重复产出。
        replay = TailRecorder(
            hub,
            tenant_id=tail_tenant.tenant_id,
            conversation_id=conversation_id,
            after_seq=public_seqs[-1],
        )
        try:
            await replay.wait_caught_up()
            assert replay.seqs == [5, 6], f"游标之后才产出（不含游标本身）：{replay.seqs}"
        finally:
            await replay.close()

        # checkpoint：只回真实持久水位，不另分配 canonical seq。
        rows_before = await _canonical_row_count(conversation_id)
        checkpoint = await hub.checkpoint(
            tenant_id=tail_tenant.tenant_id, conversation_id=conversation_id
        )
        assert checkpoint == watermark == 6, "checkpoint 必须是真实持久水位"
        assert await _canonical_row_count(conversation_id) == rows_before, (
            "取 checkpoint 不得分配新 canonical seq"
        )

        # 超前游标：显式 CURSOR_AHEAD（不静默夹取）。
        with pytest.raises(AppError) as ahead:
            await hub.check_cursor(
                tenant_id=tail_tenant.tenant_id,
                conversation_id=conversation_id,
                after_seq=checkpoint + 1,
            )
        assert ahead.value.code == ErrorCode.CURSOR_AHEAD
        assert hub.stats().cursor_ahead >= 1

        # 过滤跳号（未过期）不是游标过期：游标停在跳号之前仍可续跟。
        await hub.check_cursor(
            tenant_id=tail_tenant.tenant_id,
            conversation_id=conversation_id,
            after_seq=3,
        )

        # 已过期游标：最早可用行被保留策略淘汰 → 显式 CURSOR_EXPIRED（带上最早可用水位）。
        await _soft_delete_through(conversation_id, 3)
        with pytest.raises(AppError) as expired:
            await hub.check_cursor(
                tenant_id=tail_tenant.tenant_id,
                conversation_id=conversation_id,
                after_seq=0,
            )
        assert expired.value.code == ErrorCode.CURSOR_EXPIRED
        assert expired.value.data["earliest_seq"] == 4
        await hub.check_cursor(
            tenant_id=tail_tenant.tenant_id,
            conversation_id=conversation_id,
            after_seq=3,
        )
    finally:
        await hub.aclose()


# --------------------------------------------------------------------------- #
# 场景 6：重建快照不混 Run + 终态不先于最后持久正文
# --------------------------------------------------------------------------- #


async def test_b06_rebuilt_stream_keeps_runs_separate_and_terminal_after_body(
    tail_tenant: TailTenant,
) -> None:
    """契约：Run 作用域不混 Run；终态排在全部已持久正文之后（含 Reaper 合成终态）。"""
    # 本用例只验证追尾的 Run 作用域与终态顺序：Reaper 路径不带提交后唤醒，用有界补查的
    # 节拍把"缺终态行时按 Run 记录合成终态"在预算内取回（生产补查周期是 5s）。
    hub = EventTailHub(fallback_poll_sec=NEAR_FALLBACK_SEC)
    conversation_id, first_run = await _seed_conversation(tail_tenant, status="COMPLETED")
    await _append_rows(
        tail_tenant,
        conversation_id,
        first_run,
        [
            ("USER_MESSAGE", None, {"text": "第一轮"}),
            (DELTA_STREAM_TYPE, DELTA_STREAM_TYPE, {"delta": "第一轮正文。", "reply_generation": 0}),
            ("RUN_COMPLETED", "run.completed", {"status": "COMPLETED", "final_text": "第一轮正文。"}),
        ],
    )
    _, second_run = await _seed_conversation(
        tail_tenant, status="RUNNING", conversation_id=conversation_id
    )
    second_seqs = await _append_rows(
        tail_tenant,
        conversation_id,
        second_run,
        [
            (DELTA_STREAM_TYPE, DELTA_STREAM_TYPE, {"delta": "第二轮一。", "reply_generation": 0}),
            (DELTA_STREAM_TYPE, DELTA_STREAM_TYPE, {"delta": "第二轮二。", "reply_generation": 0}),
        ],
    )
    service, session, supervisor = _run_service(
        tail_tenant, hub, base_url="http://127.0.0.1:9/v1"
    )
    try:
        # 重建快照不混 Run：追第二轮只出第二轮的行（首轮正文不得混入）。
        second_tail = await service.tail_run_events(
            second_run, tail_tenant.tenant_id, after_seq=0
        )
        second_events = await _collect_events(second_tail.events, limit=len(second_seqs))
        assert [event.seq for event in second_events] == second_seqs
        assert all(event.type != "run.completed" for event in second_events)

        # 终态不先于最后持久正文：首轮终态行 seq 大于全部正文行，且之后没有本 Run 正文。
        first_tail = await service.tail_run_events(first_run, tail_tenant.tenant_id, after_seq=0)
        first_events = await asyncio.wait_for(_drain_run(first_tail), timeout=WAIT_TIMEOUT_SEC)
        terminal_seq = first_events[-1].seq
        assert terminal_seq is not None
        assert first_events[-1].type == "run.completed"
        assert terminal_seq == max(event.seq or 0 for event in first_events)
        async with get_session_factory()() as db:
            late_body = await db.scalar(
                sa.select(sa.func.count())
                .select_from(CanonicalEvent)
                .where(
                    CanonicalEvent.run_id == first_run,
                    CanonicalEvent.seq > terminal_seq,
                    CanonicalEvent.is_deleted.is_(False),
                )
            )
        assert late_body == 0, "终态之后不得再出现本 Run 的已持久正文"

        # Reaper 路径：Run 行已终态、没有终态行 → 合成终态仍在全部已持久正文之后。
        await _append_rows(
            tail_tenant,
            conversation_id,
            second_run,
            [(DELTA_STREAM_TYPE, DELTA_STREAM_TYPE, {"delta": "崩溃前正文。", "reply_generation": 0})],
        )
        await _set_run_status(second_run, str(RunStatus.FAILED))
        await hub.publish(tenant_id=tail_tenant.tenant_id, conversation_id=conversation_id)
        crashed_tail = await service.tail_run_events(
            second_run, tail_tenant.tenant_id, after_seq=0
        )
        crashed_events = await asyncio.wait_for(
            _drain_run(crashed_tail), timeout=WAIT_TIMEOUT_SEC
        )
    finally:
        await supervisor.close()
        await session.close()
        await hub.aclose()

    body_seqs = [event.seq or 0 for event in crashed_events if event.type == DELTA_STREAM_TYPE]
    terminal = crashed_events[-1]
    assert body_seqs, "崩溃前已提交的正文必须全部先送达"
    assert terminal.type == "run.failed", f"缺终态行时按 Run 记录合成终态：{terminal.type}"
    assert (terminal.seq or 0) >= max(body_seqs), "合成终态不得先于最后持久正文"
