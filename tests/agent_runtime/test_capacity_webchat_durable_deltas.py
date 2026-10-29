"""B-259：模型增量有界合并与终态前 flush（TASK-059；design §3.6 公开流与持久写入 / §3.7 配置契约）。

真实边界（不 mock 存储或租约）：

- **真实 Runtime 业务服务**：`RunService.start` 受理 + 真实 `AgentRunnerExecutor` → `AgentRunner`
  → `OpenAICompatibleProvider` 调用链；装配只决定"模型端点在哪儿"，不替换存储、租约或执行器；
- **真实 HTTP 模型探针**：进程内 uvicorn 的 `text/event-stream` 端点，按脚本逐块下发正文；块间
  节拍与"闸门"（发完第 N 块后等测试放行）都发生在真实 socket 上，不是 provider 替身；
- **隔离 PostgreSQL**（空库 + `alembic upgrade head`，跑完即 drop）与隔离 Redis 号位：会话/冻结
  快照/Run/canonical 事件/租约都是真实行，`seq` 由真实 `EventWriter` 在会话行锁内分配；
- **真实进程崩溃**：崩溃场景把真实 RunService 放进子进程，用不可捕获的 SIGKILL 在模型仍在产出时
  杀死它（无收尾、无 flush、无终态），不伪造存储或租约。

关键断言（B-259 契约）：

1. **有界合并（时间或字节窗口）**：小增量按窗口合并后批量写 canonical —— 增量行数由窗口边界界定
   （单行 ≤ `flush_bytes` + 单块上限，且至少一行由字节边界触发），远小于模型块数，绝不逐 token 一次
   事务；时间边界在没有新字节到达时同样触发落库；空窗口的 drain 不产生空事件。
2. **先提交再推送**：时间边界在模型停顿期间落库 —— 第一块正文在"探针尚未发出第二块"时已能从
   独立连接读到，并经真实 tail 服务（SSE 接缝）作为已提交事实产出。
3. **终态前 flush**：Run 终态写入前，所有已产生正文都已落库；终态行 seq 大于全部增量行，终态之后
   不再有增量行，正文顺序不破、无丢失（拼接等于模型完整正文），每行携带生成代号（TASK-051 遗留③）；
   断流这类失败终态同样排在已产生正文之后（窗口里的块也先落库，不随失败静默丢弃）。
4. **崩溃只丢未确认缓冲**：进程在模型仍在产出时被 SIGKILL 后，已提交正文逐行原样保留、且仍是完整
   正文的前缀（无半行、无乱序、无重复）；仍在内存窗口里的那一块允许丢失，崩溃也不留下终态。
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from muad_agent_core.agent import AgentRunner
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.model import OpenAICompatibleProvider
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.async_tools.continuation_service import (
    ExecutionIdentity,
    ExecutionLeaseLost,
)
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.event_batcher import EventBatchEntry, EventBatcher
from muad_agent_runtime.application.executor import (
    AgentRunnerExecutor,
    ExecutorEvent,
    ExecutorFactory,
    ExecutorRequest,
    RunExecutor,
)
from muad_agent_runtime.application.ports import NullPlatformSettingsClient
from muad_agent_runtime.application.public_events import project_public_stream_event
from muad_agent_runtime.application.run_events import DeltaWindow
from muad_agent_runtime.application.run_service import RunService, RunStart
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, Conversation, RunRecord
from muad_contracts import (
    ChannelContext,
    MessageInput,
    ResolvedAgent,
    ResolveDefinitionResponse,
    ResolvedModel,
    RunRequest,
    RunStatus,
)
from muad_contracts.webchat_events import WebchatDeltaEvent
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.conftest import FakeResolveClient

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTANCE = "durable-deltas-b259"
WAIT_TIMEOUT_SEC = 20.0
POLL_SEC = 0.05
TERMINAL_STREAM_TYPES = ("run.completed", "run.failed")
#: 增量行的 canonical 流类型（写入方 `run_service._persist_event` 直接取执行器事件类型）。
DELTA_STREAM_TYPE = "message.delta"


# --------------------------------------------------------------------------- #
# 有界窗口：边界语义（纯函数证据，与集成场景互补）
# --------------------------------------------------------------------------- #


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_b259_window_flushes_on_either_boundary_and_never_emits_empty() -> None:
    """时间/字节任一边界到界即应 flush；空窗口不产生空事件（不把"没事发生"写成一行）。"""
    clock = FakeClock()
    window = DeltaWindow(flush_interval_sec=0.05, flush_bytes=8, clock=clock)
    assert window.add("abcd") is None
    assert window.due() is False
    clock.advance(0.049)
    assert window.due() is False
    assert window.add("efgh") == "abcdefgh", "字节边界到界即合并返回"
    assert window.pending_bytes == 0
    assert window.due() is False and window.drain() is None, "已 flush 的窗口不再产出"
    assert window.add("ij") is None
    clock.advance(0.05)
    assert window.due() is True, "时间边界在没有新字节时同样到界"
    assert window.drain() == "ij"
    assert window.drain() is None


def test_b259_production_window_flushes_first_delta_immediately() -> None:
    """首段立即 flush：生产窗口的首个 delta 不等待 300ms 时间边界。"""
    window = DeltaWindow(flush_interval_sec=0.3, flush_bytes=8192, flush_first=True)
    assert window.add("首段") == "首段"
    assert window.add("后续") is None
    assert window.drain() == "后续"


def test_b259_window_drain_is_lossless_in_order() -> None:
    """窗口只是缓冲：任意 add/drain 交错都不得改变拼接结果与顺序。"""
    clock = FakeClock()
    window = DeltaWindow(flush_interval_sec=0.05, flush_bytes=4096, clock=clock)
    emitted: list[str] = []
    for piece in ("甲", "乙", "丙"):
        merged = window.add(piece)
        if merged is not None:
            emitted.append(merged)
        clock.advance(0.03)
    tail = window.drain()
    if tail is not None:
        emitted.append(tail)
    assert "".join(emitted) == "甲乙丙"


# --------------------------------------------------------------------------- #
# 隔离数据存储与真实 HTTP 模型探针
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DurableTenant:
    tenant_id: str
    agent_id: uuid.UUID
    user_id: uuid.UUID


@pytest.fixture
async def durable_tenant(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> AsyncIterator[DurableTenant]:
    """隔离 PostgreSQL/Redis 号位 + Runtime 进程环境；跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", "http://127.0.0.1:9")
    await runtime_db.dispose_engine()
    try:
        yield DurableTenant(
            tenant_id=f"deltas-{uuid.uuid4().hex[:12]}",
            agent_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
        )
    finally:
        await runtime_db.dispose_engine()


