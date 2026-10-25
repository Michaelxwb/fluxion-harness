"""[E-08] 混沌恢复：Redis 抖动 / Gateway kill / Runtime kill / PG 连接中断（TASK-012）。

真实边界（不 mock 业务服务）：
- 真实四单元独立栈（真实 uvicorn 子进程、真实入口代理、真实 PostgreSQL/Redis/共享卷）；
- Redis 抖动由真实 TCP 故障代理承载：Runtime 子进程**启动时** `REDIS_URL` 就指向代理，
  `DROP` 切断在飞连接并丢弃后续连接（真实网络故障，不是协议替身）；
- Gateway/Runtime 用 SIGKILL 整进程组 + 原地重启（同地址的新进程）注入 Pod kill；
- PG 腿注入**本栈临时库**的服务端连接中断（`pg_terminate_backend`），不重启共享 PG 容器；
- 断言是用户可观察结果（公开 SSE 正文/终态）+ 真实 PG 事实（终态行/通知行/事件行），
  不断言源码字符串。

边界登记（不得冒充）：本机没有 PG 副本，**主备切换/副本提升**无法安全注入 ⇒ 该腿
`env_blocked`（缺失环境：带副本的 PG 目标拓扑；owner=发布负责人），由 staging 的 E-09
（deadline=发布前）覆盖；本文件只覆盖「连接整体失效 → 恢复」这一下半段。
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest

from tests.acceptance.capacity.delivery_harness import (
    PROBE_API_KEY,
    ChatSession,
    SeededDelivery,
    bind_probe_credential,
    build_stack,
    create_datastore_handle,
    db_execute,
    db_rows,
    exchange_chat_session,
    metric_total,
    probe_state,
    seed_delivery_control,
    set_probe_behavior,
    wait_until,
)
from tests.acceptance.capacity.execution_support import EXECUTION_CADENCE_ENV
from tests.acceptance.capacity.probes import FaultProxy, FaultProxyMode
from tests.acceptance.capacity.stack import CapacityStack, ServiceInstance, run_async
from tests.acceptance.capacity.test_capacity import (
    PublicStreamRead,
    apply_public_frames,
    read_public_stream,
)
from tests.acceptance.capacity.test_stream_delivery import (
    create_conversation,
    event_rows,
    run_row,
    submit_message,
)
from tests.capacity.profile import load_profile

pytestmark = pytest.mark.integration

WEBCHAT_ENTRIES_PREFIX = "/api/v1/webchat/entries"
TERMINAL_RUN_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")
WAIT_TIMEOUT_SEC = 120.0
#: 真实增量流：足够长（秒级）以便在运行中注入故障，而不是赛跑。
REDIS_JITTER_DELTAS = tuple(f"抖动{index:02d}-" for index in range(40))
GATEWAY_DELTAS = ("网关", "中断", "执行", "解耦", "恢复", "正文")
GATEWAY_BODY = "".join(GATEWAY_DELTAS)
RUNTIME_DELTAS = ("执行", "实例", "崩溃", "前缀", "重放", "诚实", "终态")
RUNTIME_BODY = "".join(RUNTIME_DELTAS)
PG_DELTAS = ("数据", "库连", "接中", "断后", "恢复", "继续")
PG_BODY = "".join(PG_DELTAS)


@dataclass(frozen=True, slots=True)
class ChaosHarness:
    stack: CapacityStack
    seeded: SeededDelivery
    session: ChatSession
    redis_proxy: FaultProxy


def _proxied(url: str, proxy: FaultProxy) -> str:
    """把 DSN 指向真实故障代理（host/port 换成代理地址，其余保持原样）。"""
    parsed = urlsplit(url)
    host, port = proxy.address
    credentials = ""
    if parsed.username is not None:
        credentials = f"{parsed.username}:{parsed.password or ''}@"
    return urlunsplit(
        (parsed.scheme, f"{credentials}{host}:{port}", parsed.path, parsed.query, parsed.fragment)
    )


def _disable_memory_writes(database_url: str, agent_id: uuid.UUID) -> None:
    """关闭后台记忆提炼：混沌断言只关心交互执行链，不让提炼 job 混进模型调用计数。"""

    async def update() -> None:
        from muad_console_platform.infrastructure.models.control import AgentDefinition
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                agent = await session.get(AgentDefinition, agent_id)
                assert agent is not None, "Agent 行必须已存在"
                config = dict(agent.runtime_config or {})
                config["memory_write"] = False
                agent.runtime_config = config
                await session.commit()
        finally:
            await engine.dispose()

    run_async(update)


@pytest.fixture(scope="module")
def chaos_harness(
    tmp_path_factory: pytest.TempPathFactory, capacity_profile_path: Path
) -> Iterator[ChaosHarness]:
    """本栈独立库/Redis 号位 + Runtime 的 Redis 经真实故障代理 + 加速执行节拍。"""
    datastore = create_datastore_handle()
    redis_url = urlsplit(datastore.redis_url)
    redis_proxy = FaultProxy(str(redis_url.hostname), int(redis_url.port or 6379))
    redis_proxy.start()
    service_env = {
        **EXECUTION_CADENCE_ENV,
        "agent-runtime": {
            **EXECUTION_CADENCE_ENV["agent-runtime"],
            "REDIS_URL": _proxied(datastore.redis_url, redis_proxy),
        },
    }
    stack = build_stack(
        root=tmp_path_factory.mktemp("capacity-chaos"),
        profile=load_profile(capacity_profile_path),
        datastore=datastore,
        service_env=service_env,
    )
    seeded = seed_delivery_control(datastore.database_url, stack.tenant_id)
    _disable_memory_writes(datastore.database_url, seeded.agent_id)
    stack.start()
    bind_probe_credential(
        datastore.database_url, seeded.credential_id, f"{stack.model_probe.url}/v1"
    )
    session = exchange_chat_session(stack.proxy.url, seeded.token, seeded.user_id)
    try:
        yield ChaosHarness(
            stack=stack, seeded=seeded, session=session, redis_proxy=redis_proxy
        )
    finally:
        stack.teardown()
        redis_proxy.stop()


def _start_read(
    harness: ChaosHarness, run_id: str, *, min_frames: int | None = 1
) -> tuple[PublicStreamRead, threading.Thread]:
    """后台真实公开 SSE 订阅：边到边写进 sink。

    `min_frames` 给定时读到该帧数即返回（调用方随后按游标重连）；`None` 表示一直读到
    终态或连接断开（用于观察 Pod kill 造成的在飞断流）。
    """
    read = PublicStreamRead()

    def until(item: PublicStreamRead) -> bool:
        if item.terminal is not None:
            return True
        return min_frames is not None and len(item.frames) >= min_frames

    def reader() -> None:
        read_public_stream(
            harness.stack.proxy.url,
            harness.session,
            run_id,
            after_seq=0,
            until=until,
            sink=read,
            timeout_sec=WAIT_TIMEOUT_SEC,
        )

    thread = threading.Thread(target=reader, name=f"chaos-read-{run_id[:8]}", daemon=True)
    thread.start()
    return read, thread


def _wait_for_frames(read: PublicStreamRead, *, min_frames: int = 1) -> None:
    wait_until(
        lambda: len(read.frames) >= min_frames,
        what=f"公开 SSE 未在超时内收到 {min_frames} 帧",
        timeout=60.0,
    )


def _wait_terminal(database_url: str, run_id: str, *, timeout: float = 90.0) -> dict[str, Any]:
    wait_until(
        lambda: run_row(database_url, run_id)["status"] in TERMINAL_RUN_STATUSES,
        what=f"Run {run_id} 未在超时内到达终态",
        timeout=timeout,
    )
    return dict(run_row(database_url, run_id))


def _assert_single_terminal_and_delivery(database_url: str, run_id: str) -> None:
    """真实 PG 事实：终态事件行与用户通知行各恰一条（无重复终态/投递）。"""
    terminals = event_rows(database_url, run_id, "run.completed") + event_rows(
        database_url, run_id, "run.failed"
    )
    assert len(terminals) == 1, f"终态事件必须恰一条，实际 {len(terminals)}"
    rows = db_rows(
        database_url,
        "SELECT count(*) AS total, count(DISTINCT delivery_key) AS keys"
        " FROM runtime.channel_notification WHERE run_id = $1",
        uuid.UUID(run_id),
    )
    assert int(rows[0]["total"]) == 1, f"终态通知必须恰一行，实际 {rows[0]['total']}"
    assert int(rows[0]["keys"]) == 1, "终态投递键不得重复"


def _public_terminal_status(read: PublicStreamRead) -> str | None:
    """公开投影的终态：成功是 `run.terminal`；失败按契约投影成 `run.error`（无 terminal 帧）。"""
    if read.terminal is not None:
        return read.terminal
    if any(frame.event == "run.error" for frame in read.frames):
        return "FAILED"
    return None


def _assert_cursor_continuity(
    first: PublicStreamRead, second: PublicStreamRead, run_id: str
) -> None:
    """按最后已应用 seq 重连：严格向前、无重复、只带本 Run 身份、终态恰一帧。"""
    cursor = first.last_seq
    assert cursor >= 1, "首个订阅必须已应用真实持久事件"
    assert second.frames, "重连必须真的重放持久事件"
    seqs = [frame.seq for frame in second.frames]
    assert all(seq > cursor for seq in seqs), "重连不得回退重投已应用事件"
    assert seqs == sorted(set(seqs)), "seq 必须严格递增且不重复"
    assert all(frame.run_id in (None, run_id) for frame in second.frames)
    terminal_frames = [
        frame
        for frame in (*first.frames, *second.frames)
        if frame.event in ("run.terminal", "run.error")
    ]
    assert len(terminal_frames) == 1, "两次订阅拼起来只允许一个终态帧"


def _downtime_submission(
    harness: ChaosHarness, conversation_id: str
) -> httpx.Response:
    """停机窗口内经入口代理提交一条真实消息（按事实失败，不伪造成功）。"""
    with httpx.Client(
        base_url=harness.stack.proxy.url,
        timeout=10.0,
        trust_env=False,
        cookies={"muad_chat_session": harness.session.cookie},
        headers={"X-Chat-Context": harness.session.context},
    ) as client:
        return client.post(
            f"{WEBCHAT_ENTRIES_PREFIX}/{harness.seeded.entry_id}/messages",
            json={
                "conversation_id": conversation_id,
                "message_id": f"chaos-down-{uuid.uuid4().hex[:10]}",
                "text": "停机窗口内提交",
                "upload_ids": [],
            },
            headers={"Idempotency-Key": f"chaos-down-{uuid.uuid4().hex}"},
        )


def _set_probe(probe_url: str, *, latency_ms: int, chunk_interval_ms: int, deltas: Any) -> None:
    set_probe_behavior(
        probe_url,
        {
            "latency_ms": latency_ms,
            "chunk_interval_ms": chunk_interval_ms,
            "deltas": list(deltas),
        },
        api_key=PROBE_API_KEY,
    )


def _assert_hint_recovery(
    harness: ChaosHarness, runtime: ServiceInstance, proxy: FaultProxy, ok_before: float
) -> None:
    """恢复直通后新 Run 正常完成，提示路径重新可见（降级不是永久状态）。"""
    stack, seeded, session = harness.stack, harness.seeded, harness.session
    proxy.set_fault(FaultProxyMode.PASS_THROUGH)
    conversation_id = create_conversation(stack.proxy.url, session, seeded.entry_id)
    run_id = submit_message(stack.proxy.url, session, seeded.entry_id, conversation_id, "恢复直通")
    recovered = read_public_stream(
        stack.proxy.url, session, run_id, timeout_sec=WAIT_TIMEOUT_SEC
    )
    assert recovered.terminal == "COMPLETED", "恢复直通后新 Run 必须正常完成"
    wait_until(
        lambda: metric_total(runtime.url, "event_stream_hint_total", {"status": "ok"}) > ok_before,
        what="恢复后提示路径未重新可见（ok 计数未增长）",
        timeout=30.0,
    )


def _submit_and_drop_redis(
    harness: ChaosHarness, conversation_id: str, proxy: FaultProxy
) -> tuple[str, PublicStreamRead]:
    """提交真实 Run，在它仍在执行时切断 Redis（真实 TCP DROP），返回首个订阅的观测。"""
    stack, seeded, session = harness.stack, harness.seeded, harness.session
    run_id = submit_message(
        stack.proxy.url, session, seeded.entry_id, conversation_id, "Redis 抖动"
    )
    first, thread = _start_read(harness, run_id, min_frames=3)
    _wait_for_frames(first, min_frames=3)
    assert run_row(stack.database_url, run_id)["status"] == "RUNNING", (
        "Redis 抖动必须作用在执行中的 Run 上"
    )
    proxy.set_fault(FaultProxyMode.DROP)
    assert proxy.mode is FaultProxyMode.DROP
    thread.join(timeout=30.0)
    return run_id, first


def test_e08_redis_jitter_degrades_to_pg_and_recovers(chaos_harness: ChaosHarness) -> None:
    """[E-08] Redis 抖动：在飞 Run 不丢，SSE 退化为 PG 有界补查后可恢复，降级可见。

    断言位置：① 故障注入时 Run 确实在 RUNNING（不是赛跑）；② 切断真实 Redis 连接后
    公开 SSE 仍读到 COMPLETED 且正文逐字一致；③ 按游标重连严格向前、终态恰一帧；
    ④ `/metrics` 的 `event_stream_hint_total{status="degraded"}` 真实增长；⑤ 恢复直通后
    新 Run 正常完成且 `status="ok"` 重新可见。
    """
    stack, seeded, session, proxy = (
        chaos_harness.stack,
        chaos_harness.seeded,
        chaos_harness.session,
        chaos_harness.redis_proxy,
    )
    runtime = stack.instance("agent-runtime", 0)
    degraded_before = metric_total(runtime.url, "event_stream_hint_total", {"status": "degraded"})
    _set_probe(
        stack.model_probe.url,
        latency_ms=100,
        chunk_interval_ms=200,
        deltas=REDIS_JITTER_DELTAS,
    )
    conversation_id = create_conversation(stack.proxy.url, session, seeded.entry_id)
    run_id, first = _submit_and_drop_redis(chaos_harness, conversation_id, proxy)

    second = read_public_stream(
        stack.proxy.url, session, run_id, after_seq=first.last_seq, timeout_sec=WAIT_TIMEOUT_SEC
    )
    assert second.terminal == "COMPLETED", second.error or "Redis 抖动后未读到终态"
    _assert_cursor_continuity(first, second, run_id)
    assert apply_public_frames((*first.frames, *second.frames)) == "".join(REDIS_JITTER_DELTAS)
    terminal = _wait_terminal(stack.database_url, run_id)
    assert terminal["status"] == "COMPLETED"
    _assert_single_terminal_and_delivery(stack.database_url, run_id)
    assert metric_total(runtime.url, "event_stream_hint_total", {"status": "degraded"}) > (
        degraded_before
    ), "Redis 降级必须有真实指标（不静默吞掉）"
    assert proxy.dropped_connections >= 1, "代理确实切断过连接（真实故障）"

    _assert_hint_recovery(
        chaos_harness,
        runtime,
        proxy,
        metric_total(runtime.url, "event_stream_hint_total", {"status": "ok"}),
    )


def _kill_gateway_and_assert_outage(
    harness: ChaosHarness, conversation_id: str, first: PublicStreamRead, thread: threading.Thread
) -> ServiceInstance:
    """SIGKILL Gateway：在飞 SSE 真实断开、停机请求 502；随后同地址重启（保证后续用例可用）。"""
    stack = harness.stack
    gateway = stack.instance("im-gateway", 0)
    gateway_pid, gateway_port = gateway.pid, gateway.port
    failures_before = int(stack.proxy.stats()["upstream_failures"]["im-gateway"])
    try:
        stack.kill_instance(gateway)
        assert gateway.alive is False
        thread.join(timeout=30.0)
        assert first.disconnected is True, "Gateway 被杀必须让在飞公开 SSE 真实断开"
        assert first.terminal is None, "断开时不得出现终态（Run 仍在执行）"
        down = _downtime_submission(harness, conversation_id)
        assert down.status_code == 502, f"停机窗口必须按事实失败：{down.status_code} {down.text}"
        assert down.json()["unit"] == "im-gateway"
        # 在飞 SSE 与停机窗口请求都经入口代理失败：至少新增一次真实上游失败记录。
        assert int(stack.proxy.stats()["upstream_failures"]["im-gateway"]) > failures_before
    finally:
        revived = stack.restart_instance("im-gateway", index=0)
    assert revived.pid != gateway_pid and revived.port == gateway_port
    return revived


def test_e08_gateway_kill_keeps_execution_and_recovers_public_sse(
    chaos_harness: ChaosHarness,
) -> None:
    """[E-08] Gateway Pod kill：执行与传输解耦，已受理 Run 不丢，公开 SSE 按游标续上。

    断言位置：① 停机窗口内经入口代理的请求 502 且代理上游失败计数增长（真实不可用）；
    ② 在飞公开 SSE 因 Gateway 被杀真实断开且断开时无终态；③ 原地重启后按游标重连读到
    COMPLETED，正文逐字一致、seq 严格向前；④ 会话仍恰一个 Run、模型只调用一次、
    PG 终态事件与通知各一条（无重复终态/投递）。
    """
    stack, seeded, session = chaos_harness.stack, chaos_harness.seeded, chaos_harness.session
    probe = stack.model_probe
    _set_probe(probe.url, latency_ms=1000, chunk_interval_ms=400, deltas=GATEWAY_DELTAS)
    calls_before = int(probe_state(probe.url)["call_count"])
    conversation_id = create_conversation(stack.proxy.url, session, seeded.entry_id)
    run_id = submit_message(
        stack.proxy.url, session, seeded.entry_id, conversation_id, "Gateway 崩溃"
    )
    first, thread = _start_read(chaos_harness, run_id, min_frames=None)
    _wait_for_frames(first)
    assert run_row(stack.database_url, run_id)["status"] == "RUNNING"

    _kill_gateway_and_assert_outage(chaos_harness, conversation_id, first, thread)
    second = read_public_stream(
        stack.proxy.url, session, run_id, after_seq=first.last_seq, timeout_sec=WAIT_TIMEOUT_SEC
    )
    assert second.terminal == "COMPLETED", second.error or "重连未读到终态"
    _assert_cursor_continuity(first, second, run_id)
    assert apply_public_frames((*first.frames, *second.frames)) == GATEWAY_BODY
    terminal = _wait_terminal(stack.database_url, run_id)
    assert terminal["status"] == "COMPLETED", "Gateway 停机不得改变执行结局"
    runs = db_rows(
        stack.database_url,
        "SELECT count(*) AS total FROM runtime.run_record WHERE conversation_id = $1",
        uuid.UUID(conversation_id),
    )
    assert int(runs[0]["total"]) == 1, "恢复不得重建 Run"
    assert int(probe_state(probe.url)["call_count"]) == calls_before + 1, "不得重复执行模型"
    _assert_single_terminal_and_delivery(stack.database_url, run_id)


def _kill_runtime_and_restart(
    harness: ChaosHarness, first: PublicStreamRead, thread: threading.Thread
) -> ServiceInstance:
    """SIGKILL 执行中的 Runtime：在飞 SSE 真实断开、断开时无终态；同地址重启。"""
    stack = harness.stack
    runtime = stack.instance("agent-runtime", 0)
    runtime_pid, runtime_port = runtime.pid, runtime.port
    try:
        stack.kill_instance(runtime)
        assert runtime.alive is False
        thread.join(timeout=30.0)
        assert first.disconnected is True, "执行实例被杀必须让公开 SSE 真实断开"
        assert first.terminal is None, "断开时不得出现终态（终态由 Reaper 落）"
    finally:
        revived = stack.restart_instance("agent-runtime", index=0)
    assert revived.pid != runtime_pid and revived.port == runtime_port
    return revived


def test_e08_runtime_kill_reaps_run_and_resumes_sse_without_duplicates(
    chaos_harness: ChaosHarness,
) -> None:
    """[E-08] Runtime Pod kill：已受理 Run 不丢（诚实终态），已提交前缀可 replay，无重复。

    断言位置：① 执行中的 Runtime 被 SIGKILL 后，在飞公开 SSE 真实断开且无终态；
    ② 原地重启后 Reaper 落 FAILED/RUN_ABANDONED（不静默、不凭空重跑）；③ 按游标重连
    读到该终态，seq 严格向前，正文是已提交前缀；④ 终态事件/通知各一条、模型只调用一次
    （无双执行、无重复投递）。
    """
    stack, seeded, session = chaos_harness.stack, chaos_harness.seeded, chaos_harness.session
    probe = stack.model_probe
    _set_probe(probe.url, latency_ms=1000, chunk_interval_ms=400, deltas=RUNTIME_DELTAS)
    calls_before = int(probe_state(probe.url)["call_count"])
    conversation_id = create_conversation(stack.proxy.url, session, seeded.entry_id)
    run_id = submit_message(
        stack.proxy.url, session, seeded.entry_id, conversation_id, "Runtime 崩溃"
    )
    first, thread = _start_read(chaos_harness, run_id, min_frames=None)
    wait_until(
        lambda: bool(apply_public_frames(first.frames)),
        what="公开 SSE 未在超时内收到任何正文（kill 必须发生在已提交前缀之后）",
        timeout=60.0,
    )
    assert run_row(stack.database_url, run_id)["status"] == "RUNNING"

    _kill_runtime_and_restart(chaos_harness, first, thread)
    terminal = _wait_terminal(stack.database_url, run_id, timeout=120.0)
    assert terminal["status"] == "FAILED" and terminal["error_code"] == "RUN_ABANDONED", (
        f"崩溃回收必须落诚实终态：{terminal}"
    )
    second = read_public_stream(
        stack.proxy.url, session, run_id, after_seq=first.last_seq, timeout_sec=WAIT_TIMEOUT_SEC
    )
    assert _public_terminal_status(second) == "FAILED", (
        second.error or "重连未读到崩溃终态（run.error）"
    )
    _assert_cursor_continuity(first, second, run_id)
    replayed = apply_public_frames((*first.frames, *second.frames))
    assert replayed, "已提交前缀必须可 replay（正文不得为空）"
    assert RUNTIME_BODY.startswith(replayed), f"replay 正文必须是真实前缀：{replayed!r}"
    assert int(probe_state(probe.url)["call_count"]) == calls_before + 1, (
        "崩溃回收不得重跑模型（无双执行）"
    )
    _assert_single_terminal_and_delivery(stack.database_url, run_id)


def test_e08_postgres_connection_outage_keeps_admitted_run(
    chaos_harness: ChaosHarness,
) -> None:
    """[E-08] PG 连接整体失效：已受理 Run 不丢、SSE 可恢复、恢复后新 Run 正常。

    本机没有 PG 副本且共享容器不可重启 ⇒ 这里注入**本栈临时库**的服务端连接中断
    （真实 `pg_terminate_backend`，作用域仅本库）；主备切换/副本提升腿另行登记 env_blocked。
    断言位置：① 中断发生在 Run 执行中；② Run 仍落恰一个终态（CAS 权威，不丢）；③ 按游标
    重连读到该终态，正文是已提交前缀；④ 终态事件/通知各一条；⑤ 故障后新对话经真实入口
    受理并 COMPLETED（依赖恢复的事实，不靠进程内状态）。
    """
    stack, seeded, session = chaos_harness.stack, chaos_harness.seeded, chaos_harness.session
    _set_probe(stack.model_probe.url, latency_ms=2000, chunk_interval_ms=400, deltas=PG_DELTAS)
    conversation_id = create_conversation(stack.proxy.url, session, seeded.entry_id)
    run_id = submit_message(
        stack.proxy.url, session, seeded.entry_id, conversation_id, "PG 连接中断"
    )
    first, thread = _start_read(chaos_harness, run_id)
    _wait_for_frames(first)
    assert run_row(stack.database_url, run_id)["status"] == "RUNNING"
    db_execute(
        stack.database_url,
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
        " WHERE datname = current_database() AND pid <> pg_backend_pid()",
    )
    thread.join(timeout=30.0)
    terminal = _wait_terminal(stack.database_url, run_id, timeout=120.0)
    assert terminal["status"] in TERMINAL_RUN_STATUSES
    second = read_public_stream(
        stack.proxy.url, session, run_id, after_seq=first.last_seq, timeout_sec=WAIT_TIMEOUT_SEC
    )
    assert _public_terminal_status(second) == terminal["status"], (
        second.error or "重连未读到终态"
    )
    _assert_cursor_continuity(first, second, run_id)
    replayed = apply_public_frames((*first.frames, *second.frames))
    assert PG_BODY.startswith(replayed), f"replay 正文必须是真实前缀：{replayed!r}"
    _assert_single_terminal_and_delivery(stack.database_url, run_id)

    recovered_conversation = create_conversation(stack.proxy.url, session, seeded.entry_id)
    recovered_run = submit_message(
        stack.proxy.url, session, seeded.entry_id, recovered_conversation, "PG 恢复后提问"
    )
    recovered = read_public_stream(
        stack.proxy.url, session, recovered_run, timeout_sec=WAIT_TIMEOUT_SEC
    )
    assert recovered.terminal == "COMPLETED", "故障后新 Run 必须经真实入口正常完成"


def test_e08_postgres_failover_replica_promotion_is_env_blocked() -> None:
    """[E-08] PG 主备切换/副本提升：本机无副本目标拓扑 ⇒ 显式 env_blocked，不伪造通过。

    缺失环境：带只读副本与提升能力的目标 PG 拓扑（本机 PG 是共用单主容器，重启会打断
    其他应用，禁止注入）。owner=发布负责人；由 staging 的 E-09（deadline=发布前）覆盖。
    """
    pytest.skip(
        "env_blocked: PG failover（主备切换/副本提升）需带副本的目标拓扑；"
        "本机共享单主容器不可安全注入。owner=发布负责人；由 staging E-09 覆盖。"
    )
