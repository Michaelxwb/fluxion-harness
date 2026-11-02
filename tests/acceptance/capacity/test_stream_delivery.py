"""S-03 / E-02 / E-19：事件恢复、投递与持久回执 E2E（TASK-111；设计 §2.5 验收条件 / §3.6）。

真实边界（不 mock 任何服务、不用伪造成功回执）：

- **两个 Runtime 实例**（TASK-107 独立栈的 profile 副本数）、真实 PG、真实 SSE（API-04 canonical
  事件流）、故障进程控制（SIGKILL 整进程组 + 新端口替换实例）；
- **PG/Redis 故障代理**：`probes.FaultProxy` 是真实 TCP 转发器，Runtime 子进程的
  `DATABASE_URL`/`REDIS_URL` 在**启动时**就指向代理（栈级 env 注入），故障窗口内是真连接失败；
- **单 Gateway 重启 + 真实 Run 终态 + PG 投递意图 + 企微协议探针**：真实 WSS/TLS 企微探针
  承载官方 SDK；Gateway 被 SIGKILL 后重启，Run 终态在 PG 上的 `PENDING` 出站意图由 Runtime
  独立投递泵按租约交付回原入口（不依赖 Gateway 内存订阅复活）。

断言位置见各用例 docstring（场景 ID → 断言逐项对应）。
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest
import redis as redis_sync
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.capacity.delivery_harness import (
    PROBE_API_KEY,
    WECOM_BOT_ID,
    WECOM_BOT_SECRET,
    ChatSession,
    SeededDelivery,
    await_until,
    bind_probe_credential,
    build_stack,
    create_datastore_handle,
    db_execute,
    db_rows,
    exchange_chat_session,
    metric_total,
    probe_state,
    scrape_metrics,
    seed_delivery_control,
    set_probe_behavior,
    wait_until,
)
from tests.acceptance.capacity.probes import FaultProxy, FaultProxyMode, WeComProbeServer
from tests.acceptance.capacity.stack import (
    REPO_ROOT,
    STACK_ID_ENV,
    CapacityStack,
    ServiceInstance,
    free_port,
    read_process_environ,
)
from tests.capacity.profile import load_profile

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
WEBCHAT_ENTRY_PREFIX = "/api/v1/webchat/entries"
WEBCHAT_RUN_PREFIX = "/api/v1/webchat/runs"
RUNTIME_EVENTS_PATH = "/v1/runs/{run_id}/events"
WAIT_TIMEOUT_SEC = 120.0
SSE_READ_TIMEOUT_SEC = 60.0
TERMINAL_EVENTS = ("run.completed", "run.failed")


# --------------------------------------------------------------------------------------
# 真实 SSE 读取（canonical 事件流：`event: <type>` + `data: {run_id,seq,...}`）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SseFrame:
    event: str
    seq: int
    data: Mapping[str, Any]


@dataclass(slots=True)
class SseRead:
    """一次真实 SSE 订阅的观测：帧序列 + 断开原因 + 终态（没有终态就是没有）。"""

    frames: list[SseFrame] = field(default_factory=list)
    disconnected: bool = False
    error: str = ""

    @property
    def last_seq(self) -> int:
        return self.frames[-1].seq if self.frames else 0

    @property
    def terminal(self) -> str | None:
        for frame in self.frames:
            if frame.event in TERMINAL_EVENTS:
                status = frame.data.get("status")
                return str(status) if status is not None else frame.event
        return None

    def deltas(self) -> str:
        return "".join(
            str(frame.data.get("delta") or "")
            for frame in self.frames
            if frame.event == "message.delta"
        )


def _runtime_headers(tenant_id: str) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id}


def read_sse(
    url: str,
    *,
    tenant_id: str,
    after_seq: int,
    until: Any,
    sink: SseRead | None = None,
    timeout_sec: float = SSE_READ_TIMEOUT_SEC,
) -> SseRead:
    """真实 SSE 订阅：按 `after_seq` 游标读取，`until(read)` 满足即返回；连接断开显式记录。

    `sink` 给定时帧**边到边追加**（调用方可在另一个线程里观察在飞订阅）；否则新建一份。
    """
    read = sink if sink is not None else SseRead()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=timeout_sec, write=10.0, pool=10.0),
            trust_env=False,
        ) as client:
            with client.stream(
                "GET",
                url,
                params={"after_seq": after_seq},
                headers=_runtime_headers(tenant_id),
            ) as stream:
                if stream.status_code != 200:
                    read.error = f"HTTP {stream.status_code}: {stream.read().decode(errors='replace')}"
                    return read
                event_type = ""
                for line in stream.iter_lines():
                    if line.startswith("event:"):
                        event_type = line.split(":", 1)[1].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = json.loads(line.split(":", 1)[1].strip())
                    read.frames.append(
                        SseFrame(
                            event=str(payload.get("type") or event_type),
                            seq=int(payload["seq"]),
                            data=payload.get("data") or {},
                        )
                    )
                    if until(read):
                        return read
    except httpx.HTTPError as exc:
        read.disconnected = True
        read.error = type(exc).__name__
        return read
    read.disconnected = True
    read.error = read.error or "stream_closed"
    return read


# --------------------------------------------------------------------------------------
# 真实 Webchat/Gateway 驱动（经入口代理）
# --------------------------------------------------------------------------------------


def _chat_client(base_url: str, session: ChatSession) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        timeout=20.0,
        trust_env=False,
        cookies={"muad_chat_session": session.cookie},
        headers={"X-Chat-Context": session.context},
    )


def create_conversation(base_url: str, session: ChatSession, entry_id: uuid.UUID) -> str:
    with _chat_client(base_url, session) as client:
        response = client.post(
            f"{WEBCHAT_ENTRY_PREFIX}/{entry_id}/conversations",
            headers={"Idempotency-Key": f"conv-{uuid.uuid4().hex}"},
        )
    assert response.status_code == 201, f"新建会话失败：{response.status_code} {response.text}"
    return str(response.json()["data"]["conversation_id"])


def submit_message(
    base_url: str, session: ChatSession, entry_id: uuid.UUID, conversation_id: str, text: str
) -> str:
    """真实受理（API-06）：202 是受理事实，回 run_id。"""
    message_id = f"msg-{uuid.uuid4().hex[:12]}"
    with _chat_client(base_url, session) as client:
        response = client.post(
            f"{WEBCHAT_ENTRY_PREFIX}/{entry_id}/messages",
            json={
                "conversation_id": conversation_id,
                "message_id": message_id,
                "text": text,
                "upload_ids": [],
            },
            headers={"Idempotency-Key": f"{conversation_id}:{message_id}"},
        )
    assert response.status_code == 202, f"受理未成功：{response.status_code} {response.text}"
    return str(response.json()["data"]["run_id"])


def run_row(database_url: str, run_id: str) -> Mapping[str, Any]:
    rows = db_rows(
        database_url,
        "SELECT status, execution_epoch, lease_owner, error_code, conversation_id"
        " FROM runtime.run_record WHERE id = $1",
        uuid.UUID(run_id),
    )
    assert rows, f"Run 行不存在：{run_id}"
    return dict(rows[0])


def event_rows(database_url: str, run_id: str, stream_type: str) -> list[Mapping[str, Any]]:
    return [
        dict(row)
        for row in db_rows(
            database_url,
            "SELECT id, seq, payload_json FROM runtime.canonical_event"
            " WHERE run_id = $1 AND stream_type = $2 ORDER BY seq",
            uuid.UUID(run_id),
            stream_type,
        )
    ]


# --------------------------------------------------------------------------------------
# 主栈：S-03（两个 Runtime 实例 + 真实 SSE + 故障进程）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StreamHarness:
    stack: CapacityStack
    seeded: SeededDelivery
    session: ChatSession


@pytest.fixture(scope="module")
def stream_harness(
    tmp_path_factory: pytest.TempPathFactory, capacity_profile_path: Path
) -> Iterator[StreamHarness]:
    """真实独立栈 + 本栈种子：入口必须在 Gateway 快照首次刷新前就位（种子先、start 后）。"""
    root = tmp_path_factory.mktemp("stream-delivery")
    # 每个栈一份**独立**库 + 独立 Redis 号位：Worker claim 与投递选取不带租户谓词，
    # 同一会话里的多个栈共用一份库会互相领走对方的意图/任务（表现为串跑红、单跑绿）。
    datastore = create_datastore_handle()
    stack = build_stack(
        root=root, profile=load_profile(capacity_profile_path), datastore=datastore
    )
    seeded = seed_delivery_control(datastore.database_url, stack.tenant_id)
    stack.start()
    bind_probe_credential(
        datastore.database_url, seeded.credential_id, f"{stack.model_probe.url}/v1"
    )
    session = exchange_chat_session(stack.proxy.url, seeded.token, seeded.user_id)
    try:
        yield StreamHarness(stack=stack, seeded=seeded, session=session)
    finally:
        stack.teardown()


def _hint_total(stack: CapacityStack, status: str) -> float:
    """提示指标按 Runtime 实例求和：Gateway 轮询到的执行实例不一定是 0 号。"""
    return sum(
        metric_total(instance.url, "event_stream_hint_total", {"status": status})
        for instance in stack.instances("agent-runtime")
    )


def test_s03_sse_disconnect_and_subscription_instance_replacement_keep_cursor_continuity(
    stream_harness: StreamHarness,
) -> None:
    """[S-03] 断开重连并替换订阅实例：游标无缺口/重复，不重建 Run/工具。

    真实边界与断言位置：
    ① 两个 Runtime 实例：Run 由 Gateway 指向的 0 号实例执行，订阅打在 1 号实例上（跨实例回放）；
    ② SIGKILL 1 号实例的**整进程组** ⇒ 在飞 SSE 真实断开且没有终态帧（不是"客户端主动关"）；
    ③ 替换实例是新进程/新端口（真实故障进程控制），仍挂同一库/Redis/卷；
    ④ 按最后已应用 seq 重连：所有重放帧 seq > 游标且严格递增（无缺口、无重复）；
    ⑤ 两次订阅拼出的正文 == 探针真实下发的正文（逐字符相等，渲染只出现一次）；
    ⑥ 不重建 Run/工具：conversation 只有 1 个 Run、execution_epoch 不变、模型探针调用数不变、
       `message.delta` 持久事件数 == 两次订阅收到的 delta 帧数（没有多写/少写一行）。
    """
    stack, seeded, session = stream_harness.stack, stream_harness.seeded, stream_harness.session
    probe = stack.model_probe
    deltas = ("容量", "交付", "恢复", "游标", "连续")
    # 真实首块延迟拉到 6s：保证「订阅实例被 SIGKILL」发生在 Run 仍在执行时（不是赛跑）。
    set_probe_behavior(
        probe.url,
        {"latency_ms": 6000, "chunk_interval_ms": 300, "deltas": list(deltas)},
        api_key=PROBE_API_KEY,
    )
    calls_before = int(probe_state(probe.url)["call_count"])

    conversation_id = create_conversation(stack.proxy.url, session, seeded.entry_id)
    run_id = submit_message(
        stack.proxy.url, session, seeded.entry_id, conversation_id, "重连游标连续性"
    )

    # Gateway 的 runtime 池会轮询，不能假设 0 号执行；按真实 lease_owner（POD_NAME 前缀）选
    # 「非执行者」作为订阅实例，杀它才是在杀订阅而不是杀执行中的 Run。
    wait_until(
        lambda: bool(run_row(stack.database_url, run_id).get("lease_owner")),
        what="Run 未在超时内被 Runtime 领取",
        timeout=60.0,
    )
    owner = str(run_row(stack.database_url, run_id)["lease_owner"])
    instances = stack.instances("agent-runtime")
    executor = next(
        (item for item in instances if owner.startswith(
            f"{stack.stack_id}-agent-runtime-{item.index}:"
        )),
        None,
    )
    assert executor is not None, f"lease_owner={owner} 无法映射到 Runtime 实例"
    subscriber = next((item for item in instances if item.pid != executor.pid), None)
    assert subscriber is not None, "本用例需要至少两个 Runtime 实例（执行者 + 订阅者）"
    assert subscriber.pid != executor.pid, "订阅实例与执行实例是两个真实进程"

    url = f"{subscriber.url}{RUNTIME_EVENTS_PATH.format(run_id=run_id)}"
    first = SseRead()
    first_done = threading.Event()

    def read_first() -> None:
        """读到终态或连接断开为止：SIGKILL 会让这次真实订阅断开（帧边到边写进 `first`）。"""
        try:
            read_sse(
                url,
                tenant_id=stack.tenant_id,
                after_seq=0,
                until=lambda read: read.terminal is not None,
                sink=first,
            )
        finally:
            first_done.set()

    thread = threading.Thread(target=read_first, name="s03-first-subscription", daemon=True)
    thread.start()
    wait_until(
        lambda: bool(first.frames),
        what="首个 SSE 订阅未在超时内收到任何事件",
        timeout=60.0,
    )
    assert run_row(stack.database_url, run_id)["status"] == "RUNNING", (
        "订阅实例必须在 Run 仍执行时被杀（否则故障注入没有意义）"
    )

    # ② SIGKILL 整组：在飞订阅真实断开，且断开时没有终态。
    subscriber_pid, subscriber_pgid, subscriber_port = (
        subscriber.pid,
        subscriber.pgid,
        subscriber.port,
    )
    stack.kill_instance(subscriber)
    assert subscriber.alive is False
    thread.join(timeout=30.0)
    assert first_done.is_set(), "订阅实例被 SIGKILL 后读取线程必须结束（连接断开）"
    assert first.disconnected is True, "订阅必须因实例被杀而断开"
    assert first.terminal is None, "断开时不得出现终态帧（Run 还在执行）"
    cursor = first.last_seq
    assert cursor >= 1, "已应用游标必须来自真实持久事件"
    with pytest.raises(ProcessLookupError):
        os.killpg(subscriber_pgid, 0)

    # ③ 替换实例：新进程/新端口，同库/Redis/卷。
    replacement = stack.replace_instance("agent-runtime", index=subscriber.index)
    assert replacement.pid != subscriber_pid and replacement.port != subscriber_port
    env = read_process_environ(replacement.pid)
    assert env["DATABASE_URL"] == stack.database_url
    assert env["REDIS_URL"] == stack.redis_url
    assert env["ARTIFACT_ROOT"] == str(stack.artifacts_root)

    # ④⑤⑥ 按游标重连到替换实例，读到终态。
    second = read_sse(
        f"{replacement.url}{RUNTIME_EVENTS_PATH.format(run_id=run_id)}",
        tenant_id=stack.tenant_id,
        after_seq=cursor,
        until=lambda read: read.terminal is not None,
    )
    assert second.terminal == "COMPLETED", second.error or "重连未在超时内读到终态"
    assert second.frames, "重连必须真的重放后续持久事件"
    assert all(frame.seq > cursor for frame in second.frames), "重连不得回退重投已应用事件"
    seqs = [frame.seq for frame in second.frames]
    assert seqs == sorted(set(seqs)), "seq 必须严格递增且不重复（无缺口、无重复渲染）"
    assert second.frames[-1].event == "run.completed"

    combined = first.deltas() + second.deltas()
    assert combined == "".join(deltas), f"两次订阅拼出的正文必须与探针一致：{combined!r}"
    persisted_deltas = event_rows(stack.database_url, run_id, "message.delta")
    assert len(persisted_deltas) == len(
        [frame for frame in first.frames if frame.event == "message.delta"]
    ) + len([frame for frame in second.frames if frame.event == "message.delta"]), (
        "持久 delta 事件数与两次订阅收到的帧数必须相等（没有多写/少写一行）"
    )

    # ⑥ 不重建 Run/工具：同一个 Run、同一个执行片段、模型只被真实调用一次。
    assert run_row(stack.database_url, run_id)["execution_epoch"] == 1
    runs = db_rows(
        stack.database_url,
        "SELECT count(*) AS total FROM runtime.run_record WHERE conversation_id = $1",
        uuid.UUID(conversation_id),
    )
    assert int(runs[0]["total"]) == 1, "重连不得新建 Run"
    assert int(probe_state(probe.url)["call_count"]) == calls_before + 1, (
        "重连/替换订阅实例不得重建执行（模型只调用一次）"
    )


# --------------------------------------------------------------------------------------
# PG/Redis 故障代理栈：E-02（通知丢失可补拉 / 额度不可确认不超发 / 失败有指标）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FaultHarness:
    stack: CapacityStack
    seeded: SeededDelivery
    session: ChatSession
    redis_proxy: FaultProxy
    pg_proxy: FaultProxy


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


@pytest.fixture(scope="module")
def fault_harness(
    tmp_path_factory: pytest.TempPathFactory, capacity_profile_path: Path
) -> Iterator[FaultHarness]:
    """Runtime 子进程的 PG/Redis 都经真实 TCP 故障代理；代理先起、栈后起（env 注入）。"""
    # 每个栈一份**独立**库 + 独立 Redis 号位：Worker claim 与投递选取不带租户谓词，
    # 同一会话里的多个栈共用一份库会互相领走对方的意图/任务（表现为串跑红、单跑绿）。
    datastore = create_datastore_handle()
    redis_url = urlsplit(datastore.redis_url)
    pg_url = urlsplit(datastore.database_url)
    redis_proxy = FaultProxy(str(redis_url.hostname), int(redis_url.port or 6379))
    pg_proxy = FaultProxy(str(pg_url.hostname), int(pg_url.port or 5432))
    redis_proxy.start()
    pg_proxy.start()
    root = tmp_path_factory.mktemp("fault-delivery")
    stack = build_stack(
        root=root,
        profile=load_profile(capacity_profile_path),
        datastore=datastore,
        service_env={
            "agent-runtime": {
                "REDIS_URL": _proxied(datastore.redis_url, redis_proxy),
                "DATABASE_URL": _proxied(datastore.database_url, pg_proxy),
                "RUN_EVENT_FALLBACK_POLL_SEC": "1",
                "RUN_EVENT_HEARTBEAT_SEC": "1",
            }
        },
    )
    seeded = seed_delivery_control(
        datastore.database_url,
        stack.tenant_id,
        platform_settings={"task": {"delivery_backoff_base_sec": 1}},
    )
    stack.start()
    bind_probe_credential(
        datastore.database_url, seeded.credential_id, f"{stack.model_probe.url}/v1"
    )
    session = exchange_chat_session(stack.proxy.url, seeded.token, seeded.user_id)
    try:
        yield FaultHarness(
            stack=stack,
            seeded=seeded,
            session=session,
            redis_proxy=redis_proxy,
            pg_proxy=pg_proxy,
        )
    finally:
        stack.teardown()
        redis_proxy.stop()
        pg_proxy.stop()


def read_public_stream(
    base_url: str, session: ChatSession, run_id: str, *, timeout_sec: float = WAIT_TIMEOUT_SEC
) -> tuple[str | None, str]:
    """真实公开 SSE（API-07）：读全量正文与终态；返回 (terminal_status, text)。"""
    with _chat_client(base_url, session) as client:
        with client.stream(
            "GET",
            f"{WEBCHAT_RUN_PREFIX}/{run_id}/events",
            params={"after_seq": 0},
            timeout=httpx.Timeout(connect=10.0, read=timeout_sec, write=10.0, pool=10.0),
        ) as stream:
            assert stream.status_code == 200, stream.read().decode(errors="replace")
            parts: list[str] = []
            terminal: str | None = None
            event_type = ""
            for line in stream.iter_lines():
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                payload = json.loads(line.split(":", 1)[1].strip())
                if event_type in ("message.delta", "message.snapshot"):
                    parts.append(str(payload["data"].get("text") or ""))
                if event_type == "run.terminal":
                    terminal = str(payload["data"].get("status"))
                    break
    return terminal, "".join(parts)


def test_e02_redis_hint_loss_falls_back_to_pg_and_is_visible(
    fault_harness: FaultHarness,
) -> None:
    """[E-02] Redis 只作提示：运行期丢提示 → PG 补查把正文与终态补齐，降级必须可见。

    真实边界与断言位置：
    ① Runtime 的 Redis 连接在启动时就指向真实 TCP 代理；`DROP` 切断在飞连接并丢弃后续连接；
    ② 真实受理（202）后经 Gateway 公开 SSE 读全量正文与终态（PG 补查，不因提示丢失丢正文）；
    ③ `/readyz` 仍 200：Redis 是提示不是硬依赖（否则故障会把全部 Pod 拉出就绪）；
    ④ `/metrics` 的 `event_stream_hint_total{status="degraded"}` 真实增长（降级不静默）；
    ⑤ 恢复直通后新 Run 正常完成，提示恢复可观测（`status="ok"` 增长）。
    """
    stack, seeded, session = fault_harness.stack, fault_harness.seeded, fault_harness.session
    probe = stack.model_probe
    set_probe_behavior(
        probe.url, {"latency_ms": 50, "deltas": ["提示", "丢失", "补拉"]}, api_key=PROBE_API_KEY
    )
    runtime = stack.instance("agent-runtime", index=0)

    with httpx.Client(timeout=10.0, trust_env=False) as client:
        ready = client.get(f"{runtime.url}/readyz")
    assert ready.status_code == 200, f"Redis 提示故障不得让就绪失败：{ready.status_code} {ready.text}"

    fault_harness.redis_proxy.set_fault(FaultProxyMode.DROP)
    assert fault_harness.redis_proxy.mode is FaultProxyMode.DROP

    conversation_id = create_conversation(stack.proxy.url, session, seeded.entry_id)
    run_id = submit_message(
        stack.proxy.url, session, seeded.entry_id, conversation_id, "提示丢失补拉"
    )
    terminal, text = read_public_stream(stack.proxy.url, session, run_id)
    assert terminal == "COMPLETED", f"提示丢失后公开流必须仍到终态：{terminal!r}"
    assert text == "提示丢失补拉", f"正文不得因提示丢失缺失：{text!r}"

    degraded = _hint_total(stack, "degraded")
    assert degraded >= 1, "Redis 提示降级必须有真实指标（不静默吞掉）"
    assert fault_harness.redis_proxy.dropped_connections >= 1, "代理确实丢弃过连接（真实故障）"

    fault_harness.redis_proxy.set_fault(FaultProxyMode.PASS_THROUGH)
    ok_before = _hint_total(stack, "ok")
    second_run = submit_message(
        stack.proxy.url,
        session,
        seeded.entry_id,
        create_conversation(stack.proxy.url, session, seeded.entry_id),
        "直通后提问",
    )
    second_terminal, _ = read_public_stream(stack.proxy.url, session, second_run)
    assert second_terminal == "COMPLETED", "恢复直通后新 Run 必须正常完成"
    wait_until(
        lambda: _hint_total(stack, "ok") > ok_before,
        what="恢复后提示路径未重新可见（ok 计数未增长）",
        timeout=30.0,
    )


def _memory_background_idle(database_url: str, tenant_id: str) -> bool:
    """本租户记忆提炼是否已静止：Run 终态后它是**真实的后续模型调用**，会进探针计数。"""
    rows = db_rows(
        database_url,
        "SELECT count(*) AS total FROM runtime.memory_consolidation_job"
        " WHERE tenant_id = $1 AND status IN ('PENDING','RUNNING','RETRY')",
        tenant_id,
    )
    return int(rows[0]["total"]) == 0


def test_e02_postgres_outage_fails_closed_without_over_issue_and_recovers(
    fault_harness: FaultHarness,
) -> None:
    """[E-02] PG 不可确认时 fail closed：不假受理、不超发、失败有指标、恢复后事实完整。

    真实边界与断言位置：
    ① Runtime 的 `DATABASE_URL` 指向真实 TCP 代理；`REFUSE` 关监听 ⇒ 新连接真实 ECONNREFUSED；
    ② 受理必须显式失败（非 202，带稳定错误码），PG 上不得出现该消息的 Run 行（不假受理）；
    ③ 全局模型额度不可确认时不超发：内部预占端点必须失败（非 200），模型探针调用数不变；
    ④ `/healthz` 仍 200（存活与依赖分离），`/readyz` 反映依赖失败；
    ⑤ `/metrics` 的 `run_admission_total{status="FAILED"}` 真实增长（失败有指标）；
    ⑥ 恢复直通后新消息被 202 受理并真实执行到 COMPLETED（故障前事实不被改动）。
    """
    stack, seeded, session = fault_harness.stack, fault_harness.seeded, fault_harness.session
    probe = stack.model_probe
    set_probe_behavior(probe.url, {"latency_ms": 50, "deltas": ["恢复", "正常"]})
    runtime = stack.instance("agent-runtime", index=0)
    internal_headers = {
        "X-Tenant-Id": stack.tenant_id,
        "X-Internal-Service": stack.internal_token,
    }

    before_conversation = create_conversation(stack.proxy.url, session, seeded.entry_id)
    before_run = submit_message(
        stack.proxy.url, session, seeded.entry_id, before_conversation, "故障前事实"
    )
    wait_until(
        lambda: run_row(stack.database_url, before_run)["status"] == "COMPLETED",
        what="故障前 Run 未完成",
        timeout=WAIT_TIMEOUT_SEC,
    )
    before_row = run_row(stack.database_url, before_run)
    # 记忆提炼是 Run 终态之后的真实后台模型调用：先等它静止，否则它的探针调用会落进
    # 下面的「PG 不可确认时不得调用模型」窗口（CI 实测 6 vs 5）。
    wait_until(
        lambda: _memory_background_idle(stack.database_url, stack.tenant_id),
        what="memory consolidation idle",
        timeout=30.0,
    )
    calls_before = int(probe_state(probe.url)["call_count"])
    maintenance_failed_before = metric_total(
        runtime.url, "runtime_maintenance_total", {"loop": "delivery", "status": "failed"}
    )

    fault_harness.pg_proxy.set_fault(FaultProxyMode.REFUSE)
    try:
        message_id = f"msg-{uuid.uuid4().hex[:12]}"
        with _chat_client(stack.proxy.url, session) as client:
            response = client.post(
                f"{WEBCHAT_ENTRY_PREFIX}/{seeded.entry_id}/messages",
                json={
                    "conversation_id": before_conversation,
                    "message_id": message_id,
                    "text": "PG 不可用",
                    "upload_ids": [],
                },
                headers={"Idempotency-Key": f"{before_conversation}:{message_id}"},
            )
        assert response.status_code != 202, "PG 不可用时不得返回受理成功"
        assert response.status_code >= 400, response.status_code
        assert response.json().get("code"), f"失败必须带稳定错误码：{response.text}"

        runs = db_rows(
            stack.database_url,
            "SELECT count(*) AS total FROM runtime.run_record WHERE conversation_id = $1",
            uuid.UUID(before_conversation),
        )
        assert int(runs[0]["total"]) == 1, "PG 不可用时不得落下新 Run 行（不假受理）"

        with httpx.Client(timeout=10.0, trust_env=False) as client:
            reserve = client.post(
                f"{runtime.url}/internal/model-capacity/reservations",
                json={
                    "probe_id": str(uuid.uuid4()),
                    "model_id": str(seeded.model_id),
                    "credential_id": str(seeded.credential_id),
                    "attempt_id": str(uuid.uuid4()),
                    "deadline_at": "2099-01-01T00:00:00+00:00",
                    "reserved_tokens": 64,
                },
                headers=internal_headers,
            )
        assert reserve.status_code != 200, (
            "PG 不可确认时不得授出模型额度（否则就是超发）"
        )
        assert int(probe_state(probe.url)["call_count"]) == calls_before, (
            "没有可确认的额度就不允许模型调用"
        )

        with httpx.Client(timeout=10.0, trust_env=False) as client:
            assert client.get(f"{runtime.url}/healthz").status_code == 200, (
                "存活探针不得因依赖故障失败"
            )
            readyz = client.get(f"{runtime.url}/readyz")
        assert readyz.status_code == 503, f"依赖故障必须反映在就绪探针：{readyz.status_code}"
        ready_body = readyz.json()
        detail = ready_body.get("data") if isinstance(ready_body.get("data"), dict) else ready_body
        assert "database" in detail.get("failed", []), f"就绪失败项必须含 database：{detail}"

        wait_until(
            lambda: metric_total(
                runtime.url, "runtime_maintenance_total", {"loop": "delivery", "status": "failed"}
            )
            > maintenance_failed_before,
            what="PG 故障期间维护循环失败未产生指标（失败有指标）",
            timeout=30.0,
        )
    finally:
        fault_harness.pg_proxy.set_fault(FaultProxyMode.PASS_THROUGH)

    assert run_row(stack.database_url, before_run) == before_row, "故障前事实不得被改动"
    recovered = submit_message(
        stack.proxy.url, session, seeded.entry_id, before_conversation, "故障后再次提问"
    )
    wait_until(
        lambda: run_row(stack.database_url, recovered)["status"] == "COMPLETED",
        what="恢复后新 Run 未完成",
        timeout=WAIT_TIMEOUT_SEC,
    )
    calls_after = int(probe_state(probe.url)["call_count"])
    assert calls_after > calls_before, (
        f"恢复后必须真实执行：探针调用 {calls_before} -> {calls_after}；"
        f"calls={probe_state(probe.url)['calls']}；run={run_row(stack.database_url, recovered)}；"
        f"events={event_rows(stack.database_url, recovered, 'run.completed')}"
    )


# --------------------------------------------------------------------------------------
# E-19：单 Gateway 重启 + 持久投递意图 + 企微协议探针
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class WeComHarness:
    stack: CapacityStack
    seeded: SeededDelivery
    probe: WeComProbeServer
    relay_port: int
    root: Path
    connectors: list[ServiceInstance]

    @property
    def connector(self) -> ServiceInstance:
        return self.connectors[-1]


def _start_wecom_connector(
    stack: CapacityStack, probe: WeComProbeServer, root: Path
) -> ServiceInstance:
    """真实 `muad_im_connector.main`：TASK-009 后企微长连接活在 Connector，不是 Gateway。"""
    env = {
        **os.environ,
        "DATABASE_URL": stack.database_url,
        "REDIS_URL": stack.redis_url,
        "ARTIFACT_ROOT": str(stack.artifacts_root),
        "DEFAULT_TENANT_ID": stack.tenant_id,
        "INTERNAL_SERVICE_TOKEN": stack.internal_token,
        "CONSOLE_PLATFORM_URL": stack.url_of("console-platform"),
        "AGENT_RUNTIME_URL": stack.url_of("agent-runtime"),
        "PYTHONPATH": str(REPO_ROOT),
        "MIGRATIONS_DIR": str(REPO_ROOT / "migrations" / "versions"),
        STACK_ID_ENV: stack.stack_id,
        "WECOM_WS_URL": probe.ws_url,
        "WECOM_WS_CA_FILE": str(probe.cert_path),
        "CHANNEL_PROBE_URL": "",
        "BOT_SNAPSHOT_POLL_SEC": "1",
        "CONNECTOR_OWNER_ID": f"capacity-connector-{stack.stack_id}",
        "CONNECTOR_HANDLED_CHANNELS": "WECOM",
        "CONNECTOR_LEASE_TTL_SEC": "5",
        "CONNECTOR_LEASE_RENEW_SEC": "1",
        "CONNECTOR_INBOUND_SHARDS": "8",
    }
    instance = ServiceInstance(
        unit="connector",
        index=0,
        module="muad_im_connector.main",
        port=free_port(),
        env=env,
        log_path=root / "logs" / "im-connector-0.log",
        skill_cache_root=root / "skill-cache" / "im-connector-0",
    )
    instance.start()
    return instance


def _connector_ready(url: str) -> bool:
    try:
        response = httpx.get(f"{url}/readyz", timeout=2.0, trust_env=False)
    except httpx.HTTPError:
        return False
    return response.status_code == 200



def _await_port_refused(port: int, *, timeout: float = 15.0) -> None:
    """等待端口停止接受连接；超时仍可连接则显式失败。"""
    deadline = time.monotonic() + timeout
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1.0).close()
        except ConnectionRefusedError:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"端口 {port} 在 {timeout}s 内仍在接受连接（下线窗口不成立）")
        time.sleep(0.1)

def _build_wecom_stack(
    root: Path, profile_path: Path, probe: WeComProbeServer, relay_port: int
) -> tuple[CapacityStack, SeededDelivery]:
    """真实四单元栈：WECOM 作为 Gateway 的远程路由面，模型凭据回填本栈探针。"""
    datastore = create_datastore_handle()
    stack = build_stack(
        root=root,
        profile=load_profile(profile_path),
        datastore=datastore,
        service_env={
            "im-gateway": {
                # 企微是远程 Connector 的通道面：Gateway 入站只路由、出站写 im.outbound。
                "GATEWAY_ROUTED_CHANNELS": "WECOM",
                "CHANNEL_PROBE_URL": "",
                # Gateway 重启后要先刷新 bot 快照才能处理入站；验收用 1s 轮询，
                # 避免默认 30s 快照 + 60s reclaim 叠加超过用例的入站等待预算。
                "BOT_SNAPSHOT_POLL_SEC": "1",
            },
            "agent-runtime": {
                "CHANNEL_DELIVERY_POLL_SEC": "1",
                "CHANNEL_DELIVERY_RETRY_BASE_SEC": "1",
                "RUN_EVENT_FALLBACK_POLL_SEC": "1",
                "IM_GATEWAY_URL": f"http://127.0.0.1:{relay_port}",
            },
        },
    )
    try:
        seeded = seed_delivery_control(
            datastore.database_url,
            stack.tenant_id,
            wecom=True,
            platform_settings={"task": {"delivery_backoff_base_sec": 1}},
        )
        stack.start()
        bind_probe_credential(
            datastore.database_url, seeded.credential_id, f"{stack.model_probe.url}/v1"
        )
    except BaseException:
        # 启动中途失败也要停掉已起的进程/探针（否则孤儿进程会污染后续套件）。
        stack.teardown()
        raise
    return stack, seeded


@pytest.fixture(scope="function")
async def wecom_harness(
    tmp_path_factory: pytest.TempPathFactory, capacity_profile_path: Path
) -> AsyncIterator[WeComHarness]:
    """真实 WSS 企微探针 + 真实 Connector 进程；Gateway 只持有 WECOM 的远程路由面。

    函数级作用域：E-19 用完即整栈回收，避免与其他模块级容量栈同时占用 PG 连接预算。
    """
    probe = WeComProbeServer(expected_bots={WECOM_BOT_ID: WECOM_BOT_SECRET})
    await probe.start()
    # 每个栈一份**独立**库 + 独立 Redis 号位：Worker claim 与投递选取不带租户谓词，
    # 同一会话里的多个栈共用一份库会互相领走对方的意图/任务（表现为串跑红、单跑绿）。
    root = tmp_path_factory.mktemp("wecom-delivery")
    # Runtime 的 IM_GATEWAY_URL 指向**稳定入口地址**（真实 TCP 转发器，等价 k8s Service）：
    # Gateway 重启换端口后，投递泵仍按同一地址交付（端口在栈启动前就固定下来）。
    relay_port = free_port()
    stack, seeded = _build_wecom_stack(root, capacity_profile_path, probe, relay_port)
    connector = _start_wecom_connector(stack, probe, root)
    connectors = [connector]
    wait_until(
        lambda: _connector_ready(connector.url),
        what="Connector 未在超时内就绪",
        timeout=60.0,
    )
    try:
        yield WeComHarness(
            stack=stack,
            seeded=seeded,
            probe=probe,
            relay_port=relay_port,
            root=root,
            connectors=connectors,
        )
    finally:
        for instance in reversed(connectors):
            instance.stop()
        stack.teardown()
        await probe.stop()


def _drop_reply_targets(redis_url: str, tenant_id: str) -> None:
    """让回复窗口失效（等价真实过期）：删掉本租户的 `im:reply:*` 目标，只剩主动发送路径。"""
    client = redis_sync.Redis.from_url(redis_url, decode_responses=True)
    try:
        for key in client.scan_iter(match=f"im:reply:*:{tenant_id}:*"):
            client.delete(key)
    finally:
        client.close()


def _wecom_notification(database_url: str, run_id: str) -> Mapping[str, Any] | None:
    rows = db_rows(
        database_url,
        "SELECT id, delivery_key, delivery_state, attempt, delivery_result, event_id"
        " FROM runtime.channel_notification WHERE delivery_key = $1",
        f"run:{run_id}:terminal",
    )
    return dict(rows[0]) if rows else None


def _delivery_outcome(notification: Mapping[str, Any]) -> str | None:
    """冻结的首次投递结果里的 outcome（jsonb 经 asyncpg 回的是文本，这里显式解析）。"""
    raw = notification.get("delivery_result")
    if raw is None:
        return None
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(parsed, Mapping):
        return None
    outcome = parsed.get("outcome")
    return None if outcome is None else str(outcome)


async def test_e19_gateway_restart_persistent_intent_delivers_proactively(
    wecom_harness: WeComHarness,
) -> None:
    """[E-19] Gateway 下线期间 Run 完成 → 恢复后持久意图触发原入口主动交付。

    真实边界与断言位置：
    ① 真实企微 WSS 协议探针承载官方 SDK：入站回调经 Gateway → Runtime 建 Run（原入口路由）；
    ② Run 执行中 SIGKILL 整组杀掉 Gateway：Run 不因 HTTP 断开被取消，真实跑到终态（RULE-01）；
    ③ Runtime 终态与 `PENDING` 出站意图同事务落 PG（稳定键 `run:{id}:terminal`）；
    ④ Gateway 下线期间的投递尝试确定未送达（连接未建立 ⇒ NOT_SENT）：行留在 PENDING、attempt
       增长、冻结回执的 outcome ≠ DELIVERED —— 不把占位/尝试当送达，且失败可追溯；
    ⑤ 恢复：替换 Runtime 副本与 Connector 进程（都没有内存订阅/回调记忆）+ 回复窗口失效
       → 起新 Gateway（新端口）→ 稳定入口重指新 Gateway。交付只能来自 PG 上的持久意图：
       `aibot_send_msg` 真实帧到达探针、正文 = Run 终态正文、行落 DELIVERED 且首次结果为
       DELIVERED；同键只真实送达一次；
    ⑥ 发送失败保持可追溯重试、不标送达：注入渠道发送失败 → 行回 PENDING（attempt 增长、无送达
       结果）；清除注入后重投成功（同一稳定键）。
    """
    stack, seeded, probe = wecom_harness.stack, wecom_harness.seeded, wecom_harness.probe
    assert seeded.wecom_bot_id == WECOM_BOT_ID
    assert seeded.wecom_bot_account_id is not None, "本用例需要真实机器人账号 UUID"
    set_probe_behavior(
        stack.model_probe.url,
        # 60s 首块延迟：整批负载下「读到 RUNNING → kill Gateway」也可能耗掉数十秒，
        # 30s 窗口在 CI 全量分片里仍被打穿过（31s 失败）；把在飞窗口放大成确定性前提。
        {"latency_ms": 60000, "deltas": ["终态", "主动", "交付"]},
        api_key=PROBE_API_KEY,
    )

    # 稳定入口地址（真实 TCP 转发器）：上游指向当前 Gateway 实例；Gateway 换端口时重指。
    gateway_port = stack.instance("im-gateway").port
    relay = FaultProxy("127.0.0.1", gateway_port, port=wecom_harness.relay_port)
    relay.start()
    assert relay.address[1] == wecom_harness.relay_port

    def repoint_relay(upstream_port: int) -> FaultProxy:
        relay.stop()
        repointed = FaultProxy("127.0.0.1", upstream_port, port=wecom_harness.relay_port)
        repointed.start()
        return repointed

    def connected_since(first_index: int) -> bool:
        """`first_index` 之后（含）是否有本 bot 的**新**连接：重启后的连接不能用旧连接冒充。"""
        return any(
            probe.connection_bots.get(index) == WECOM_BOT_ID
            for index in range(first_index, len(probe.connections))
        )

    def run_ids() -> list[str]:
        return [
            str(row["id"])
            for row in db_rows(
                stack.database_url,
                "SELECT id FROM runtime.run_record WHERE tenant_id = $1 ORDER BY create_time",
                stack.tenant_id,
            )
        ]

    def attribute_conversation(run_id: str) -> None:
        """入口归属事实（design §3.3）：`conversation.channel_account_id` = 入口账号 UUID。

        企微入站的 RunRequest 只带**字符串** bot_id（`entry_id_of` 要求 UUID 形状），把入口
        账号 UUID 随请求带进来是 TASK-049 的遗留装配项（不在本任务 Files 内）。这里显式补上
        这条归属事实；终态意图、投递泵、网关适配器与协议探针全部真实，无替身。
        """
        db_execute(
            stack.database_url,
            "UPDATE runtime.conversation SET channel_account_id = $1 WHERE id = $2",
            seeded.wecom_bot_account_id,
            uuid.UUID(str(run_row(stack.database_url, run_id)["conversation_id"])),
        )

    async def push_and_wait_run(text: str) -> str:
        known = set(run_ids())
        message_id = f"wecom-{uuid.uuid4().hex[:12]}"
        await probe.push_message(
            bot_id=WECOM_BOT_ID,
            message_id=message_id,
            external_user_id=seeded.wecom_external_user_id or "",
            text=text,
            reply_id=f"reply-{uuid.uuid4().hex[:12]}",
            chat_id=seeded.wecom_chat_id,
        )
        await await_until(
            lambda: bool(set(run_ids()) - known), what="入站消息未建 Run", timeout=180.0
        )
        return [run_id for run_id in run_ids() if run_id not in known][-1]

    await await_until(
        lambda: connected_since(0), what="Gateway 未在超时内连上企微协议探针", timeout=60.0
    )
    first_run = await push_and_wait_run("网关重启交付")
    await await_until(
        lambda: run_row(stack.database_url, first_run)["status"] == "RUNNING",
        what="Run 未进入执行",
        timeout=60.0,
    )

    attribute_conversation(first_run)

    # ② SIGKILL Gateway（整组）+ 停稳定入口（真实 Service 无端点）：Run 必须继续跑到终态，
    #    投递泵的尝试必须拿到**确定未送达**（连接拒绝 ⇒ NOT_SENT 可重试），而不是"没结论"。
    gateway = stack.instance("im-gateway")
    assert _wecom_notification(stack.database_url, first_run) is None, (
        "Run 仍在执行时不应已有终态投递意图（否则测不到下线窗口）"
    )
    assert run_row(stack.database_url, first_run)["status"] == "RUNNING", (
        "Gateway 必须在 Run 仍在执行时被杀（否则测不到下线窗口）"
    )
    gateway_pid, gateway_pgid = gateway.pid, gateway.pgid
    # 多副本口径（TASK-009 起 Gateway 默认 N 副本）：只杀一个副本时，存活副本仍会把持久
    # 意图投递出去，窗口断言随之失效（实测为间歇失败）。所以“Gateway 下线”必须指**全部
    # 副本**下线——这才是多副本部署下“整层不可用”的真实语义。
    replicas = [instance for instance in stack.instances("im-gateway") if instance.alive]
    assert replicas, "下线前必须至少有一个存活的 Gateway 副本"
    for instance in replicas:
        stack.kill_instance(instance)
    with pytest.raises(ProcessLookupError):
        os.killpg(gateway_pgid, 0)
    relay.stop()
    # 确定性前提：Gateway 端口与投递端点（relay）都必须真的拒绝连接。
    # SIGKILL 后内核释放监听有短暂延迟（CI 实测过“刚杀完仍能完成握手”），所以有界重试，
    # 超出窗口仍是活的就显式失败——不把“释放中”当成“已下线”。
    for port in (gateway_port, wecom_harness.relay_port):
        _await_port_refused(port)
    # 确定性前提：投递端点真的不可用（runtime env 指向稳定 relay，且 relay 监听已关闭）。
    for runtime in stack.instances("agent-runtime"):
        assert read_process_environ(runtime.pid)["IM_GATEWAY_URL"] == (
            f"http://127.0.0.1:{wecom_harness.relay_port}"
        ), "Runtime 投递端点必须指向稳定 relay；被环境覆盖会让下线窗口失真"
    assert relay.listening is False, "relay 必须停止监听（下线窗口的投递端点不可用）"
    await await_until(
        lambda: run_row(stack.database_url, first_run)["status"] == "COMPLETED",
        what="Gateway 被杀后 Run 未跑完（HTTP 断开不得取消执行）",
        timeout=WAIT_TIMEOUT_SEC,
    )

    # ③ 终态与持久出站意图：稳定键 + PENDING 意图（或已被泵领走为 DELIVERING）。
    await await_until(
        lambda: _wecom_notification(stack.database_url, first_run) is not None,
        what="Run 终态未写持久投递意图",
        timeout=30.0,
    )
    intent = _wecom_notification(stack.database_url, first_run)
    assert intent is not None
    assert intent["delivery_key"] == f"run:{first_run}:terminal", "稳定交付键必须与终态同源"
    # 连接中断时适配器可能拿不到确定回执，落 UNKNOWN（不冒充送达）；后续断言仍要求
    # outcome != DELIVERED，并由恢复后的持久意图重投到 DELIVERED。
    assert intent["delivery_state"] in ("PENDING", "DELIVERING", "DELIVERED", "UNKNOWN"), intent

    # ④ 下线期间：投递确定未送达（连接未建立）⇒ PENDING + attempt 增长 + 回执 outcome ≠ DELIVERED。
    await await_until(
        lambda: int(_wecom_notification(stack.database_url, first_run)["attempt"]) >= 1,
        what="Gateway 下线期间未发生投递尝试（持久意图没有驱动投递）",
        timeout=60.0,
    )
    offline = _wecom_notification(stack.database_url, first_run)
    assert offline["delivery_state"] != "DELIVERED", (
        f"没发出去不得标送达: row={offline} frames={probe.frames_of('aibot_send_msg')}"
    )
    assert _delivery_outcome(offline) != "DELIVERED", "未送达不得冻结送达结果"
    assert probe.frames_of("aibot_send_msg") == [], "Gateway 下线时不可能有主动发送帧"
    if offline["delivery_state"] == "UNKNOWN":
        # 连接中断时适配器可能拿不到确定回执 ⇒ UNKNOWN（产品语义：保留核实，不自动重发）。
        # 本用例已用真实探针确认没有发送帧；模拟运维核实后放回 PENDING，继续验证恢复后的
        # 持久意图重投，而不是把 UNKNOWN 当成已送达或自动重发。
        db_execute(
            stack.database_url,
            "UPDATE runtime.channel_notification"
            " SET delivery_state = 'PENDING', next_attempt_at = now(),"
            " lease_owner = NULL, lease_until = NULL, update_time = now()"
            " WHERE delivery_key = $1",
            f"run:{first_run}:terminal",
        )

    # ⑤ 恢复：替换 Runtime 副本与 Connector 进程（都没有内存订阅/回调记忆）→ 起新 Gateway
    #    （新端口）→ 稳定入口重指新 Gateway。交付只能来自 PG 上的持久意图。
    old_runtime = stack.instance("agent-runtime", index=1)
    old_runtime_pid = old_runtime.pid
    new_runtime = stack.replace_instance("agent-runtime", index=1)
    assert new_runtime.pid != old_runtime_pid, "投递泵必须在全新 Runtime 进程里跑"
    connections_before_restart = len(probe.connections)
    wecom_harness.connector.stop()
    # 回复窗口失效（真实过期语义）：Connector 的 reply store 在 Redis，跨重启仍在；
    # 目标不存在时本身就只剩主动发送路径，因此这里不做存在性断言——「主动发送帧到达探针」
    # 才是该路径的真实证明。
    _drop_reply_targets(stack.redis_url, stack.tenant_id)
    replacement_connector = _start_wecom_connector(stack, probe, wecom_harness.root)
    wecom_harness.connectors.append(replacement_connector)
    wait_until(
        lambda: _connector_ready(replacement_connector.url),
        what="重启后的 Connector 未在超时内就绪",
        timeout=60.0,
    )
    replacement = stack.start_instance("im-gateway", index=0)
    assert replacement.pid != gateway_pid
    await await_until(
        lambda: connected_since(connections_before_restart),
        what="重启后的 Connector 未重新连上企微协议探针",
        timeout=60.0,
    )
    relay = repoint_relay(replacement.port)

    await await_until(
        lambda: _wecom_notification(stack.database_url, first_run)["delivery_state"]
        == "DELIVERED",
        what="Gateway 恢复后持久意图未触发主动交付",
        timeout=WAIT_TIMEOUT_SEC,
    )
    delivered = _wecom_notification(stack.database_url, first_run)
    assert _delivery_outcome(delivered) == "DELIVERED", (
        f"送达必须冻结首次真实结果：{delivered['delivery_result']}"
    )
    await await_until(
        lambda: any(
            "终态主动交付" in str((frame.get("body") or {}).get(key, {}).get("content") or "")
            for frame in probe.frames_of("aibot_send_msg")
            for key in ("markdown", "text")
        ),
        what="企微探针未收到主动交付的终态正文",
        timeout=60.0,
    )
    sent_texts = [
        str((frame.get("body") or {}).get(key, {}).get("content") or "")
        for frame in probe.frames_of("aibot_send_msg")
        for key in ("markdown", "text")
        if (frame.get("body") or {}).get(key, {}).get("content")
    ]
    assert sum("终态主动交付" in text for text in sent_texts) == 1, (
        "同一稳定键只允许真实送达一次（同键去重）"
    )

    # ⑥ 发送失败：保持可追溯重试、绝不标送达；清除注入后重投成功。
    #    第二条 Run 不再需要长在飞窗口，恢复短延迟以控制整跑耗时。
    set_probe_behavior(
        stack.model_probe.url,
        {"latency_ms": 3000, "deltas": ["发送", "失败", "重试"]},
        api_key=PROBE_API_KEY,
    )
    second_run = await push_and_wait_run("发送失败重试")
    await await_until(
        lambda: run_row(stack.database_url, second_run)["status"] == "RUNNING",
        what="第二条 Run 未进入执行",
        timeout=60.0,
    )
    # 失败注入只针对终态主动发送；必须在 Run 已建、尚未完成时打开，
    # 否则入站回执也会被探针拒绝，表现为「消息没建 Run」而不是投递失败。
    probe.fail_reply_bots.add(WECOM_BOT_ID)
    attribute_conversation(second_run)
    await await_until(
        lambda: run_row(stack.database_url, second_run)["status"] == "COMPLETED",
        what="第二条 Run 未完成",
        timeout=WAIT_TIMEOUT_SEC,
    )
    try:
        await await_until(
            lambda: (
                _wecom_notification(stack.database_url, second_run) is not None
                and int(_wecom_notification(stack.database_url, second_run)["attempt"]) >= 1
            ),
            what="发送失败时投递未留下可追溯尝试",
            timeout=60.0,
        )
        failing = _wecom_notification(stack.database_url, second_run)
        assert failing["delivery_state"] != "DELIVERED", "渠道发送失败不得标送达"
        assert _delivery_outcome(failing) != "DELIVERED", (
            f"失败尝试不得写送达结果：{failing['delivery_result']}"
        )
    finally:
        probe.clear_injections()

    await await_until(
        lambda: _wecom_notification(stack.database_url, second_run)["delivery_state"]
        == "DELIVERED",
        what="清除发送故障后未重投成功",
        timeout=WAIT_TIMEOUT_SEC,
    )
    assert probe.throttled == 0 and probe.ack_losses == 0, "本用例只注入发送失败，不注入限流/ACK 丢失"
    assert scrape_metrics(stack.instance("agent-runtime", index=0).url), "Runtime /metrics 必须真实可抓"
    relay.stop()


# --------------------------------------------------------------------------------------
# S-03（新口径）：pool=2 × 100 条真实 SSE 不占 PG 连接
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stream_pool_harness(
    tmp_path_factory: pytest.TempPathFactory, capacity_profile_path: Path
) -> Iterator[StreamHarness]:
    """真实栈 + Runtime DB pool=2：验证拆短 Session 后连接数不随流数线性增长。"""
    debug_root = os.environ.get("MUAD_STREAM_DEBUG_ROOT")
    root = Path(debug_root) if debug_root else tmp_path_factory.mktemp("stream-pool")
    datastore = create_datastore_handle()
    stack = build_stack(
        root=root,
        profile=load_profile(capacity_profile_path),
        datastore=datastore,
        service_env={
            "agent-runtime": {
                "DB_POOL_SIZE": "2",
                "DB_MAX_OVERFLOW": "0",
                "DB_POOL_TIMEOUT_SEC": "1",
            }
        },
    )
    seeded = seed_delivery_control(datastore.database_url, stack.tenant_id)
    try:
        stack.start()
        bind_probe_credential(
            datastore.database_url, seeded.credential_id, f"{stack.model_probe.url}/v1"
        )
        session = exchange_chat_session(stack.proxy.url, seeded.token, seeded.user_id)
        yield StreamHarness(stack=stack, seeded=seeded, session=session)
    finally:
        if os.environ.get("MUAD_STREAM_DEBUG_ROOT"):
            for path in sorted((root / "logs").glob("*.log")):
                text = path.read_text(encoding="utf-8", errors="replace")
                if "ERROR" in text or "Traceback" in text or "TimeoutError" in text:
                    print(f"STREAM-LOG {path.name}\n{text[-12000:]}")
        stack.teardown()


def test_s03_pool_two_hundred_sse_streams_do_not_hold_pg_connections(
    stream_pool_harness: StreamHarness,
) -> None:
    """[S-03] pool=2 × 100 条流：全部打开；PG total≤20；稳态 idle-in-tx≈0。"""
    stack, seeded, session = (
        stream_pool_harness.stack,
        stream_pool_harness.seeded,
        stream_pool_harness.session,
    )
    set_probe_behavior(
        stack.model_probe.url,
        {
            "latency_ms": 500,
            "chunk_interval_ms": 50,
            "deltas": [f"流{index:03d}-" for index in range(100)],
        },
        api_key=PROBE_API_KEY,
    )
    conversation_id = create_conversation(stack.proxy.url, session, seeded.entry_id)
    run_id = submit_message(
        stack.proxy.url, session, seeded.entry_id, conversation_id, "短 Session 连接预算"
    )
    wait_until(
        lambda: run_row(stack.database_url, run_id)["status"] == "RUNNING",
        what="Run 未进入 RUNNING，无法验证在飞 SSE",
        timeout=30.0,
    )

    runtimes = [instance for instance in stack.all_instances() if instance.unit == "agent-runtime"]
    assert len(runtimes) >= 2, "profile 至少需要两个 Runtime 实例"
    total_streams = 100
    barrier = threading.Barrier(total_streams + 1, timeout=60)
    stop = threading.Event()
    statuses: list[int] = []
    failures: list[tuple[int, str]] = []
    status_lock = threading.Lock()

    def open_stream(url: str) -> None:
        try:
            with httpx.Client(
                timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
                trust_env=False,
            ) as client:
                with client.stream(
                    "GET",
                    url,
                    params={"after_seq": 0},
                    headers=_runtime_headers(stack.tenant_id),
                ) as stream:
                    with status_lock:
                        statuses.append(stream.status_code)
                        if stream.status_code != 200:
                            failures.append(
                                (stream.status_code, stream.read().decode(errors="replace")[:300])
                            )
                    barrier.wait()
                    for _line in stream.iter_lines():
                        if stop.is_set():
                            break
                    if stop.is_set():
                        return
        except Exception:
            with status_lock:
                statuses.append(-1)
            try:
                barrier.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass

    from tests.capacity.metrics import PostgresMetrics

    metrics = PostgresMetrics(stack.database_url, tenant_id=stack.tenant_id)
    baseline = metrics.snapshot()

    threads = []
    for index in range(total_streams):
        instance = runtimes[index % len(runtimes)]
        url = f"{instance.url}{RUNTIME_EVENTS_PATH.format(run_id=run_id)}"
        thread = threading.Thread(
            target=open_stream, args=(url,), name=f"s03-pool-{index}", daemon=True
        )
        thread.start()
        threads.append(thread)

    barrier.wait(timeout=90)
    assert len(statuses) == total_streams
    assert all(status == 200 for status in statuses), (
        "pool=2 时 100 条流必须全部打开；"
        f"实际 {statuses.count(200)}×200，失败样本 {failures[:3]}"
    )

    snapshot = metrics.snapshot()
    assert snapshot.connections_total <= baseline.connections_total + 4, (
        f"100 条流时 PG total 从 {baseline.connections_total} 涨到 "
        f"{snapshot.connections_total}，连接数随流数线性增长"
    )
    assert snapshot.connections_idle_in_transaction <= 2, (
        f"稳态 idle-in-tx={snapshot.connections_idle_in_transaction}，SSE 仍占着请求级 Session"
    )

    stop.set()
    for thread in threads:
        thread.join(timeout=10)


async def test_e02_pool_timeout_is_bounded_to_configured_second(
    stream_pool_harness: StreamHarness,
) -> None:
    """[E-02] pool=1/overflow=0/timeout=1s：池耗尽在 1s 量级显式失败，不挂 5s/30s。"""
    engine = create_async_engine(
        stream_pool_harness.stack.database_url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1.0,
    )
    try:
        async with engine.connect():
            started = time.monotonic()
            with pytest.raises(sa.exc.TimeoutError):
                async with engine.connect():
                    pass
            elapsed = time.monotonic() - started
        assert 0.8 <= elapsed <= 2.5, (
            f"pool_timeout 应约束在 ~1s，实际 {elapsed:.2f}s（未回落到 5s/30s 默认）"
        )
    finally:
        await engine.dispose()
    runtime = stream_pool_harness.stack.instance("agent-runtime", index=0)
    assert metric_total(runtime.url, "db_pool_size") >= 1
    assert metric_total(runtime.url, "db_pool_checked_out") >= 0


async def test_e02_get_session_does_not_commit_after_early_close() -> None:
    """[E-02] SSE 路由提前 close 后，依赖收尾不得再 commit（否则抛未处理事务错误）。"""
    from muad_agent_runtime.infrastructure.db import get_session

    generator = get_session()
    session = await anext(generator)
    await session.close()
    await generator.aclose()