@dataclass
class ModelProbeScript:
    """真实 SSE 探针脚本：正文块序列 + 块间节拍 + "发完第 N 块后等放行"的闸门。"""

    chunks: tuple[str, ...]
    gap_sec: float = 0.0
    hold_after: int = 0
    #: True = 发完正文块就直接断流：没有 `finish_reason`、没有 `[DONE]`（上游被切断的真实形状）。
    truncate: bool = False
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
    for index, text in enumerate(script.chunks, start=1):
        if index > 1 and script.gap_sec > 0:
            await asyncio.sleep(script.gap_sec)
        script.sent = index
        yield _chunk_frame(text)
        if index == script.hold_after:
            await script.release.wait()
    if script.truncate:
        return  # 断流：正文之后没有任何收尾信号
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
        script.release.set()  # 探针闸门不得拖住收尾
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=10)
        listener.close()


# --------------------------------------------------------------------------- #
# 真实业务服务：RunService + 真实执行器 + 真实 PG 行
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


def _resolve_response(tenant: DurableTenant, base_url: str) -> ResolveDefinitionResponse:
    return ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=tenant.agent_id, key="deltas-agent", revision=1,
            instructions="fixed instructions", runtime_config={},
        ),
        model=ResolvedModel(
            id=uuid.uuid4(), revision=1, model_id="probe", base_url=base_url,
            api_key="probe-credential", params={"temperature": 0.0, "max_tokens": 256},
        ),
        skills=[],
        mcp_servers=[],
    )


def _run_request(tenant: DurableTenant) -> RunRequest:
    return RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.user_id,
        channel=ChannelContext(type="WECOM", bot_id="bot-1"),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text="数一数正文"),
    )


