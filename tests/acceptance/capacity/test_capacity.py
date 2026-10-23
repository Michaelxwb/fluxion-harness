"""[S-01][S-14][S-15] 1000 用户容量、增量合并/断流恢复与单 Pod 恢复验收（TASK-116）。

设计来源：`agent-capacity-webchat.backend.design.md` §2.5 验收条件（S-01/S-14/S-15）、
§3.7 配置契约与生效边界、§4.3 分层验收与报告。

真实边界（不 mock 任何业务服务、不用伪造成功回执）：

- **1000 个不同用户各自一份真实 CHAT 主体**：真实 `PlatformUser`/授权/Token 行 + 真实 Token
  交换（经入口代理 → Console API-04），不是「1000 个空闲连接」；
- **真实 HTTP/SSE 负载**：消息经真实入口代理 → 真实 Gateway API-06 → 真实 Runtime → 真实 PG
  → TASK-106 真实模型协议探针；SSE 经真实 socket 读 `message.delta`/`run.stage`/`run.terminal`；
- **真实容量边界**：模型容量 = 物理凭据 `max_inflight`（真实 PG 行 + 探针在飞计数）；执行槽 =
  Runtime 启动配置（本 harness 显式声明 `RUNTIME_EXECUTION_CONCURRENCY`）；探针 `max_concurrency`
  是 socket 层观测到的真实同时推理数；
- **真实进程故障**：Console/Gateway 的原地重启（同地址、新进程 = 单 Pod 重启）与 Runtime/Worker
  的 SIGKILL 整组替换（`CapacityStack` 的进程组控制），单 Pod 不可用由入口代理的真实 502 计数记录；
- **真实预算观测**：`/metrics`（严格 Prometheus 解析）、PG 连接/事件字节、进程 RSS/FD（`ps`/`lsof`）。

编码期口径（任务 Checklist 明确「编码期不运行，留给 verify-e2e」）：本文件在编码期只做采集/静态
与轻量冒烟，**不执行全量 1000 用户负载与浏览器容量套件**；全量执行与结论留给需求级 verify-e2e
（TASK-118）。本文件不因为延后而弱化任何断言：阈值都来自锁定 profile、真实 PG 行与真实进程事实。

断言位置逐项对应 S-01/S-14/S-15 契约（见各用例 docstring 的①…⑥编号）。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from muad_common import SharedSettings

from tests.acceptance.capacity.delivery_harness import (
    ChatSession,
    SeededDelivery,
    bind_probe_credential,
    build_stack,
    create_datastore_handle,
    exchange_chat_session,
    probe_state,
    resolve_task_spec,
    seed_delivery_control,
    set_probe_behavior,
    wait_until,
)
from tests.acceptance.capacity.execution_support import (
    EXECUTION_CADENCE_ENV,
    build_run_payload,
    build_task_body,
    fetch_all,
    fetch_one,
    seed_chat_users,
    service_headers,
    submit_task,
    task_row,
    wait_run_terminal,
)
from tests.acceptance.capacity.stack import UNITS, CapacityStack, read_process_environ, run_async
from tests.capacity.driver import (
    ChatCredential,
    ControlProbeKind,
    FailureKind,
    LoadDriver,
    LoadReport,
    LoadScale,
    LoadTarget,
    ProbeOutcome,
    RunKind,
    TurnResult,
)
from tests.capacity.metrics import PostgresMetrics
from tests.capacity.profile import CapacityProfile, load_profile, profile_hash

pytestmark = [pytest.mark.integration, pytest.mark.capacity_load]

# --------------------------------------------------------------------------------------
# 锁定规格（与 profile / 真实配置一一对应，不在用例里另立默认值）
# --------------------------------------------------------------------------------------

WEBCHAT_ENTRY_PREFIX = "/api/v1/webchat/entries"
WEBCHAT_RUN_PREFIX = "/api/v1/webchat/runs"
CLIENT_CONFIG_PATH = "/api/v1/webchat/client-config"
RUNTIME_RUNS_PATH = "/v1/runs"
TERMINAL_RUN_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")
TERMINAL_TASK_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")

#: S-01 的锁定用户数：默认等于 profile.workload.users（正式验收）；`CAPACITY_LOAD_USERS`
#: 可把它降为受限验证规模（`RunKind.VERIFICATION` + 显式 scale，不冒充全量验收）。
LOAD_USERS = int(os.environ.get("CAPACITY_LOAD_USERS", "1000"))
#: 驱动控制探测的独立主体：探测 Run 不占负载用户的受理份额。
CONTROL_INDEX = LOAD_USERS
#: S-15 单 Pod 恢复的独立主体（与负载用户互不干扰）。
RECOVERY_INDEX = LOAD_USERS + 1
SEEDED_USERS = LOAD_USERS + 2

#: 物理 Key 的在飞上限（真实 `control.model_credential.max_inflight`）：模型容量边界。
MODEL_MAX_INFLIGHT = 16
#: 本 harness 声明的 Runtime/Worker 执行槽（启动固定，§3.7；用例从真实进程 env 复核）。
RUNTIME_EXECUTION_CONCURRENCY = 128
WORKER_EXECUTION_CONCURRENCY = 16
#: 真实执行槽总数（2 个 Runtime 副本 × 每进程槽位）：并发采样里 RUNNING 数的上界。
RUNTIME_SLOTS = RUNTIME_EXECUTION_CONCURRENCY * 2
#: 事件行的存储开销摊派（payload 之外的头部/索引/对齐；保守估计，报告口径）。
EVENT_ROW_OVERHEAD_BYTES = 256
#: PG 维护/迁移保留连接（§3.7「另留维护/迁移保留量」）。
PG_MAINTENANCE_RESERVE = 10
#: 每进程除连接池之外的 FD 开销（监听 socket、日志、库文件、Skill 包等）。
FD_SERVER_OVERHEAD = 256

#: 受控探针的负载行为：首块延迟 = profile.probe_latency_ms.p50，6 段 5ms 间隔的小增量。
MODEL_LATENCY_MS = 800
MODEL_CHUNK_INTERVAL_MS = 5
MODEL_DELTAS: tuple[str, ...] = ("容量", "一千", "用户", "增量", "窗口", "正文")
MODEL_BODY = "".join(MODEL_DELTAS)

#: S-15 的慢模型行为：重启/替换窗口内 Run 仍在执行（真实在飞窗口）。
SLOW_LATENCY_MS = 8000
SLOW_DELTAS: tuple[str, ...] = ("单", "Pod", "恢复", "正文")
SLOW_BODY = "".join(SLOW_DELTAS)

#: 租户级平台设置：准入/等待预算按全量负载显式声明（§3.7 唯一来源是 Console 设置）。
PLATFORM_SETTINGS: Mapping[str, Any] = {
    "model_pool": {"dispatch_epoch_sec": 3600},
    "capacity": {"queue_wait_timeout_sec": 900, "model_wait_timeout_sec": 600},
}

#: 栈级启动配置：加速节拍（断言「最终发生」）+ 显式执行槽（容量 harness 的声明配置）。
SERVICE_ENV: Mapping[str, Mapping[str, str]] = {
    **EXECUTION_CADENCE_ENV,
    "agent-runtime": {
        **EXECUTION_CADENCE_ENV["agent-runtime"],
        "RUNTIME_EXECUTION_CONCURRENCY": str(RUNTIME_EXECUTION_CONCURRENCY),
        # 模型池必须覆盖单进程执行并发（启动校验：每个在飞执行至多一条模型连接）。
        "MODEL_HTTP_MAX_CONNECTIONS": str(RUNTIME_EXECUTION_CONCURRENCY),
    },
    "agent-worker": {
        **EXECUTION_CADENCE_ENV["agent-worker"],
        "WORKER_EXECUTION_CONCURRENCY": str(WORKER_EXECUTION_CONCURRENCY),
    },
}
#: 生效的增量合并窗口（来自栈级 env，用例从真实进程 env 复核后使用）。
FLUSH_WINDOW_MS = int(SERVICE_ENV["agent-runtime"]["STREAM_DELTA_FLUSH_INTERVAL_MS"])


# --------------------------------------------------------------------------------------
# 真实公开事件流（经入口代理 → Gateway）：断流/游标/正文重建
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublicFrame:
    """一条公开 SSE 帧：事件类型 + 持久 seq + 归属 Run + 正文/阶段（白名单字段）。"""

    event: str
    seq: int
    run_id: str | None
    text: str
    stage: str | None


@dataclass(slots=True)
class PublicStreamRead:
    """一次真实公开流读取：帧序列 + 断开原因 + 终态（没有终态就是没有）。"""

    frames: list[PublicFrame] = field(default_factory=list)
    terminal: str | None = None
    disconnected: bool = False
    error: str = ""
    http_status: int | None = None

    @property
    def last_seq(self) -> int:
        return self.frames[-1].seq if self.frames else 0

    @property
    def delta_frames(self) -> tuple[PublicFrame, ...]:
        return tuple(frame for frame in self.frames if frame.event == "message.delta")


def apply_public_frames(frames: Sequence[PublicFrame]) -> str:
    """客户端语义重建正文：`message.snapshot` 整组替换、`message.delta` 追加。

    与浏览器/驱动同一口径（design frontend §3.2）：断流恢复跨代时必须按 snapshot 替换，不能把
    两代正文直接拼接。
    """
    text = ""
    for frame in frames:
        if frame.event == "message.snapshot":
            text = frame.text
        elif frame.event == "message.delta":
            text += frame.text
    return text


def _chat_client(base_url: str, session: ChatSession | ChatCredential) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        timeout=30.0,
        trust_env=False,
        cookies={"muad_chat_session": session.cookie},
        headers={"X-Chat-Context": session.context},
    )


def _public_frame(payload: Mapping[str, Any], fallback_type: str) -> PublicFrame:
    data = payload.get("data") or {}
    return PublicFrame(
        event=str(payload.get("type") or fallback_type),
        seq=int(payload["seq"]),
        run_id=None if payload.get("run_id") is None else str(payload["run_id"]),
        text=str(data.get("text") or ""),
        stage=None if data.get("stage") is None else str(data["stage"]),
    )


def read_public_stream(
    base_url: str,
    session: ChatSession | ChatCredential,
    run_id: str,
    *,
    after_seq: int = 0,
    until: Callable[[PublicStreamRead], bool] | None = None,
    sink: PublicStreamRead | None = None,
    timeout_sec: float = 120.0,
) -> PublicStreamRead:
    """真实公开 SSE 订阅（经入口代理）：按 `after_seq` 游标读取，连接断开显式记录。

    `sink` 给定时帧边到边追加（调用方可在另一线程里观察在飞订阅并提前关流）；默认读到终态。
    """
    read = sink if sink is not None else PublicStreamRead()
    stop = until if until is not None else (lambda item: item.terminal is not None)
    url = f"{WEBCHAT_RUN_PREFIX}/{run_id}/events"
    try:
        with _chat_client(base_url, session) as client:
            with client.stream("GET", url, params={"after_seq": after_seq}) as stream:
                read.http_status = stream.status_code
                if stream.status_code != 200:
                    read.error = f"HTTP {stream.status_code}"
                    return read
                event_type = ""
                for line in stream.iter_lines():
                    if line.startswith("event:"):
                        event_type = line.split(":", 1)[1].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = json.loads(line.split(":", 1)[1].strip())
                    frame = _public_frame(payload, event_type)
                    read.frames.append(frame)
                    if frame.event == "run.terminal":
                        read.terminal = str((payload.get("data") or {}).get("status") or "UNKNOWN")
                    if stop(read):
                        return read
    except httpx.HTTPError as exc:
        read.disconnected = True
        read.error = type(exc).__name__
        return read
    read.disconnected = True
    read.error = read.error or "stream_closed"
    return read


def create_conversation(base_url: str, session: ChatSession | ChatCredential, entry_id: Any) -> str:
    """真实新建会话（API-08，经入口代理）。"""
    with _chat_client(base_url, session) as client:
        response = client.post(
            f"{WEBCHAT_ENTRY_PREFIX}/{entry_id}/conversations",
            headers={"Idempotency-Key": f"capacity-conv-{uuid.uuid4().hex}"},
        )
    assert response.status_code == 201, f"新建会话失败：{response.status_code} {response.text}"
    return str(response.json()["data"]["conversation_id"])


def submit_message(
    base_url: str,
    session: ChatSession | ChatCredential,
    entry_id: Any,
    conversation_id: str,
    text: str,
) -> str:
    """真实受理（API-06，经入口代理）：202 是受理事实，回 run_id。"""
    message_id = f"capacity-msg-{uuid.uuid4().hex[:12]}"
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


def start_direct_run(
    runtime_url: str, *, tenant_id: str, agent_id: uuid.UUID, user_id: uuid.UUID, text: str
) -> str:
    """直接受理一个 Run（Runtime `POST /v1/runs`，真实 SSE 响应）并只等首个帧拿 run_id。

    该接口回的是执行事件流：读完首帧即返回 ⇒ 连接关闭（真实客户端断开），执行按 RULE-01 继续。
    **受理实例自己启动执行**（`_launch_execution` 本地领取），因此「杀掉该实例」对这条 Run 是
    确定的单 Pod 故障，不靠猜哪个副本在跑。
    """
    payload = build_run_payload(agent_id=agent_id, user_id=user_id, text=text)
    box: dict[str, str] = {}
    started = threading.Event()

    def reader() -> None:
        try:
            with httpx.Client(
                timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
                trust_env=False,
            ) as client:
                with client.stream(
                    "POST",
                    f"{runtime_url}{RUNTIME_RUNS_PATH}",
                    json=payload,
                    headers={"X-Tenant-Id": tenant_id},
                ) as stream:
                    if stream.status_code != 200:
                        return
                    for line in stream.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        event = json.loads(line.split(":", 1)[1].strip())
                        if event.get("run_id"):
                            box["run_id"] = str(event["run_id"])
                            started.set()
                            return
        except httpx.HTTPError:
            return

    thread = threading.Thread(target=reader, name="s15-direct-run", daemon=True)
    thread.start()
    assert started.wait(timeout=30.0), "直接受理未在超时内给出 run_id（真实受理失败）"
    return box["run_id"]


# --------------------------------------------------------------------------------------
# 真实进程观测：RSS / FD / 状态
# --------------------------------------------------------------------------------------


def process_rss_mb(pid: int) -> float:
    """真实进程 RSS（MB）：`ps` 的 KB 值换算，读不到即显式失败。"""
    completed = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True)
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value.isdigit():
        raise AssertionError(f"无法读取进程 {pid} 的 RSS：{completed.stderr.strip() or value!r}")
    return int(value) / 1024.0


def instance_rss_mb(instance: "ServiceInstance") -> float:
    """按实例测量 RSS：进程已不在时显式指名单元/槽位（不把死 pid 误报成读数失败）。"""
    if not instance.alive:
        raise AssertionError(
            f"{instance.unit}#{instance.index} 进程未运行，无法测量 RSS（预算检查要求整套栈在跑）"
        )
    return process_rss_mb(instance.pid)


def instance_fd_count(instance: "ServiceInstance") -> int:
    if not instance.alive:
        raise AssertionError(
            f"{instance.unit}#{instance.index} 进程未运行，无法测量 FD（预算检查要求整套栈在跑）"
        )
    return process_fd_count(instance.pid)


def process_fd_count(pid: int) -> int:
    """真实进程打开的文件描述符数（`lsof -p`，macOS/Linux 同口径）。"""
    completed = subprocess.run(["lsof", "-p", str(pid)], capture_output=True, text=True)
    if completed.returncode not in (0, 1):
        raise AssertionError(f"无法读取进程 {pid} 的 FD：{completed.stderr.strip()}")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return max(0, len(lines) - 1)


def run_status(database_url: str, tenant_id: str, run_id: str) -> str | None:
    row = fetch_one(
        database_url,
        "SELECT status FROM runtime.run_record WHERE tenant_id = $1 AND id = $2",
        tenant_id,
        uuid.UUID(run_id),
    )
    return None if row is None else str(row["status"])


def run_count_in_conversation(database_url: str, tenant_id: str, conversation_id: str) -> int:
    row = fetch_one(
        database_url,
        "SELECT count(*) AS runs FROM runtime.run_record"
        " WHERE tenant_id = $1 AND conversation_id = $2",
        tenant_id,
        uuid.UUID(conversation_id),
    )
    assert row is not None
    return int(row["runs"])


def run_user_id(database_url: str, tenant_id: str, run_id: str) -> str:
    row = fetch_one(
        database_url,
        "SELECT user_id FROM runtime.run_record WHERE tenant_id = $1 AND id = $2",
        tenant_id,
        uuid.UUID(run_id),
    )
    assert row is not None, f"Run 行不存在：{run_id}"
    return str(row["user_id"])


# --------------------------------------------------------------------------------------
# 模块栈：四单元独立栈 + 1002 份真实 CHAT 主体 + 真实 Worker 任务现场
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapacityModule:
    """本轮现场：真实四单元栈 + 种子 + 已交换的 CHAT 主体（负载/控制/恢复三类）。"""

    stack: CapacityStack
    seeded: SeededDelivery
    profile: CapacityProfile
    load_sessions: tuple[ChatCredential, ...]
    control_session: ChatCredential
    recovery_session: ChatCredential
    recovery_user_id: uuid.UUID
    recovery_token: str

    @property
    def load_user_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(uuid.UUID(session.user_id) for session in self.load_sessions)


def _disable_memory_writes(database_url: str, tenant_id: str, agent_id: uuid.UUID) -> None:
    """把记忆提炼关掉：容量验收测量交互推理，后台提炼会叠加额度/调用计数（显式、可复核的一行）。

    与 `execution_support.NO_MEMORY_AGENT` 同一语义（`memory_write` 是真实 runtime_config 键，
    Runtime 读它决定是否入队提炼 job）；不改变其它执行策略。
    """

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
    row = fetch_one(
        database_url,
        "SELECT runtime_config_json FROM control.agent_definition WHERE tenant_id = $1 AND id = $2",
        tenant_id,
        agent_id,
    )
    assert row is not None
    stored = row["runtime_config_json"]
    if isinstance(stored, str):
        stored = json.loads(stored)
    assert stored["memory_write"] is False, "记忆提炼开关必须真实落到 Agent 行上"


def _exchange_sessions(
    stack: CapacityStack, users: Sequence[Any]
) -> tuple[ChatCredential, ...]:
    """真实 Token 交换（经入口代理 → Console API-04）：每用户一份独立 CHAT Cookie 与上下文。"""
    credentials: list[ChatCredential] = []
    with httpx.Client(base_url=stack.proxy.url, timeout=30.0, trust_env=False) as client:
        for user in users:
            response = client.post("/api/v1/webchat/auth/exchange", json={"token": user.token})
            assert response.status_code == 200, (
                f"第 {user.index} 份 Token 交换失败：{response.status_code} {response.text}"
            )
            data = response.json()["data"]
            assert data["user_id"] == str(user.user_id), "交换必须导出来源 Token 的用户"
            cookie = response.cookies.get("muad_chat_session")
            assert cookie, "交换必须写独立 CHAT Cookie"
            credentials.append(
                ChatCredential(cookie=cookie, context=data["session_id"], user_id=data["user_id"])
            )
    assert len(credentials) == len(users)
    return tuple(credentials)


@pytest.fixture(scope="module")
def capacity_module(
    tmp_path_factory: pytest.TempPathFactory, capacity_profile_path: Path
) -> Iterator[CapacityModule]:
    """真实四单元独立栈（TASK-107）+ 1002 份主体 + 真实 Skill/入口种子。

    顺序是真实约束：Gateway 的入口快照在启动时拉取（其后按 `BOT_SNAPSHOT_POLL_SEC` 刷新），
    所以种子必须落在 `stack.start()` 之前；模型凭据的连接地址要等探针起来，启动后回填。
    库与 Redis 号位是本模块**自己的**（`create_datastore_handle`）：Worker claim 与投递选取不带
    租户谓词，与其它套件共用一份库会互相领走任务（表现为单跑绿、串跑红）。
    """
    root = tmp_path_factory.mktemp("capacity-acceptance")
    datastore = create_datastore_handle()
    profile = load_profile(capacity_profile_path)
    stack = build_stack(root=root, profile=profile, datastore=datastore, service_env=SERVICE_ENV)
    seeded = seed_delivery_control(
        datastore.database_url,
        stack.tenant_id,
        artifacts_root=stack.artifacts_root,
        platform_settings=PLATFORM_SETTINGS,
    )
    users = seed_chat_users(
        datastore.database_url,
        tenant_id=stack.tenant_id,
        agent_id=seeded.agent_id,
        count=SEEDED_USERS,
    )
    _disable_memory_writes(datastore.database_url, stack.tenant_id, seeded.agent_id)
    stack.start()
    bind_probe_credential(
        datastore.database_url, seeded.credential_id, f"{stack.model_probe.url}/v1"
    )
    sessions = _exchange_sessions(stack, users)
    try:
        yield CapacityModule(
            stack=stack,
            seeded=seeded,
            profile=profile,
            load_sessions=sessions[:LOAD_USERS],
            control_session=sessions[CONTROL_INDEX],
            recovery_session=sessions[RECOVERY_INDEX],
            recovery_user_id=users[RECOVERY_INDEX].user_id,
            recovery_token=users[RECOVERY_INDEX].token,
        )
    finally:
        stack.teardown()


@pytest.fixture(scope="module")
def load_run(capacity_module: CapacityModule) -> LoadReport:
    """S-01/S-14 共享的**一次**全量 ACCEPTANCE 负载：锁定 profile，不缩放、不改阈值。

    `RunKind.ACCEPTANCE` 由驱动强制：夹带 scale 或 profile 缺 SLO/资源声明都会在构造期失败
    （`require_formal_acceptance`），所以本 fixture 产出的报告不能被缩放后的运行冒充。
    """
    stack, seeded = capacity_module.stack, capacity_module.seeded
    profile_users = capacity_module.profile.workload.users
    scaled = LOAD_USERS != profile_users
    set_probe_behavior(
        stack.model_probe.url,
        {
            "latency_ms": MODEL_LATENCY_MS,
            "chunk_interval_ms": MODEL_CHUNK_INTERVAL_MS,
            "deltas": list(MODEL_DELTAS),
        },
    )
    driver = LoadDriver(
        target=LoadTarget(
            base_url=stack.proxy.url,
            entry_id=str(seeded.entry_id),
            sessions=capacity_module.load_sessions,
            control_session=capacity_module.control_session,
            runtime_url=stack.url_of("agent-runtime"),
            internal_token=stack.internal_token,
            model_probe_url=stack.model_probe.url,
            request_timeout_sec=30.0,
            stream_timeout_sec=180.0,
            sample_interval_sec=1.0,
            control_interval_sec=5.0,
            control_start_sec=5.0,
            control_rounds=2,
            control_terminal_wait_sec=30.0,
            user_deadline_sec=900.0,
        ),
        profile=capacity_module.profile,
        run_kind=RunKind.VERIFICATION if scaled else RunKind.ACCEPTANCE,
        scale=(
            LoadScale(
                users=LOAD_USERS,
                ramp_sec=capacity_module.profile.workload.ramp_sec,
                steady_sec=capacity_module.profile.workload.steady_sec,
                # 降规模验证按 S-01 的契约口径「每用户一个 Run」：保留 8 轮会让下一轮在 3s
                # 思考时间后撞上仍在执行的上一轮（真实 RUN_BUSY 409），把容量结论变成
                # 轮次碰撞的测量。
                turns_per_user=1,
            )
            if scaled
            else None
        ),
        metrics=PostgresMetrics(
            stack.database_url, tenant_id=stack.tenant_id, artifacts_root=stack.artifacts_root
        ),
        unit_targets={unit: stack.url_of(unit) for unit in UNITS},
        control_model_id=str(seeded.model_id),
        control_credential_id=str(seeded.credential_id),
    )
    return driver.run()


def sampled_turns(report: LoadReport, count: int = 6) -> tuple[TurnResult, ...]:
    """从真实负载里等距抽样若干轮（含最后一轮），用于逐 Run 的持久事实核对。"""
    turns = report.completed()
    assert turns, "负载没有任何终态轮次可抽样"
    step = max(1, len(turns) // count)
    picked = list(turns[::step])[:count]
    if picked[-1] is not turns[-1]:
        picked[-1] = turns[-1]
    return tuple(picked)


def _session_for_user(capacity_module: CapacityModule, user_id: str) -> ChatCredential:
    for session in capacity_module.load_sessions:
        if session.user_id == user_id:
            return session
    raise AssertionError(f"用户 {user_id} 不在负载主体里")


# --------------------------------------------------------------------------------------
# S-01：1000 用户容量与真实受理/等待/执行/进展/隔离
# --------------------------------------------------------------------------------------


def test_s01_thousand_users_admitted_visible_and_bounded(
    capacity_module: CapacityModule, load_run: LoadReport
) -> None:
    """[S-01] 配置接纳 1000 不同用户各一个 Run；持久受理、可见等待/执行、最终进展、无跨用户事件；
    执行不超模型/工具容量，排队不冒充同时推理。

    断言位置：
    ① 报告锁定本次 profile（hash 一致、无缩放）；锁定窗口内尝试的每一轮都拿到真实 202，
       且 1000 个不同用户各至少一个 Run（S-01 契约口径；profile 的每用户轮数是窗口参数，
       不要求慢机器在窗口内跑满 8000 轮）；
    ② PG 权威：1000 个**不同** user_id 均有 Run（每用户 1..turns_per_user 个），提交行与轮次一致；
    ③ 可见等待/执行：并发采样同时观测到等待与执行；`run.stage` 持久投影里有排队与执行阶段，
       且阶段词条不出现在助手正文里（状态不作为模型正文）；
    ④ 最终进展：已受理轮次全部 COMPLETED；错误账目只含唯一一次故意的边界拒绝；
    ⑤ 无跨用户事件：抽样 Run 的公开流帧都带自身 run_id、事件都在自身会话、正文与探针一致；
       非属主主体读不到该 Run 的事件（跨用户事件面）；
    ⑥ 容量边界：真实同时推理 ≤ 凭据在飞上限（从真实 PG 行复核）；任一采样点 RUNNING ≤ 执行槽。
    """
    report = load_run
    stack = capacity_module.stack
    profile = capacity_module.profile
    database_url = stack.database_url
    tenant_id = stack.tenant_id

    # ① 锁定 profile + 全量受理
    scaled = LOAD_USERS != profile.workload.users
    assert report.run_kind is (RunKind.VERIFICATION if scaled else RunKind.ACCEPTANCE), (
        "缩放验证必须是 VERIFICATION；全量验收必须是 ACCEPTANCE"
    )
    if scaled:
        assert report.scale is not None and report.scale.users == LOAD_USERS, (
            "受限验证必须显式登记缩放规模"
        )
    else:
        assert report.scale is None, "ACCEPTANCE 运行不得夹带缩放（驱动已强制，此处再核报告）"
    assert report.locked_profile_hash == profile_hash(profile), "报告必须锁定本次 profile 内容 hash"
    assert report.effective_users == LOAD_USERS
    expected_turns = 1 if scaled else profile.workload.turns_per_user
    assert report.effective_turns == expected_turns
    assert report.attempts() >= LOAD_USERS, "锁定窗口内至少覆盖 1000 轮（契约下界）"
    rejected = sorted(
        {
            (turn.http_status, (turn.detail or "")[:120])
            for turn in report.turns
            if turn.failure is not None and turn.failure.value == "ADMISSION_REJECTED"
        }
    )[:3]
    assert len({turn.user_index for turn in report.admitted()}) == LOAD_USERS, (
        f"{LOAD_USERS} 个不同用户都必须拿到真实受理（S-01：每用户各一个 Run）；"
        f"实际 {len({turn.user_index for turn in report.admitted()})}，"
        f"失败账目 {dict(report.failure_counts())}；受理拒绝状态 {rejected}"
    )
    assert len(report.admitted()) == report.attempts(), "每一轮都必须拿到真实 202 受理"
    assert len({turn.run_id for turn in report.admitted()}) == report.attempts(), (
        "每个 Run 身份唯一（并发/重投不得产生同一 run_id 的两个轮次）"
    )

    # ② 1000 个不同用户各一个 Run（PG 权威事实）
    load_user_ids = capacity_module.load_user_ids
    per_user = fetch_all(
        database_url,
        "SELECT user_id, count(*) AS runs FROM runtime.run_record"
        " WHERE tenant_id = $1 AND user_id = ANY($2::uuid[]) GROUP BY user_id",
        tenant_id,
        load_user_ids,
    )
    assert len(per_user) == LOAD_USERS, f"必须是 1000 个不同用户：实际 {len(per_user)}"
    assert all(int(row["runs"]) >= 1 for row in per_user), "每个用户至少一个真实 Run"
    assert max(int(row["runs"]) for row in per_user) <= profile.workload.turns_per_user, (
        "每用户 Run 数不得超过 profile 的每用户轮数（同用户并发多 Run 会撞每用户受理上限）"
    )
    submissions = fetch_one(
        database_url,
        "SELECT count(*) AS total FROM runtime.run_submission s"
        " JOIN runtime.run_record r ON r.id = s.run_id"
        " WHERE r.tenant_id = $1 AND r.user_id = ANY($2::uuid[])",
        tenant_id,
        load_user_ids,
    )
    assert submissions is not None and int(submissions["total"]) == report.attempts(), (
        "持久受理：提交行数与真实轮次一致"
    )

    # ③ 可见等待/执行（持久阶段投影 + 并发采样）
    assert any(sample.waiting_runs > 0 for sample in report.samples), (
        "满载时必须观测到持久等待中的 Run（排队是持久投影，不是本地假象）"
    )
    assert any(sample.running_runs > 0 for sample in report.samples), "必须观测到真实执行中的 Run"
    assert any(
        sample.admitted_runs >= 1 and sample.active_streams >= 1 for sample in report.samples
    ), "采样必须同时观测到持久受理与在飞 SSE 订阅"
    stages = fetch_all(
        database_url,
        "SELECT payload_json::text AS payload FROM runtime.canonical_event"
        " WHERE tenant_id = $1 AND stream_type = 'run.stage'",
        tenant_id,
    )
    stage_words = {str(json.loads(str(row["payload"]))["stage"]) for row in stages if row["payload"]}
    assert {"WAITING_RUNTIME", "RUNNING"} & stage_words, f"阶段投影必须含真实等待/执行：{stage_words}"
    assert stage_words <= {
        "WAITING_RUNTIME",
        "WAITING_MODEL",
        "RUNNING",
        "WAITING_TOOL",
        "WAITING_INPUT",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }, f"阶段词表封闭（不把内部状态透传成公开阶段）：{stage_words}"
    body_rows = fetch_all(
        database_url,
        "SELECT payload_json::text AS payload FROM runtime.canonical_event"
        " WHERE tenant_id = $1 AND stream_type = 'message.delta' LIMIT 200",
        tenant_id,
    )
    for row in body_rows:
        text = str(json.loads(str(row["payload"]))["delta"])
        assert "WAITING" not in text and "正在等待模型资源" not in text, "阶段词条不得混进助手正文"

    # ④ 最终进展 + 错误账目（不做选择性排除）
    assert report.failure_counts() == {FailureKind.ADMISSION_REJECTED.value: 1}, (
        "错误账目必须完整：已受理轮次全成功 + 唯一一次故意边界拒绝"
    )
    assert len(report.completed()) == report.attempts(), "每一轮都必须走到真实终态"
    assert {turn.terminal_status for turn in report.completed()} == {"COMPLETED"}, (
        "容量验收路径不得出现失败/取消终态（有就是真实容量问题，不能排除掉）"
    )
    completed_rows = fetch_one(
        database_url,
        "SELECT count(*) AS total FROM runtime.run_record"
        " WHERE tenant_id = $1 AND user_id = ANY($2::uuid[]) AND status = 'COMPLETED'",
        tenant_id,
        load_user_ids,
    )
    assert completed_rows is not None and int(completed_rows["total"]) == report.attempts(), (
        "PG 里 COMPLETED 行数必须等于轮次（终态是持久事实）"
    )

    # ⑤ 无跨用户事件（抽样逐 Run 核对：公开流 + 会话归属 + 事件归属 + 非属主不可读）
    for turn in sampled_turns(report):
        assert turn.run_id is not None
        owner = run_user_id(database_url, tenant_id, turn.run_id)
        session = _session_for_user(capacity_module, owner)
        replay = read_public_stream(stack.proxy.url, session, turn.run_id)
        assert replay.terminal == "COMPLETED", replay.error or "抽样 Run 未读到终态"
        assert all(frame.run_id == turn.run_id for frame in replay.frames), (
            "公开流帧必须只带本 Run 身份（无跨用户事件）"
        )
        assert apply_public_frames(replay.frames) == MODEL_BODY, (
            "抽样 Run 的公开正文必须与真实模型探针下发的正文逐字一致"
        )
        ownership = fetch_one(
            database_url,
            "SELECT r.user_id AS run_user, c.user_id AS conversation_user,"
            " (SELECT count(*) FROM runtime.canonical_event e"
            "  WHERE e.run_id = r.id AND e.conversation_id <> r.conversation_id) AS foreign_events"
            " FROM runtime.run_record r JOIN runtime.conversation c ON c.id = r.conversation_id"
            " WHERE r.tenant_id = $1 AND r.id = $2",
            tenant_id,
            uuid.UUID(turn.run_id),
        )
        assert ownership is not None
        assert ownership["run_user"] == ownership["conversation_user"], "Run 与会话必须同属一个用户"
        assert int(ownership["foreign_events"]) == 0, "事件不得写进别的会话"
        foreign = read_public_stream(
            stack.proxy.url,
            capacity_module.recovery_session,
            turn.run_id,
            until=lambda read: bool(read.frames),
        )
        assert not foreign.frames, "非属主主体不得读到该 Run 的公开事件（跨用户事件面）"

    # ⑥ 执行不超模型/工具容量；排队不冒充同时推理
    credential = fetch_one(
        database_url,
        "SELECT max_inflight FROM control.model_credential WHERE tenant_id = $1 AND id = $2",
        tenant_id,
        capacity_module.seeded.credential_id,
    )
    assert credential is not None and int(credential["max_inflight"]) == MODEL_MAX_INFLIGHT, (
        "模型容量边界必须来自真实凭据行（与用例常量一致）"
    )
    probe = report.model_probe
    assert probe is not None
    assert probe.calls_after - probe.calls_before >= report.attempts(), "每一轮都真实到达模型探针"
    assert probe.max_concurrency <= MODEL_MAX_INFLIGHT, (
        f"真实同时推理 {probe.max_concurrency} 超过凭据在飞上限 {MODEL_MAX_INFLIGHT}"
    )
    assert probe.max_concurrency >= 1, "必须观测到真实在飞推理（不是空跑）"
    assert probe.faults == {}, "未注入故障时探针故障计数为空"
    runtime_env = read_process_environ(stack.instance("agent-runtime", 0).pid)
    assert runtime_env["RUNTIME_EXECUTION_CONCURRENCY"] == str(RUNTIME_EXECUTION_CONCURRENCY), (
        "执行槽必须来自真实进程 env（容量声明与实际一致）"
    )
    assert max(sample.running_runs for sample in report.samples) <= RUNTIME_SLOTS, (
        f"任一采样点的执行中 Run 数不得超过执行槽总数 {RUNTIME_SLOTS}"
    )
    assert max(sample.waiting_runs for sample in report.samples) <= LOAD_USERS, (
        "等待中的 Run 数不得超过负载用户数"
    )


# --------------------------------------------------------------------------------------
# S-14：增量窗口合并、断流恢复与满载下的控制面/预算
# --------------------------------------------------------------------------------------


def test_s14_incremental_merge_stream_break_and_loaded_control(
    capacity_module: CapacityModule, load_run: LoadReport
) -> None:
    """[S-14] 按配置窗口合并小增量，断流恢复正文一致；事件写入率/PG 连接/内存不超报告预算，
    取消/确认/续租在满载时仍可服务，终态前全部正文已持久化。

    断言位置：
    ① 按配置窗口合并：抽样 Run 的持久 `message.delta` 行数 < 探针下发的段数，且落在
       「配置窗口 × 增量到达跨度」的界内；合并文本与探针正文逐字一致；窗口值取自真实进程 env；
    ② 断流恢复：真实公开流读一段后**断开连接**，按最后已应用 seq 重连，两段帧拼出的正文
       （snapshot 替换语义）与持久正文逐字一致，seq 严格递增且不重复；
    ③ 预算：事件写入率 ≤ profile.storage_budget.event_bytes_per_day 折算值；PG 连接数 ≤
       resources.pg_connection_budget；四单元进程 RSS ≤ 各自 memory_limit_mb；
    ④ 满载控制面：取消/确认/续租三类探测都在负载窗口内、结局显式分类、取消在 PG 上真实生效、
       续租不缩短租约且结算落 SETTLED；
    ⑤ 终态前全部正文已持久化：每个抽样 Run 的最后一条正文事件 seq < 终态事件 seq。
    """
    report = load_run
    stack = capacity_module.stack
    database_url = stack.database_url
    tenant_id = stack.tenant_id
    profile = capacity_module.profile
    resources = profile.resources
    assert resources is not None, "正式验收 profile 必须有资源声明"
    sample = sampled_turns(report, count=8)

    # ① 按配置窗口合并小增量
    runtime_env = read_process_environ(stack.instance("agent-runtime", 0).pid)
    window_ms = int(runtime_env["STREAM_DELTA_FLUSH_INTERVAL_MS"])
    assert window_ms == FLUSH_WINDOW_MS, "合并窗口必须来自真实进程 env（与栈级声明一致）"
    spread_ms = MODEL_CHUNK_INTERVAL_MS * (len(MODEL_DELTAS) - 1)
    merge_bound = spread_ms // window_ms + 2  # 每次 flush 需窗口到界；末次在回合边界强制 flush
    for turn in sample:
        rows = fetch_all(
            database_url,
            "SELECT payload_json::text AS payload FROM runtime.canonical_event"
            " WHERE tenant_id = $1 AND run_id = $2 AND stream_type = 'message.delta' ORDER BY seq",
            tenant_id,
            uuid.UUID(str(turn.run_id)),
        )
        assert rows, f"Run {turn.run_id} 没有任何持久正文增量"
        assert len(rows) < len(MODEL_DELTAS), (
            f"小增量必须按配置窗口合并：持久 {len(rows)} 行 vs 探针下发 {len(MODEL_DELTAS)} 段"
        )
        assert len(rows) <= merge_bound, (
            f"持久增量行数 {len(rows)} 超出配置窗口（{window_ms}ms）允许的上界 {merge_bound}"
        )
        text = "".join(str(json.loads(str(row["payload"]))["delta"]) for row in rows)
        assert text == MODEL_BODY, f"合并后的正文必须逐字等于探针正文：{text!r}"

    # ② 断流恢复正文一致（真实公开流：读一段 → 关连接 → 按游标重连）
    session = capacity_module.recovery_session
    conversation_id = create_conversation(
        stack.proxy.url, session, capacity_module.seeded.entry_id
    )
    run_id = submit_message(
        stack.proxy.url, session, capacity_module.seeded.entry_id, conversation_id, "S-14 断流恢复"
    )
    first = PublicStreamRead()
    read_public_stream(
        stack.proxy.url,
        session,
        run_id,
        after_seq=0,
        sink=first,
        until=lambda read: len(read.delta_frames) >= 1,
    )
    assert first.delta_frames, "第一段订阅必须真的收到正文增量（否则断流没有意义）"
    cursor = first.last_seq
    assert cursor >= 1
    second = read_public_stream(stack.proxy.url, session, run_id, after_seq=cursor)
    assert second.terminal == "COMPLETED", second.error or "重连未在超时内读到终态"
    seqs = [frame.seq for frame in second.frames]
    assert seqs == sorted(set(seqs)) and all(seq > cursor for seq in seqs), (
        "重连必须按游标严格向前、无重复（无缺口、无重复渲染）"
    )
    assert apply_public_frames((*first.frames, *second.frames)) == MODEL_BODY, (
        f"断流恢复后的正文必须一致：{apply_public_frames((*first.frames, *second.frames))!r}"
    )
    persisted = fetch_all(
        database_url,
        "SELECT payload_json::text AS payload FROM runtime.canonical_event"
        " WHERE tenant_id = $1 AND run_id = $2 AND stream_type = 'message.delta' ORDER BY seq",
        tenant_id,
        uuid.UUID(run_id),
    )
    assert "".join(str(json.loads(str(row["payload"]))["delta"]) for row in persisted) == MODEL_BODY, (
        "持久正文必须与恢复后的公开正文一致"
    )

    # ③ 预算：事件写入率 / PG 连接 / 内存
    event_row = fetch_one(
        database_url,
        "SELECT count(*) AS events, coalesce(sum(octet_length(payload_json::text)), 0) AS payload_bytes"
        " FROM runtime.canonical_event WHERE tenant_id = $1 AND run_id IN"
        " (SELECT id FROM runtime.run_record WHERE tenant_id = $1 AND user_id = ANY($2::uuid[]))",
        tenant_id,
        capacity_module.load_user_ids,
    )
    assert event_row is not None
    events = int(event_row["events"])
    total_bytes = int(event_row["payload_bytes"]) + EVENT_ROW_OVERHEAD_BYTES * events
    wall_seconds = max(report.wall_seconds(), 1.0)
    measured_rate = total_bytes / wall_seconds
    budget_rate = resources.storage_budget.event_bytes_per_day / 86400.0
    assert events > 0 and total_bytes > 0, "负载必须产生真实事件写入"
    assert measured_rate <= budget_rate, (
        f"事件写入率 {measured_rate:.0f} B/s 超过报告预算 {budget_rate:.0f} B/s"
        f"（{events} 事件 / {total_bytes} 字节 / {wall_seconds:.0f}s）"
    )
    assert report.postgres_after.connections_total <= resources.pg_connection_budget, (
        f"PG 连接 {report.postgres_after.connections_total} 超过预算 {resources.pg_connection_budget}"
    )
    for instance in stack.all_instances():
        limit_mb = resources.hardware[instance.unit].memory_limit_mb
        rss_mb = instance_rss_mb(instance)
        assert rss_mb <= limit_mb, (
            f"{instance.unit}#{instance.index} RSS {rss_mb:.0f}MB 超过内存预算 {limit_mb}MB"
        )

    # ④ 取消/确认/续租在满载时仍可服务
    probes = report.control_probes
    kinds = {probe.kind for probe in probes}
    assert kinds == {ControlProbeKind.CANCEL, ControlProbeKind.CONFIRM, ControlProbeKind.RENEW}, (
        "三类控制探测都必须真实执行"
    )
    assert all(probe.outcome in {ProbeOutcome.OK, ProbeOutcome.REJECTED} for probe in probes), (
        "控制探测结局必须显式分类（超时/传输错误会显式计为失败，不冒充成功）"
    )
    assert all(probe.latency_ms < 15_000 for probe in probes), "控制面在满载下仍被服务（不排长流队尾）"
    assert all(
        report.first_start_sec <= probe.started_at <= report.finished_at for probe in probes
    ), "控制探测必须发生在负载窗口内（满载下仍可服务）"
    cancels = [probe for probe in probes if probe.kind is ControlProbeKind.CANCEL]
    assert cancels and all(probe.outcome is ProbeOutcome.OK for probe in cancels)
    assert any(
        probe.run_state is not None
        and (probe.run_state.cancel_requested or probe.run_state.status == "CANCELLED")
        for probe in cancels
    ), "取消必须在 PG 行上真实生效"
    renews = [probe for probe in probes if probe.kind is ControlProbeKind.RENEW]
    assert renews and all(probe.outcome is ProbeOutcome.OK for probe in renews)
    for probe in renews:
        lease = probe.lease
        assert lease is not None
        assert lease.lease_until_after_epoch >= lease.lease_until_before_epoch, "续租不得缩短租约"
        assert lease.settled_state == "SETTLED", "结算落真实终态"

    # ⑤ 终态前全部正文已持久化
    for turn in sample:
        rows = fetch_all(
            database_url,
            "SELECT seq, stream_type FROM runtime.canonical_event"
            " WHERE tenant_id = $1 AND run_id = $2 ORDER BY seq",
            tenant_id,
            uuid.UUID(str(turn.run_id)),
        )
        body_seqs = [int(row["seq"]) for row in rows if row["stream_type"] == "message.delta"]
        # 持久层的终态流类型是 Runtime 的 `run.completed`/`run.failed`；`run.terminal` /
        # `run.error` 只是 Gateway 对外投影的公开事件名（`webchat_streams`）。
        terminals = [
            int(row["seq"]) for row in rows if row["stream_type"] in ("run.completed", "run.failed")
        ]
        assert body_seqs and terminals, "抽样 Run 必须有正文与终态事件"
        assert max(body_seqs) < min(terminals), (
            "终态事件必须排在全部正文之后（终态前全部正文已持久化）"
        )


# --------------------------------------------------------------------------------------
# S-15：单 Pod 重启、副本替换、租约保留与总预算可核对
# --------------------------------------------------------------------------------------


def test_s15_single_pod_restart_replica_replacement_and_lease_hold(
    capacity_module: CapacityModule,
) -> None:
    """[S-15] 单 Console/Gateway 重启与 Runtime/Worker 替换：单 Pod 短暂不可用按事实记录；
    恢复后重连带抖动，已受理 Run 不重建、无双执行，租约资源无误释放。

    断言位置：
    ① Console 原地重启：停机窗口内经入口代理的真实请求得到 502 且代理上游失败计数 +1（事实记录）；
       重启是同地址新进程（pid 变、端口不变、同库/Redis/卷），恢复后真实 Console 能力可用；
    ② Gateway 原地重启：受理一个 Run（慢模型 ⇒ 执行跨越停机窗口），停机窗口内真实聊天请求 502、
       代理计数 +1；执行与传输解耦（停机期间 Run 仍落终态，RULE-01）；
    ③ 重连带抖动：API-19 真实投影给 `reconnect_base_ms ≤ reconnect_cap_ms` 与有界次数（前端在该
       区间内按 full-jitter 退避，浏览器侧由 `capacity-recovery.spec.ts` 实测间隔）；
       按最后已应用 seq 重连，正文一致、无重复；
    ④ 不重建/无双执行：会话仍恰一个 Run、run_id 不变、模型探针调用数只增 1；
    ⑤ 租约资源无误释放：SIGKILL 执行中 Runtime 后，被杀尝试的租约行仍在、状态为 ACTIVE/UNKNOWN、
       容量 inflight 未被错误清零；租约过期后由 Reaper 落诚实终态 FAILED/RUN_ABANDONED；
    ⑥ Worker 替换：SIGKILL 整组后起新实例（新端口、同库/卷/Redis），经**新实例**提交的真实 Task
       被领取并**恰好执行一次**（attempt=1、无重复结果）。
    """
    stack, seeded = capacity_module.stack, capacity_module.seeded
    database_url, tenant_id = stack.database_url, stack.tenant_id
    probe = stack.model_probe
    session = capacity_module.recovery_session
    http = httpx.Client(timeout=60.0, trust_env=False)
    try:
        # ① Console 原地重启
        console = stack.instance("console-platform")
        console_pid, console_port = console.pid, console.port
        console_failures_before = int(stack.proxy.stats()["upstream_failures"]["console-platform"])
        stack.stop_instance(console)
        assert console.alive is False
        down = http.post(
            f"{stack.proxy.url}/api/v1/webchat/auth/exchange", json={"token": "single-pod-outage"}
        )
        assert down.status_code == 502, f"停机窗口必须按事实失败：{down.status_code} {down.text}"
        assert down.json()["unit"] == "console-platform"
        console_failures_after = int(stack.proxy.stats()["upstream_failures"]["console-platform"])
        assert console_failures_after == console_failures_before + 1, "单 Pod 不可用必须被真实记录"
        console2 = stack.restart_instance("console-platform")
        assert console2.pid != console_pid, "重启必须是新进程"
        assert console2.port == console_port, "单 Pod 重启必须保持同一地址（Service 不变）"
        console_env = read_process_environ(console2.pid)
        assert console_env["DATABASE_URL"] == database_url
        assert console_env["REDIS_URL"] == stack.redis_url
        assert console_env["ARTIFACT_ROOT"] == str(stack.artifacts_root)
        revived = exchange_chat_session(
            stack.proxy.url, capacity_module.recovery_token, capacity_module.recovery_user_id
        )
        assert revived.user_id == capacity_module.recovery_user_id, "恢复后真实 Console 能力可用"

        # ② Gateway 原地重启 + 执行与传输解耦
        set_probe_behavior(
            probe.url,
            {"latency_ms": SLOW_LATENCY_MS, "chunk_interval_ms": 0, "deltas": list(SLOW_DELTAS)},
        )
        calls_before = int(probe_state(probe.url)["call_count"])
        conversation_id = create_conversation(stack.proxy.url, session, seeded.entry_id)
        run_id = submit_message(
            stack.proxy.url, session, seeded.entry_id, conversation_id, "S-15 单 Pod 恢复"
        )
        first = PublicStreamRead()
        reader = threading.Thread(
            target=lambda: read_public_stream(
                stack.proxy.url,
                session,
                run_id,
                after_seq=0,
                sink=first,
                until=lambda read: bool(read.frames),
            ),
            name="s15-first-subscription",
            daemon=True,
        )
        reader.start()
        wait_until(
            lambda: bool(first.frames), what="首个公开 SSE 订阅未在超时内收到任何事件", timeout=60.0
        )
        cursor = first.last_seq
        gateway = stack.instance("im-gateway")
        gateway_pid, gateway_port = gateway.pid, gateway.port
        gateway_failures_before = int(stack.proxy.stats()["upstream_failures"]["im-gateway"])
        stack.kill_instance(gateway)
        assert gateway.alive is False
        down_chat = http.post(
            f"{stack.proxy.url}{WEBCHAT_ENTRY_PREFIX}/{seeded.entry_id}/messages",
            json={
                "conversation_id": conversation_id,
                "message_id": f"capacity-outage-{uuid.uuid4().hex[:8]}",
                "text": "停机窗口内提交",
                "upload_ids": [],
            },
            headers={"Idempotency-Key": f"outage:{uuid.uuid4().hex}"},
        )
        assert down_chat.status_code == 502, f"停机窗口必须按事实失败：{down_chat.text}"
        gateway_failures_after = int(stack.proxy.stats()["upstream_failures"]["im-gateway"])
        assert gateway_failures_after == gateway_failures_before + 1, "单 Pod 不可用必须被真实记录"
        wait_until(
            lambda: run_status(database_url, tenant_id, run_id) in TERMINAL_RUN_STATUSES,
            what="停机窗口内 Run 未继续执行到终态（执行必须与传输解耦）",
            timeout=120.0,
        )
        outage_terminal = fetch_one(
            database_url,
            "SELECT status, error_code, error_message FROM runtime.run_record"
            " WHERE tenant_id = $1 AND id = $2",
            tenant_id,
            uuid.UUID(run_id),
        )
        assert outage_terminal is not None and outage_terminal["status"] == "COMPLETED", (
            f"Gateway 停机不得改变执行结局（RULE-01）：{dict(outage_terminal or {})}"
        )
        gateway2 = stack.restart_instance("im-gateway")
        assert gateway2.pid != gateway_pid and gateway2.port == gateway_port, (
            "Gateway 单 Pod 重启必须同地址、新进程"
        )

        # ③ 重连带抖动（真实配置投影）+ 按游标重连的正文一致性
        transport = _client_transport(stack.proxy.url, session)
        base_ms, cap_ms, max_attempts = (
            int(transport["reconnect_base_ms"]),
            int(transport["reconnect_cap_ms"]),
            int(transport["reconnect_max_attempts"]),
        )
        assert 0 < base_ms <= cap_ms, f"重连退避区间非法：{base_ms}..{cap_ms}"
        assert max_attempts >= 1, "重连次数必须有界且为正"
        second = read_public_stream(stack.proxy.url, session, run_id, after_seq=cursor)
        assert second.terminal == "COMPLETED", second.error or "重连未读到终态"
        assert all(frame.run_id == run_id for frame in second.frames), "重连帧必须只带本 Run 身份"
        seqs = [frame.seq for frame in second.frames]
        assert seqs == sorted(set(seqs)) and all(seq > cursor for seq in seqs), (
            "重连必须严格向前、无重复"
        )
        assert apply_public_frames((*first.frames, *second.frames)) == SLOW_BODY, (
            "单 Pod 重启后的恢复正文必须逐字一致"
        )

        # ④ 不重建、无双执行
        assert run_count_in_conversation(database_url, tenant_id, conversation_id) == 1, (
            "重启恢复不得重建 Run（会话仍恰一个 Run）"
        )
        assert int(probe_state(probe.url)["call_count"]) == calls_before + 1, (
            "重启恢复不得重建执行（模型只被真实调用一次）"
        )

        # ⑤ 租约资源无误释放（执行中的 Runtime 实例被 SIGKILL）
        calls_before_kill = int(probe_state(probe.url)["call_count"])
        direct = start_direct_run(
            stack.url_of("agent-runtime", 1),
            tenant_id=tenant_id,
            agent_id=seeded.agent_id,
            user_id=capacity_module.recovery_user_id,
            text="S-15 Runtime 替换",
        )
        wait_until(
            lambda: int(probe_state(probe.url)["call_count"]) >= calls_before_kill + 1,
            what="被杀 Runtime 的模型调用未真实到达探针",
            timeout=60.0,
        )
        direct_conversation = fetch_one(
            database_url,
            "SELECT conversation_id FROM runtime.run_record WHERE tenant_id = $1 AND id = $2",
            tenant_id,
            uuid.UUID(direct),
        )
        assert direct_conversation is not None
        leases = fetch_all(
            database_url,
            "SELECT state FROM runtime.model_call_lease WHERE tenant_id = $1 AND run_id = $2",
            tenant_id,
            uuid.UUID(direct),
        )
        assert leases, "执行中的 Run 必须有真实模型调用租约"
        runtime = stack.instance("agent-runtime", 1)
        runtime_pid, runtime_port = runtime.pid, runtime.port
        stack.kill_instance(runtime)
        assert runtime.alive is False
        held = fetch_all(
            database_url,
            "SELECT state FROM runtime.model_call_lease WHERE tenant_id = $1 AND run_id = $2",
            tenant_id,
            uuid.UUID(direct),
        )
        assert held, "被杀进程的在飞租约不得被删除（租约资源不误释放）"
        assert all(str(row["state"]) in ("ACTIVE", "UNKNOWN") for row in held), (
            f"在飞租约必须保持占用语义：{[str(row['state']) for row in held]}"
        )
        inflight = fetch_one(
            database_url,
            "SELECT coalesce(sum(inflight), 0) AS inflight FROM runtime.model_capacity"
            " WHERE tenant_id = $1",
            tenant_id,
        )
        assert inflight is not None and int(inflight["inflight"]) >= 1, (
            "被杀尝试的容量占用不得被静默清零（远端是否停止未知）"
        )
        runtime2 = stack.replace_instance("agent-runtime", 1)
        assert runtime2.pid != runtime_pid and runtime2.port != runtime_port, (
            "Runtime 替换必须是新进程/新端口（同一库/卷/Redis）"
        )
        runtime_env = read_process_environ(runtime2.pid)
        assert runtime_env["DATABASE_URL"] == database_url
        assert runtime_env["REDIS_URL"] == stack.redis_url
        assert runtime_env["ARTIFACT_ROOT"] == str(stack.artifacts_root)
        terminal = wait_run_terminal(database_url, tenant_id, uuid.UUID(direct), timeout_sec=120.0)
        assert terminal["status"] == "FAILED" and terminal["error_code"] == "RUN_ABANDONED", (
            "执行中的 Run 在 Pod 崩溃后必须落诚实终态（Reaper 的 RUN_ABANDONED，不静默、不重跑）"
        )
        assert (
            run_count_in_conversation(database_url, tenant_id, str(direct_conversation["conversation_id"]))
            == 1
        ), "崩溃回收不得重建 Run"
        assert int(probe_state(probe.url)["call_count"]) == calls_before_kill + 1, (
            "崩溃回收不得重复调用模型（无双执行）"
        )

        # ⑥ Worker 替换 + 真实 Task 恰好执行一次
        worker = stack.instance("agent-worker", 1)
        worker_pid, worker_port = worker.pid, worker.port
        stack.kill_instance(worker)
        worker2 = stack.replace_instance("agent-worker", 1)
        assert worker2.pid != worker_pid and worker2.port != worker_port, (
            "Worker 替换必须是新进程/新端口"
        )
        worker_env = read_process_environ(worker2.pid)
        assert worker_env["DATABASE_URL"] == database_url
        assert worker_env["REDIS_URL"] == stack.redis_url
        assert worker_env["ARTIFACT_ROOT"] == str(stack.artifacts_root)
        assert seeded.skill_id is not None and seeded.storage_key and seeded.checksum, (
            "Worker 任务现场需要真实 Skill 包（种子必须带 artifacts_root）"
        )
        spec = resolve_task_spec(
            stack.url_of("console-platform"),
            stack.internal_token,
            tenant_id=tenant_id,
            agent_id=seeded.agent_id,
            actor_user_id=seeded.user_id,
            channel="WEBCHAT",
        )
        idempotency_key = f"capacity-s15-task-{uuid.uuid4().hex}"
        body = build_task_body(
            tenant_id=tenant_id,
            agent_id=seeded.agent_id,
            actor_user_id=seeded.user_id,
            skill_id=spec.skill_id,
            artifact_id=spec.artifact_id,
            storage_key=str(seeded.storage_key),
            checksum=str(seeded.checksum),
            idempotency_key=idempotency_key,
            input_data={"sleep_sec": 0},
            retry_safety="READ_ONLY",
        )
        status_code, data = submit_task(
            http,
            stack.url_of("agent-worker", 1),
            headers=service_headers(tenant_id, stack.internal_token),
            body=body,
            idempotency_key=idempotency_key,
        )
        assert status_code == 200, f"Task 提交失败：{status_code} {data}"
        task_id = str(data["task_id"])
        wait_until(
            lambda: (row := task_row(database_url, tenant_id, task_id)) is not None
            and str(row["status"]) in TERMINAL_TASK_STATUSES,
            what="替换后的 Worker 未在超时内执行完 Task",
            timeout=120.0,
        )
        row = task_row(database_url, tenant_id, task_id)
        assert row is not None
        assert str(row["status"]) == "COMPLETED" and row["error_code"] is None, row
        assert int(row["attempt"]) == 1, "替换后的 Worker 必须恰好执行一次（无重复领取）"
        result = row["result_json"]
        assert isinstance(result, Mapping) and result.get("attempt") == 1, (
            f"Skill 子进程的真实执行回执必须是一次：{result!r}"
        )
    finally:
        http.close()


def _client_transport(base_url: str, session: ChatSession | ChatCredential) -> Mapping[str, Any]:
    """真实 API-19 投影：浏览器消费的传输参数（重连退避/次数/单帧预算），不回内部地址。"""
    with _chat_client(base_url, session) as client:
        response = client.get(CLIENT_CONFIG_PATH)
    assert response.status_code == 200, f"client-config 不可用：{response.status_code} {response.text}"
    data = response.json()["data"]
    transport = data["transport"]
    assert "sse_event_max_bytes" in transport and "sse_heartbeat_sec" in transport
    return transport


def test_s15_fd_http_pg_budgets_are_computable_and_measured(
    capacity_module: CapacityModule,
) -> None:
    """[S-15] FD/HTTP/PG 总预算可核对：预算从唯一 typed 来源可计算，且真实进程不越界。

    断言位置：
    ① profile 资源声明齐全（PG 连接/FD/存储预算与各单元 requests/limits）；
    ② PG 总连接 = Σ 进程数 × 最大副本数 × (pool + overflow) + 维护保留 ≤ pg_connection_budget；
    ③ 每进程 FD 预算 = 各 HTTP 池连接 + PG/Redis + 服务端开销 ≤ fd_budget；
    ④ SSE 总缓冲 ≤ 各单元内存上限（进程内存预算约束）；
    ⑤ 真实测量：PG 连接数 ≤ 预算；四单元进程 RSS ≤ memory_limit_mb、FD 数 ≤ fd_budget。
    """
    stack = capacity_module.stack
    resources = capacity_module.profile.resources
    assert resources is not None
    settings = SharedSettings()

    # ② PG 总连接预算
    pg_total = sum(
        resources.process_counts[unit]
        * resources.max_replica_counts[unit]
        * (settings.db_pool_size + settings.db_max_overflow)
        for unit in UNITS
    )
    assert pg_total + PG_MAINTENANCE_RESERVE <= resources.pg_connection_budget, (
        f"PG 总连接 {pg_total}+{PG_MAINTENANCE_RESERVE} 超过预算 {resources.pg_connection_budget}"
    )

    # ③ 每进程 FD 预算 + ④ SSE 缓冲与内存
    for unit in UNITS:
        hardware = resources.hardware[unit]
        assert hardware.cpu_request_millicores <= hardware.cpu_limit_millicores
        assert hardware.memory_request_mb <= hardware.memory_limit_mb
        pools = (
            settings.control_http_max_connections
            + settings.stream_http_max_connections
            + settings.model_http_max_connections
            + settings.mcp_http_max_connections
            + settings.egress_http_max_connections
            + settings.upload_http_max_connections
        )
        per_process = (
            pools
            + settings.db_pool_size
            + settings.db_max_overflow
            + settings.redis_max_connections
            + FD_SERVER_OVERHEAD
        )
        assert per_process <= resources.fd_budget, (
            f"{unit} 每进程 FD 预算 {per_process} 超过 fd_budget {resources.fd_budget}"
        )
        assert settings.sse_total_buffer_bytes <= hardware.memory_limit_mb * 1024 * 1024, (
            f"{unit} 的 SSE 总缓冲超过内存上限（进程内存预算约束）"
        )
        assert settings.sse_subscriber_buffer_bytes >= 2 * settings.sse_event_max_bytes

    # ⑤ 真实测量
    metrics = PostgresMetrics(stack.database_url, tenant_id=stack.tenant_id)
    snapshot = metrics.snapshot()
    assert snapshot.connections_total <= resources.pg_connection_budget, (
        f"实测 PG 连接 {snapshot.connections_total} 超过预算 {resources.pg_connection_budget}"
    )
    assert snapshot.locks_waiting == 0, "稳态下不得有未授予的锁等待（热点锁会在此显形）"
    for instance in stack.all_instances():
        hardware = resources.hardware[instance.unit]
        rss_mb = instance_rss_mb(instance)
        fd_count = instance_fd_count(instance)
        assert rss_mb <= hardware.memory_limit_mb, (
            f"{instance.unit}#{instance.index} RSS {rss_mb:.0f}MB 超过 {hardware.memory_limit_mb}MB"
        )
        assert fd_count <= resources.fd_budget, (
            f"{instance.unit}#{instance.index} FD {fd_count} 超过 fd_budget {resources.fd_budget}"
        )