def _real_executor_factory() -> ExecutorFactory:
    """真实执行链：真实 provider/runner；窗口参数**不注入**，由 `SharedSettings` 生效。"""

    async def factory(request: ExecutorRequest) -> RunExecutor:
        provider = OpenAICompatibleProvider(
            base_url=request.model.base_url,
            model=request.model.model_id,
            api_key=request.model.api_key or None,
        )
        runner = AgentRunner(
            provider=provider, registry=ToolRegistry(), hooks=HookPipeline(),
            runtime_events=request.runtime_events,
        )
        return AgentRunnerExecutor(runner=runner, request=request, close=provider.aclose)

    return factory


@asynccontextmanager
async def started_run(
    tenant: DurableTenant, base_url: str, *, release: asyncio.Event | None = None
) -> AsyncIterator[StartedRun]:
    """真实 RunService + 真实执行器；收尾先放行探针闸门，再等执行片段停下。"""
    supervisor = ExecutionSupervisor()
    session_factory = get_session_factory()
    session = session_factory()
    service = RunService(
        session,
        FakeResolveClient(response=_resolve_response(tenant, base_url)),
        INSTANCE,
        NullPlatformSettingsClient(),
        executor_factory=_real_executor_factory(),
        supervisor=supervisor,
    )
    try:
        start = await service.start(_run_request(tenant), tenant.tenant_id)
        yield StartedRun(service=service, supervisor=supervisor, session=session, start=start)
    finally:
        if release is not None:
            release.set()
        await supervisor.close()
        await session.close()


async def _delta_rows(run_id: uuid.UUID) -> list[CanonicalEvent]:
    """从**独立连接**读已提交增量行：只有真正提交过的字节会出现在这里（推送只认它们）。"""
    async with get_session_factory()() as session:
        rows = await session.scalars(
            sa.select(CanonicalEvent)
            .where(
                CanonicalEvent.run_id == run_id,
                CanonicalEvent.stream_type == DELTA_STREAM_TYPE,
                CanonicalEvent.is_deleted.is_(False),
            )
            .order_by(CanonicalEvent.seq)
        )
        return list(rows)


async def _wait_until(
    predicate: Callable[[], Awaitable[bool]], *, what: str, timeout: float = WAIT_TIMEOUT_SEC
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(POLL_SEC)
    pytest.fail(f"等待超时（{timeout}s）：{what}")


async def _wait_delta_rows(run_id: uuid.UUID, *, minimum: int) -> list[CanonicalEvent]:
    async def enough() -> bool:
        return len(await _delta_rows(run_id)) >= minimum

    await _wait_until(enough, what=f"Run {run_id} 至少 {minimum} 行增量已提交")
    return await _delta_rows(run_id)


async def _drain_run(start: RunStart) -> list[ExecutorEvent]:
    return [event async for event in start.events]


def _delta_text(row: CanonicalEvent) -> str:
    payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    return str(payload.get("delta", ""))


def _texts(rows: list[CanonicalEvent]) -> str:
    return "".join(_delta_text(row) for row in rows)


async def _terminal_row(run_id: uuid.UUID) -> CanonicalEvent:
    async with get_session_factory()() as session:
        row = await session.scalar(
            sa.select(CanonicalEvent)
            .where(
                CanonicalEvent.run_id == run_id,
                CanonicalEvent.stream_type.in_(TERMINAL_STREAM_TYPES),
                CanonicalEvent.is_deleted.is_(False),
            )
            .order_by(CanonicalEvent.seq.desc())
            .limit(1)
        )
    assert row is not None, "Run 必须留下终态事件行"
    return row


async def _tail_first_delta(
    service: RunService, tenant: DurableTenant, run_id: uuid.UUID
) -> ExecutorEvent:
    """真实 tail 服务（SSE 接缝）在增量行已提交后应立刻产出它；只认已提交事实。"""
    tail = await service.tail_run_events(run_id, tenant.tenant_id, after_seq=0)
    iterator = tail.events
    try:
        for _ in range(10):
            event = await asyncio.wait_for(anext(iterator), timeout=5.0)
            if event.type == DELTA_STREAM_TYPE:
                return event
    finally:
        await iterator.aclose()
    pytest.fail("tail 在预算内没有产出已提交的增量事实")


async def _rows_after(run_id: uuid.UUID, seq: int) -> list[CanonicalEvent]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            sa.select(CanonicalEvent)
            .where(CanonicalEvent.run_id == run_id, CanonicalEvent.seq > seq)
            .order_by(CanonicalEvent.seq)
        )
        return list(rows)


# --------------------------------------------------------------------------- #
# 场景 1：字节窗口 —— 小增量批量落库，绝不逐 token 一次事务
# --------------------------------------------------------------------------- #


async def test_b259_byte_window_batches_small_deltas_into_bounded_rows(
    durable_tenant: DurableTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = "0123456789"
    chunk_count = 240
    flush_bytes = 1024
    monkeypatch.setenv("STREAM_DELTA_FLUSH_INTERVAL_MS", "500")
    monkeypatch.setenv("STREAM_DELTA_FLUSH_BYTES", str(flush_bytes))
    script = ModelProbeScript(chunks=(chunk,) * chunk_count)

    async with model_probe(script) as base_url, started_run(durable_tenant, base_url) as running:
        await _drain_run(running.start)
        rows = await _delta_rows(running.run_id)

    total_bytes = len(script.full_text.encode("utf-8"))
    sizes = [len(_delta_text(row).encode("utf-8")) for row in rows]
    assert total_bytes == chunk_count * len(chunk) == sum(sizes)
    assert _texts(rows) == script.full_text, "合并必须无损：拼接等于模型完整正文"
    assert 2 <= len(rows) <= (total_bytes + flush_bytes - 1) // flush_bytes + 1, (
        f"{chunk_count} 块只允许按窗口边界落库，实际 {len(rows)} 行"
    )
    assert len(rows) < chunk_count / 10, "小增量不得逐 token 落库"
    assert all(size <= flush_bytes + len(chunk) for size in sizes), "单行增量受字节窗口上界约束"
    assert any(size >= flush_bytes for size in sizes[:-1]), "字节边界确实触发过落库"
    assert all(
        isinstance(row.payload_json, dict) and row.payload_json.get("reply_generation") == 0
        for row in rows
    ), "每行增量都携带生成代号（首次生成 = 0）"


# --------------------------------------------------------------------------- #
# 场景 2：时间窗口 —— 模型停顿期间先提交，再由 tail（SSE 接缝）推送
# --------------------------------------------------------------------------- #


async def test_b259_time_window_commits_before_push_while_model_pauses(
    durable_tenant: DurableTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = "第一段正文。", "第二段正文。"
    monkeypatch.setenv("STREAM_DELTA_FLUSH_INTERVAL_MS", "50")
    monkeypatch.setenv("STREAM_DELTA_FLUSH_BYTES", "65536")
    script = ModelProbeScript(chunks=(first, second), hold_after=1)

    async with model_probe(script) as base_url, started_run(
        durable_tenant, base_url, release=script.release
    ) as running:
        rows = await _wait_delta_rows(running.run_id, minimum=1)
        assert script.sent == 1 and not script.release.is_set(), "探针仍停在第二块之前"
        assert len(rows) == 1 and _delta_text(rows[0]) == first, "时间边界在停顿期间落库"

        pushed = await _tail_first_delta(running.service, durable_tenant, running.run_id)
        assert pushed.type == DELTA_STREAM_TYPE and pushed.seq == rows[0].seq, (
            "推送的是已提交事实（同一 canonical seq），不是内存缓冲"
        )
        assert pushed.data == {"delta": first, "reply_generation": 0}

        script.release.set()
        events = await _drain_run(running.start)
        rows = await _delta_rows(running.run_id)

    assert events[-1].type == "run.completed" and events[-1].data["status"] == "COMPLETED"
    assert _texts(rows) == script.full_text, "停顿前后的正文都要落库且顺序不破"
    assert rows[0].seq < rows[-1].seq, "先提交的第一段在序号上先于其后内容"
    assert len(rows) <= 3, "合并仍受窗口边界约束"


# --------------------------------------------------------------------------- #
# 场景 3：终态前 flush —— 终态行排在全部已确认正文之后
# --------------------------------------------------------------------------- #


async def test_b259_production_window_bounds_commits_per_second(
    durable_tenant: DurableTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[S-04] 20 delta/s × 10s：300ms 窗口下 ≤4 commits/s/Run，远少于逐事件事务。"""
    monkeypatch.setenv("STREAM_DELTA_FLUSH_INTERVAL_MS", "300")
    monkeypatch.setenv("STREAM_DELTA_FLUSH_BYTES", "8192")
    script = ModelProbeScript(chunks=("0123456789",) * 200, gap_sec=0.05)
    engine = runtime_db.get_engine().sync_engine
    commits = 0
    inserts = 0

    def _on_commit(conn) -> None:  # type: ignore[no-untyped-def]
        nonlocal commits
        commits += 1

    def _on_execute(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        nonlocal inserts
        if "INSERT INTO runtime.canonical_event" in statement:
            inserts += 1

    sa.event.listen(engine, "commit", _on_commit)
    sa.event.listen(engine, "before_cursor_execute", _on_execute)
    started = time.monotonic()
    try:
        async with model_probe(script) as base_url, started_run(durable_tenant, base_url) as running:
            await _drain_run(running.start)
            rows = await _delta_rows(running.run_id)
    finally:
        elapsed = time.monotonic() - started
        sa.event.remove(engine, "commit", _on_commit)
        sa.event.remove(engine, "before_cursor_execute", _on_execute)

    assert _texts(rows) == script.full_text
    commits_per_sec = commits / elapsed
    assert commits_per_sec <= 4.5, (
        f"{script.sent} 个 delta / {elapsed:.1f}s 产生 {commits_per_sec:.1f} commits/s（目标 ≤4）"
    )
    assert inserts < 200, f"窗口内应合并批量写入，实际 INSERT {inserts} 次"
    assert inserts <= 3 * script.sent, (
        f"statements/event 必须 ≤3；实际 {inserts} 条 INSERT / {script.sent} 个 delta"
    )


async def test_b259_terminal_flush_orders_all_confirmed_text_before_terminal(
    durable_tenant: DurableTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = "0123456789"
    monkeypatch.setenv("STREAM_DELTA_FLUSH_INTERVAL_MS", "500")
    monkeypatch.setenv("STREAM_DELTA_FLUSH_BYTES", "4096")
    script = ModelProbeScript(chunks=(chunk,) * 120)

    async with model_probe(script) as base_url, started_run(durable_tenant, base_url) as running:
        events = await _drain_run(running.start)
        rows = await _delta_rows(running.run_id)
        terminal = await _terminal_row(running.run_id)
        after_terminal = await _rows_after(running.run_id, int(terminal.seq))
        async with get_session_factory()() as session:
            run = await session.get(RunRecord, running.run_id)

    assert run is not None and run.status == RunStatus.COMPLETED
    assert events[-1].type == "run.completed"
    assert _texts(rows) == script.full_text, "终态前已产生正文必须全部落库"
    assert len(rows) <= 3 and len(rows) < len(script.chunks) / 10, (
        f"120 块在 4096 字节窗口内应合并为极少行，实际 {len(rows)} 行"
    )
    assert terminal.seq > rows[-1].seq, "终态行必须排在全部已确认正文之后"
    assert all(row.stream_type not in TERMINAL_STREAM_TYPES for row in after_terminal), (
        "终态之后不得再出现正文增量或第二个终态"
    )
    projected = [project_public_stream_event(row, {}) for row in rows]
    assert all(isinstance(item, WebchatDeltaEvent) for item in projected)
    deltas = [item for item in projected if isinstance(item, WebchatDeltaEvent)]
    assert "".join(item.data.text for item in deltas) == script.full_text
    assert all(item.data.reply_generation == 0 for item in deltas)
    assert events[-1].data["final_text"] == script.full_text, "终态正文与增量拼接同一事实"


# --------------------------------------------------------------------------- #
# 场景 4：断流（无收尾信号）—— 已产生正文（含窗口内缓冲）先落库，失败终态排在它之后
# --------------------------------------------------------------------------- #


async def test_b259_truncated_stream_flushes_produced_text_before_failed_terminal(
    durable_tenant: DurableTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = "断流前已产生的正文。"
    # 窗口 500ms、块间无间隔：三块都会停在窗口里；断流发生在窗口到界之前。
    monkeypatch.setenv("STREAM_DELTA_FLUSH_INTERVAL_MS", "500")
    monkeypatch.setenv("STREAM_DELTA_FLUSH_BYTES", "65536")
    script = ModelProbeScript(chunks=(chunk,) * 3, truncate=True)

    async with model_probe(script) as base_url, started_run(durable_tenant, base_url) as running:
        events = await _drain_run(running.start)
        rows = await _delta_rows(running.run_id)
        terminal = await _terminal_row(running.run_id)

    assert events[-1].type == "run.failed", "残缺回答不得当成功落成 COMPLETED"
    assert terminal.payload_json is not None
    assert terminal.payload_json.get("error_code") == "MODEL_UNAVAILABLE"
    assert _texts(rows) == script.full_text, "断流前产生的正文必须全部落库（含仍在窗口里的块）"
    assert terminal.seq > rows[-1].seq, "失败终态也必须排在已产生正文之后"


# --------------------------------------------------------------------------- #
# 场景 5：真实进程崩溃（SIGKILL）—— 已确认字节逐行原样保留
# --------------------------------------------------------------------------- #

#: 子进程入口：起一个真实 Run（真实 RunService/执行器/模型探针/落库），随后被父进程 SIGKILL。
#: 崩溃 = 进程被不可捕获地杀死：没有收尾、没有 flush、没有终态；内存缓冲随进程消失。
_CRASH_CHILD_CODE = (
    "import sys;"
    "from tests.agent_runtime.test_capacity_webchat_durable_deltas import run_crash_child;"
    "sys.exit(run_crash_child())"
)


def run_crash_child() -> int:
    """`python -c` 入口：读环境里的隔离库/探针/租户事实，跑一个真实 Run 直到被杀。"""
    tenant = DurableTenant(
        tenant_id=os.environ["B259_TENANT"],
        agent_id=uuid.UUID(os.environ["B259_AGENT"]),
        user_id=uuid.UUID(os.environ["B259_USER"]),
    )
    return asyncio.run(_crash_child_main(tenant, os.environ["B259_BASE_URL"]))


async def _crash_child_main(tenant: DurableTenant, base_url: str) -> int:
    async with started_run(tenant, base_url):
        await asyncio.Event().wait()  # 进程存活到被 SIGKILL
    return 0


def _spawn_crash_child(tenant: DurableTenant, base_url: str) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(REPO_ROOT), str(REPO_ROOT / "tests")]),
        "B259_TENANT": tenant.tenant_id,
        "B259_AGENT": str(tenant.agent_id),
        "B259_USER": str(tenant.user_id),
        "B259_BASE_URL": base_url,
    }
    return subprocess.Popen(
        [sys.executable, "-c", _CRASH_CHILD_CODE],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


async def _run_id_for(tenant_id: str) -> uuid.UUID | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.select(RunRecord.id)
            .where(RunRecord.tenant_id == tenant_id, RunRecord.is_deleted.is_(False))
            .order_by(RunRecord.create_time.desc())
            .limit(1)
        )


async def _wait_run_exists(tenant_id: str, child: subprocess.Popen[bytes]) -> uuid.UUID:
    """等子进程受理 Run；子进程提前退出时把它的 stderr 带进失败信息（不静默超时）。"""
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    while time.monotonic() < deadline:
        run_id = await _run_id_for(tenant_id)
        if run_id is not None:
            return run_id
        if child.poll() is not None:
            detail = child.stderr.read().decode(errors="replace") if child.stderr else ""
            pytest.fail(f"崩溃子进程提前退出（exit={child.returncode}）：{detail[-2000:]}")
        await asyncio.sleep(POLL_SEC)
    pytest.fail("崩溃子进程没有在预算内受理真实 Run")


async def test_b259_confirmed_bytes_survive_process_kill(
    durable_tenant: DurableTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = "崩溃前已确认的字节。"
    # 窗口 500ms、块间隔 400ms：前两块合成一行落库；第三块落在下一个窗口内，被 SIGKILL 打断。
    monkeypatch.setenv("STREAM_DELTA_FLUSH_INTERVAL_MS", "500")
    monkeypatch.setenv("STREAM_DELTA_FLUSH_BYTES", "65536")
    script = ModelProbeScript(chunks=(chunk,) * 3, gap_sec=0.4, hold_after=3)

    async with model_probe(script) as base_url:
        child = _spawn_crash_child(durable_tenant, base_url)
        try:
            run_id = await _wait_run_exists(durable_tenant.tenant_id, child)
            confirmed = await _wait_delta_rows(run_id, minimum=1)
            await _wait_until(
                _probe_sent_reached(script, 3), what="探针发出第三块（仍在 500ms 窗口内）"
            )
            child.kill()
            assert child.wait(timeout=10) == -signal.SIGKILL, "SIGKILL 不可捕获"
            rows = await _delta_rows(run_id)
            run = await _run_row(run_id)
            terminal = await _terminal_row_or_none(run_id)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            if child.stderr is not None:
                child.stderr.close()

    assert [row.seq for row in rows] == [row.seq for row in confirmed], (
        "崩溃后已提交正文逐行原样保留（seq 与内容都不重排）"
    )
    assert [_delta_text(row) for row in rows] == [_delta_text(row) for row in confirmed]
    assert script.full_text.startswith(_texts(rows)), "已确认正文仍是完整正文的前缀"
    assert _texts(rows) < script.full_text, "第三块还在内存缓冲里，随进程崩溃一起消失"
    assert run is not None and run.status == RunStatus.RUNNING, "崩溃不做收尾：没有终态"
    assert terminal is None, "崩溃后不存在终态行（终态不先于最后一条已确认正文）"


def _probe_sent_reached(script: ModelProbeScript, count: int) -> Callable[[], Awaitable[bool]]:
    async def reached() -> bool:
        return script.sent >= count

    return reached


async def _run_row(run_id: uuid.UUID) -> RunRecord | None:
    async with get_session_factory()() as session:
        return await session.get(RunRecord, run_id)


async def _terminal_row_or_none(run_id: uuid.UUID) -> CanonicalEvent | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.select(CanonicalEvent)
            .where(
                CanonicalEvent.run_id == run_id,
                CanonicalEvent.stream_type.in_(TERMINAL_STREAM_TYPES),
            )
            .limit(1)
        )


async def test_b259_event_batch_commits_valid_and_isolates_lease_loss(
    durable_tenant: DurableTenant,
) -> None:
    """同一批里有效事件提交；失效 lease 只失败自己，不回滚同批正常事件（Plan B）。"""
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        conversation = Conversation(
            tenant_id=durable_tenant.tenant_id,
            user_id=durable_tenant.user_id,
            agent_id=durable_tenant.agent_id,
        )
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=durable_tenant.tenant_id,
            conversation_id=conversation.id,
            user_id=durable_tenant.user_id,
            agent_id=durable_tenant.agent_id,
            status=RunStatus.RUNNING,
            input_text="batch",
            trace_id=uuid.uuid4().hex,
            lease_owner=INSTANCE,
            execution_epoch=0,
            lease_until=now + timedelta(seconds=300),
        )
        session.add(run)
        await session.commit()
        run_id = run.id
        conversation_id = conversation.id
        tenant_id = durable_tenant.tenant_id
    batcher = EventBatcher(get_session_factory)
    try:
        valid = asyncio.create_task(
            batcher.submit(
                EventBatchEntry(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    identity=ExecutionIdentity(tenant_id, run_id, INSTANCE, 0),
                    event_type="message.delta",
                    payload={"delta": "a", "reply_generation": 0},
                    stream_type="message.delta",
                )
            )
        )
        expired = asyncio.create_task(
            batcher.submit(
                EventBatchEntry(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    identity=ExecutionIdentity(tenant_id, run_id, "other-owner", 0),
                    event_type="message.delta",
                    payload={"delta": "b", "reply_generation": 0},
                    stream_type="message.delta",
                )
            )
        )
        results = await asyncio.gather(valid, expired, return_exceptions=True)
    finally:
        await batcher.aclose()
    assert isinstance(results[0], int)
    assert isinstance(results[1], ExecutionLeaseLost)
    async with get_session_factory()() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(CanonicalEvent)
            .where(
                CanonicalEvent.run_id == run_id,
                CanonicalEvent.stream_type == "message.delta",
            )
        )
    assert count == 1, "有效事件必须提交，失效 lease 的事件不得写入"
