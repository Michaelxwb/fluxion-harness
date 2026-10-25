"""B-309：容量负载驱动与指标采集（TASK-109；设计 §2.5 验收条件 / §3.7 配置契约 / §4.3 分层验收与报告）。

真实边界（不 mock 任何服务、不用伪造成功回执）：

- **实际容量 harness/profile**：负载形状由锁定 YAML profile（`profiles/agent-capacity-webchat.example.yml`）
  驱动；受限规模运行显式登记为 `VERIFICATION`（全量 1000 用户验收由 TASK-116 用同一驱动执行，
  本文件不声称已做 1000 用户容量结论）。
- **真实 HTTP/SSE 负载**：消息经真实入口代理 → 真实 Gateway → 真实 Runtime → 真实 PG →
  TASK-106 真实模型探针子进程；SSE 经真实 socket 读 `message.delta`/`run.terminal` 帧。
- **真实指标**：PostgreSQL（连接/锁/WAL/库大小/运行状态并发）、四单元真实 `GET /metrics`
  （真实 HTTP 抓取）、驱动侧延迟样本与并发采样；模型探针控制面给出真实调用数与最大并发。
- **控制面独立探测**：取消（Gateway API-08）/确认（API-08 resume）/续租（Runtime API-17
  预占→续租→结算）各走独立请求，结局显式分类（含真实边界失败），不做选择性排除。

关键断言逐项对应 B-309 契约（成功、边界、失败分支与断言位置见各用例 docstring）。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from muad_console_platform.application.chat_token_service import (
    generate_chat_token,
    hash_chat_token,
)
from muad_console_platform.infrastructure.models.auth import ChatAccessToken
from muad_console_platform.infrastructure.models.channel import BotAccount
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    AgentDefinition,
    ModelCredential,
    ModelDefinition,
    ModelDeployment,
    PlatformUser,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.acceptance.capacity.stack import (
    UNITS,
    CapacityStack,
    acquire_datastore,
    query_value,
    run_async,
)
from tests.capacity.driver import (
    ChatCredential,
    ControlProbeKind,
    DriverError,
    FailureKind,
    LoadDriver,
    LoadReport,
    LoadScale,
    LoadTarget,
    ProbeOutcome,
    RunKind,
    plan_users,
)
from tests.capacity.metrics import (
    MetricsError,
    PostgresMetrics,
    parse_prometheus_text,
    percentile,
    summarize_latencies,
)
from tests.capacity.profile import load_profile, profile_hash

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = Path(__file__).resolve().parent / "profiles" / "agent-capacity-webchat.example.yml"
#: 模型探针首块真实延迟：容量并发与取消探测都需要一个真实的在飞窗口（探针真实 sleep）。
PROBE_LATENCY_MS = 250
PROBE_KEY = "sk-capacity-load-driver"
#: 受限验证规模：6 用户 × 2 轮、ramp 2s、稳态 3s、思考时间按比例缩放（形状保持、显式登记）。
VERIFICATION_SCALE = LoadScale(
    users=6, ramp_sec=2, steady_sec=3, turns_per_user=2, think_time_scale=0.05
)


# --------------------------------------------------------------------------------------
# 纯函数层：指标解析 / 分位数 / 负载计划（不依赖栈，毫秒级）
# --------------------------------------------------------------------------------------


def test_b309_prometheus_text_parser_reads_real_samples_and_rejects_malformed() -> None:
    """断言①：真实 Prometheus 文本解析（label/浮点/注释），坏输入显式失败、不静默回 0。"""
    text = "\n".join(
        [
            "# HELP gateway_sse_frames_total Public SSE frames by kind",
            "# TYPE gateway_sse_frames_total counter",
            'gateway_sse_frames_total{kind="event"} 17',
            'gateway_sse_frames_total{kind="heartbeat"} 3',
            'gateway_sse_ledger{scope="active_subscribers"} 2.0',
            "runtime_execution_slots 8",
            "",
        ]
    )
    samples = parse_prometheus_text(text, source="test:/metrics")
    assert len(samples) == 4, "注释行不产生样本"
    by_key = {(sample.name, tuple(sorted(sample.labels.items()))): sample.value for sample in samples}
    assert by_key[("gateway_sse_frames_total", (("kind", "event"),))] == 17.0
    assert by_key[("gateway_sse_frames_total", (("kind", "heartbeat"),))] == 3.0
    assert by_key[("gateway_sse_ledger", (("scope", "active_subscribers"),))] == 2.0
    assert by_key[("runtime_execution_slots", ())] == 8.0

    for broken in (
        "gateway_sse_frames_total{kind=\"event\"} not-a-number",
        "gateway_sse_frames_total{kind=\"event\"} nan",
        "gateway_sse_frames_total{kind=\"event\" 17",
        "gateway_sse_frames_total{kind=\"event\"} 17 extra",
    ):
        with pytest.raises(MetricsError):
            parse_prometheus_text(broken, source="test:/metrics")


def test_b309_percentiles_use_nearest_rank_and_refuse_empty_samples() -> None:
    """断言②：P95/P99 用最近秩（可复算），空样本显式失败（不伪造 0 延迟）。"""
    samples = [float(value) for value in range(1, 101)]  # 1..100
    assert percentile(samples, 0.50) == 50.0
    assert percentile(samples, 0.95) == 95.0
    assert percentile(samples, 0.99) == 99.0
    summary = summarize_latencies(samples)
    assert (summary.count, summary.p50, summary.p95, summary.p99, summary.maximum) == (
        100,
        50.0,
        95.0,
        99.0,
        100.0,
    )
    assert percentile([7.5], 0.99) == 7.5, "单样本任何分位都是它自己"

    with pytest.raises(MetricsError):
        percentile([], 0.95)
    with pytest.raises(MetricsError):
        summarize_latencies([])
    for bad in (0.0, 1.5, -0.1):
        with pytest.raises(MetricsError):
            percentile([1.0], bad)


def test_b309_plan_is_deterministic_and_samples_the_locked_profile() -> None:
    """断言③：计划是确定性的（无 RNG）、按锁定 profile 展开，并按 profile 分布取样。"""
    profile = load_profile(PROFILE_PATH)
    workload = profile.workload

    full = plan_users(workload)
    assert len(full) == workload.users == 1000, "无 scale 时按锁定 profile 展开全部用户"
    assert plan_users(workload) == full, "同一 profile 两次计划逐项相同（确定性，无 RNG）"

    scaled = plan_users(workload, scale=VERIFICATION_SCALE)
    assert len(scaled) == VERIFICATION_SCALE.users
    assert [plan.index for plan in scaled] == list(range(VERIFICATION_SCALE.users))
    offsets = [plan.start_offset_sec for plan in scaled]
    assert offsets == sorted(offsets), "ramp 按用户序号单调铺开"
    assert offsets[0] > 0.0, "首个用户不瞬间打满（ramp 起点真实错峰）"
    assert offsets[-1] <= VERIFICATION_SCALE.ramp_sec, "最后一个用户在 ramp 窗口内启动"
    assert all(len(plan.think_times_ms) == VERIFICATION_SCALE.turns_per_user for plan in scaled)
    assert all(len(plan.text_bytes) == VERIFICATION_SCALE.turns_per_user for plan in scaled)

    think = [plan.think_times_ms[0] for plan in scaled]
    assert len(set(think)) > 1, "思考时间按 profile 分布取样（不是常量冒充）"
    assert min(think) >= int(workload.input_distribution.think_time_ms.p50 * 0.05 * 0.9)
    assert max(think) <= int(workload.input_distribution.think_time_ms.max * 0.05 * 1.1)
    texts = [plan.text_bytes[0] for plan in scaled]
    assert len(set(texts)) > 1 and min(texts) >= 1, "正文大小同样按分布取样且非空"


def test_b309_acceptance_run_refuses_scaled_workload_and_verification_records_scale() -> None:
    """断言④：全量验收运行不得夹带缩放；受限运行必须显式登记 scale（不冒充 1000 用户结论）。"""
    profile = load_profile(PROFILE_PATH)
    target = LoadTarget(
        base_url="http://127.0.0.1:1",
        entry_id=str(uuid.uuid4()),
        sessions=(ChatCredential(cookie="unused", context=str(uuid.uuid4()), user_id="u"),),
        runtime_url="http://127.0.0.1:1",
        internal_token="unused",
    )
    # 构造期校验用例：这些路径不会发起任何查询，指标源只需给出真实结构。
    metrics = PostgresMetrics("postgresql+asyncpg://127.0.0.1:1/none", tenant_id="validation")
    with pytest.raises(DriverError, match="scale"):
        LoadDriver(
            target=target,
            profile=profile,
            run_kind=RunKind.ACCEPTANCE,
            scale=VERIFICATION_SCALE,
            metrics=metrics,
        )
    with pytest.raises(DriverError, match="scale"):
        LoadDriver(target=target, profile=profile, run_kind=RunKind.VERIFICATION, metrics=metrics)
    with pytest.raises(DriverError, match="think_time_scale"):
        LoadScale(users=1, ramp_sec=0, steady_sec=1, turns_per_user=1, think_time_scale=0.0)


# --------------------------------------------------------------------------------------
# 真实栈：种子数据（真实 PG 行 = 服务子进程读取的同一份库）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeededTenant:
    tenant_id: str
    agent_id: uuid.UUID
    model_id: uuid.UUID
    credential_id: uuid.UUID
    entry_id: uuid.UUID
    user_ids: tuple[uuid.UUID, ...]
    tokens: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapacityHarness:
    """真实容量栈 + 本轮种子（入口必须在 Gateway 快照首次刷新前就位）。"""

    stack: CapacityStack
    seeded: SeededTenant


#: 负载用户数 + 1 份控制探测主体（探测 Run 不占负载用户的受理份额）。
LOAD_USERS = VERIFICATION_SCALE.users
SEEDED_USERS = LOAD_USERS + 1


@pytest.fixture(scope="module")
def capacity_harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[CapacityHarness]:
    """四单元独立栈（TASK-107）：真实进程/共享卷/入口代理/模型探针；收尾只清本轮。

    顺序是真实约束：Gateway 的入口快照在启动时拉取、其后每 30s 刷新一次，所以种子（Agent/
    用户/网页入口）必须在 `stack.start()` 之前落进同一个库；模型凭据的连接地址要等探针起来，
    启动后再回填（Runtime 每次调用都向 Console 解析凭据，回填立即生效）。
    """
    datastore = acquire_datastore()
    stack = CapacityStack(
        root=tmp_path_factory.mktemp("capacity-load-driver"),
        profile=load_profile(PROFILE_PATH),
        datastore=datastore,
    )
    seeded = _seed_console(datastore.database_url, stack.tenant_id)
    stack.start()
    _bind_probe_credential(datastore.database_url, seeded.credential_id, f"{stack.model_probe.url}/v1")
    try:
        yield CapacityHarness(stack=stack, seeded=seeded)
    finally:
        stack.teardown()


def _seed_console(database_url: str, tenant_id: str) -> SeededTenant:
    """真实 PG 种子：模型/凭据/Agent/网页入口 + 每个虚拟用户一份用户/授权/Token。

    负载形状是「N 个不同用户各一个活跃 Run」：同用户并发多 Run 会撞每用户受理上限
    （`capacity.max_open_runs_per_user` 默认 2），那测的是配置边界而不是用户容量。
    """
    tokens = tuple(generate_chat_token() for _ in range(SEEDED_USERS))
    ids: dict[str, Any] = {}

    async def seed() -> None:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                model = ModelDefinition(
                    tenant_id=tenant_id,
                    key=f"capacity-model-{uuid.uuid4().hex[:8]}",
                    name="Capacity Load Model",
                    model_id="gpt-4o-mini",
                    provider_key="openai",
                    params_json={"temperature": 0.0, "max_tokens": 128},
                )
                session.add(model)
                await session.flush()
                credential = ModelCredential(
                    tenant_id=tenant_id,
                    key=f"capacity-cred-{uuid.uuid4().hex[:8]}",
                    provider_key="openai",
                    # 占位地址：栈起来后由 `_bind_probe_credential` 回填真实探针地址。
                    base_url="http://127.0.0.1:1/v1",
                    api_key=PROBE_KEY,
                    api_key_digest=hashlib.sha256(PROBE_KEY.encode()).hexdigest(),
                    max_inflight=32,
                )
                session.add(credential)
                await session.flush()
                session.add(
                    ModelDeployment(
                        tenant_id=tenant_id,
                        key=f"capacity-member-{uuid.uuid4().hex[:8]}",
                        model_id=model.id,
                        credential_id=credential.id,
                        weight=1,
                    )
                )
                agent = AgentDefinition(
                    tenant_id=tenant_id,
                    key=f"capacity-agent-{uuid.uuid4().hex[:8]}",
                    name="Capacity Load Agent",
                    instructions="You are a capacity load agent.",
                    model_id=model.id,
                    runtime_config={"max_model_retries": 1},
                )
                session.add(agent)
                await session.flush()
                entry = BotAccount(
                    tenant_id=tenant_id,
                    channel="WEBCHAT",
                    name="Capacity Webchat Entry",
                    bot_id=f"capacity-webchat-{uuid.uuid4().hex[:8]}",
                    agent_id=agent.id,
                    enabled=True,
                    config_json={},
                )
                session.add(entry)
                await session.flush()
                user_ids: list[uuid.UUID] = []
                for index, token in enumerate(tokens):
                    user = PlatformUser(
                        tenant_id=tenant_id,
                        user_code=f"capacity-user-{index}-{uuid.uuid4().hex[:8]}",
                        display_name=f"Capacity Load User {index}",
                    )
                    session.add(user)
                    await session.flush()
                    session.add(
                        AgentAccessGrant(user_id=user.id, agent_id=agent.id, granted_by=user.id)
                    )
                    session.add(
                        ChatAccessToken(
                            tenant_id=tenant_id,
                            platform_user_id=user.id,
                            token_hash=hash_chat_token(token),
                            revision=1,
                            issued_at=datetime.now(UTC),
                            expires_at=datetime.now(UTC) + timedelta(days=30),
                        )
                    )
                    user_ids.append(user.id)
                await session.commit()
                ids.update(
                    agent_id=agent.id,
                    model_id=model.id,
                    credential_id=credential.id,
                    entry_id=entry.id,
                    user_ids=tuple(user_ids),
                )
        finally:
            await engine.dispose()

    run_async(seed)
    return SeededTenant(
        tenant_id=tenant_id,
        agent_id=ids["agent_id"],
        model_id=ids["model_id"],
        credential_id=ids["credential_id"],
        entry_id=ids["entry_id"],
        user_ids=ids["user_ids"],
        tokens=tokens,
    )


def _bind_probe_credential(database_url: str, credential_id: uuid.UUID, probe_url: str) -> None:
    """把凭据连接地址回填为**本栈真实模型探针**（真实 HTTP 服务，不是替身）。"""

    async def bind() -> None:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                credential = await session.get(ModelCredential, credential_id)
                assert credential is not None, "凭据行必须已存在"
                credential.base_url = probe_url
                await session.commit()
        finally:
            await engine.dispose()

    run_async(bind)


@pytest.fixture(scope="module")
def capacity_stack(capacity_harness: CapacityHarness) -> CapacityStack:
    return capacity_harness.stack


@pytest.fixture(scope="module")
def seeded_tenant(capacity_harness: CapacityHarness) -> SeededTenant:
    return capacity_harness.seeded


@pytest.fixture(scope="module")
def chat_sessions(capacity_harness: CapacityHarness) -> tuple[ChatCredential, ...]:
    """真实 Token 交换（经入口代理 → Console API-04）：每用户一份 CHAT Cookie 与上下文身份。"""
    stack, seeded = capacity_harness.stack, capacity_harness.seeded
    credentials: list[ChatCredential] = []
    with httpx.Client(base_url=stack.proxy.url, timeout=15.0, trust_env=False) as client:
        for index, token in enumerate(seeded.tokens):
            response = client.post("/api/v1/webchat/auth/exchange", json={"token": token})
            assert response.status_code == 200, f"第 {index} 份 Token 交换失败：{response.text}"
            data = response.json()["data"]
            assert data["user_id"] == str(seeded.user_ids[index]), "交换必须导出来源 Token 的用户"
            assert data["auth_context_id"] == data["session_id"], "上下文身份就是 CHAT 会话身份"
            cookie = response.cookies.get("muad_chat_session")
            assert cookie, "交换必须写独立 CHAT Cookie（原文只在 HttpOnly Cookie 里）"
            credentials.append(
                ChatCredential(cookie=cookie, context=data["session_id"], user_id=data["user_id"])
            )
    assert len(credentials) == SEEDED_USERS
    return tuple(credentials)


@pytest.fixture(scope="module")
def load_run(
    capacity_harness: CapacityHarness, chat_sessions: tuple[ChatCredential, ...]
) -> LoadReport:
    """真实负载一次：受限规模 VERIFICATION（同一 report 供多条断言复用）。"""
    stack, seeded = capacity_harness.stack, capacity_harness.seeded
    probe = stack.model_probe
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        injected = client.post(
            f"{probe.url}/__probe__/behavior",
            json={
                "api_key": None,
                "behavior": {"latency_ms": PROBE_LATENCY_MS, "deltas": ["容量", "驱动"]},
            },
        )
        assert injected.status_code == 200, injected.text

    driver = LoadDriver(
        target=LoadTarget(
            base_url=stack.proxy.url,
            entry_id=str(seeded.entry_id),
            sessions=chat_sessions[:LOAD_USERS],
            control_session=chat_sessions[LOAD_USERS],
            runtime_url=stack.url_of("agent-runtime"),
            internal_token=stack.internal_token,
            model_probe_url=probe.url,
            notification_poll_sec=0.5,
            notification_page_size=20,
        ),
        profile=load_profile(PROFILE_PATH),
        run_kind=RunKind.VERIFICATION,
        scale=VERIFICATION_SCALE,
        metrics=PostgresMetrics(
            stack.database_url,
            tenant_id=stack.tenant_id,
            artifacts_root=stack.artifacts_root,
        ),
        unit_targets={unit: stack.url_of(unit) for unit in UNITS},
        control_model_id=str(seeded.model_id),
        control_credential_id=str(seeded.credential_id),
    )
    return driver.run()


def test_b309_driver_produces_real_http_sse_load_and_collects_real_metrics(
    capacity_stack: CapacityStack, seeded_tenant: SeededTenant, load_run: LoadReport
) -> None:
    """断言⑤（成功分支）：真实 HTTP/SSE 负载按锁定 profile 产生，并采集真实指标。

    断言位置：admitted 数 = users×turns（真实 202）、SSE 首字含真实模型延迟、终态真实落库、
    模型探针真实调用与最大并发、四单元 /metrics 真实增量、PG 的 WAL/连接/锁/库大小与并发采样。
    """
    report = load_run
    profile = load_profile(PROFILE_PATH)
    assert report.run_kind is RunKind.VERIFICATION
    assert report.locked_profile_hash == profile_hash(profile), "报告锁定本次 profile 内容 hash"
    assert report.effective_users == VERIFICATION_SCALE.users
    assert report.effective_turns == VERIFICATION_SCALE.turns_per_user
    assert report.scale == VERIFICATION_SCALE, "受限规模显式登记，不冒充全量验收"

    assert report.attempts() == VERIFICATION_SCALE.users * VERIFICATION_SCALE.turns_per_user
    assert report.planned_attempts == report.attempts(), "计划轮次与实际尝试逐项一致（不丢样本）"
    assert report.steady_violations == (), "所有轮次都在 ramp+steady 窗口内启动"
    assert len(report.admitted()) == report.attempts(), "每一轮都拿到真实 202 受理"
    assert len(report.completed()) >= 1, "至少一轮真实执行到终态"
    assert report.failure_counts() == {FailureKind.ADMISSION_REJECTED.value: 1}, (
        "错误账目完整：12 轮全成功 + 唯一一次故意边界拒绝（不做选择性排除）"
    )

    admission = report.admission_latency()
    first_delta = report.first_delta_latency()
    assert admission is not None and admission.count == report.attempts()
    assert first_delta is not None and first_delta.count >= 1
    assert first_delta.p95 >= PROBE_LATENCY_MS, "首字测量包含真实模型首块延迟（不是假回执）"

    assert report.last_start_sec - report.first_start_sec >= VERIFICATION_SCALE.ramp_sec * 0.5, (
        "ramp 真实错峰（用户不是瞬间全部启动）"
    )
    assert report.wall_seconds() >= VERIFICATION_SCALE.ramp_sec

    # 真实持久事实：终态与正文事件真的落在本栈 PG 上
    completed = query_value(
        capacity_stack.database_url,
        "SELECT count(*) FROM runtime.run_record WHERE tenant_id = $1 AND status = 'COMPLETED'",
        capacity_stack.tenant_id,
    )
    assert completed >= 1
    events = query_value(
        capacity_stack.database_url,
        "SELECT count(*) FROM runtime.canonical_event WHERE tenant_id = $1",
        capacity_stack.tenant_id,
    )
    assert events > 0, "真实事件写入（不是空跑）"

    # 真实推理并发与调用回执（模型探针控制面）
    probe = report.model_probe
    assert probe is not None
    assert probe.calls_after - probe.calls_before >= report.attempts(), "每次轮次都真实到达模型探针"
    assert probe.max_concurrency >= 1, "探针观测到真实在飞推理"
    assert probe.faults == {}, "未注入故障时探针故障计数为空"

    # 四单元 /metrics 真实增量（真实 HTTP 抓取，不是进程内记账）
    gateway_events = report.unit_metric_deltas["im-gateway"]['gateway_sse_frames_total{kind="event"}']
    assert gateway_events > 0, "Gateway 公开流真实下发事件帧"
    runtime_admissions = report.unit_metric_deltas["agent-runtime"][
        'run_admission_total{status="ACCEPTED"}'
    ]
    assert runtime_admissions >= report.attempts(), "Runtime 准入计数与真实受理一致"
    for unit in UNITS:
        assert report.unit_metric_deltas[unit], f"{unit} 的 /metrics 必须真实可抓取"

    # PG 指标：WAL 增长、连接、锁、库大小与并发采样
    assert report.wal_growth_bytes > 0, "真实写入必然增长 WAL"
    assert report.postgres_after.database_bytes > 0
    assert report.postgres_after.connections_total >= 1
    assert report.postgres_after.locks_granted >= 1
    assert report.postgres_after.event_rows > 0, "真实事件行必须已持久化"
    assert report.postgres_after.event_bytes > 0, "事件表必须占用真实磁盘"
    assert report.postgres_after.connections_idle_in_transaction >= 0
    assert report.max_connections_total >= report.postgres_after.connections_total
    rate = report.postgres_rate()
    assert rate.commits_per_sec > 0, "真实写入窗口必然有提交"
    assert rate.wal_bytes_per_sec > 0, "真实写入窗口必然增长 WAL"
    assert report.samples, "并发采样序列非空"
    assert any(sample.connections_total >= 1 for sample in report.samples), "采样包含 PG 连接状态"
    assert any(sample.admitted_runs >= 1 for sample in report.samples), "采样观测到持久受理中的 Run"
    assert any(sample.running_runs >= 1 for sample in report.samples), "采样观测到真实执行中的 Run"
    assert any(sample.active_streams >= 1 for sample in report.samples), "采样观测到在飞 SSE 订阅"

    # 浏览器同形通知轮询：真实 CHAT 主体、带 page_size、游标不回退
    assert report.notification_polls, "负载期间必须有真实通知轮询样本"
    assert all(poll.http_status == 200 for poll in report.notification_polls), (
        "通知轮询必须全部成功（真实 200，不是伪造空页）"
    )
    assert all(poll.error == "" for poll in report.notification_polls)
    assert all(poll.page_size == 20 for poll in report.notification_polls)
    by_user: dict[int, list[int]] = {}
    for poll in report.notification_polls:
        by_user.setdefault(poll.user_index, []).append(poll.next_seq)
    assert all(values == sorted(values) for values in by_user.values()), "游标单调不回退"

    # 边界失败分类（故意非法会话提交）：显式类别 + 真实 HTTP 状态码
    boundary = report.boundary
    assert boundary is not None and boundary.failure is FailureKind.ADMISSION_REJECTED
    assert boundary.http_status is not None and 400 <= boundary.http_status < 500
    assert boundary.detail, "失败必须带可核对的细节（不静默吞掉）"


def test_b309_control_probes_cancel_confirm_renew_are_independent_and_classified(
    load_run: LoadReport,
) -> None:
    """断言⑥（边界分支）：取消/确认/续租在负载中独立探测，结局显式分类且真实生效。

    断言位置：三类探测各至少一次；取消在 PG 行上真实生效；确认给出稳定错误码或真实回执；
    续租核 epoch 且 `lease_until` 不缩短、结算落 SETTLED。
    """
    report = load_run
    probes = report.control_probes
    kinds = {probe.kind for probe in probes}
    assert kinds == {ControlProbeKind.CANCEL, ControlProbeKind.CONFIRM, ControlProbeKind.RENEW}, (
        "三类控制探测都必须真实执行（不是只跑其中一类）"
    )
    assert all(probe.latency_ms > 0 for probe in probes)
    assert all(probe.latency_ms < 15000 for probe in probes), "控制面在负载下仍被服务（不排长流队尾）"
    assert all(
        probe.outcome in {ProbeOutcome.OK, ProbeOutcome.REJECTED} for probe in probes
    ), "控制探测的结局必须显式分类（超时/传输错误会被计为失败，不能冒充成功）"

    cancels = [probe for probe in probes if probe.kind is ControlProbeKind.CANCEL]
    assert cancels and all(probe.outcome is ProbeOutcome.OK for probe in cancels)
    cancelled = [
        probe
        for probe in cancels
        if probe.run_state is not None
        and (probe.run_state.cancel_requested or probe.run_state.status == "CANCELLED")
    ]
    assert cancelled, "取消必须在 PG 行上真实生效（cancel_requested 或 CANCELLED）"

    confirms = [probe for probe in probes if probe.kind is ControlProbeKind.CONFIRM]
    assert confirms
    for probe in confirms:
        if probe.outcome is ProbeOutcome.REJECTED:
            assert probe.error_code, "拒绝必须带稳定错误码（不静默）"

    renews = [probe for probe in probes if probe.kind is ControlProbeKind.RENEW]
    assert renews and all(probe.outcome is ProbeOutcome.OK for probe in renews)
    for probe in renews:
        lease = probe.lease
        assert lease is not None
        assert lease.lease_until_after_epoch >= lease.lease_until_before_epoch, "续租不得缩短租约"
        assert lease.settled_state == "SETTLED", "结算落真实终态"

    # 独立性：三类控制探测都在负载窗口内（与长流并行），不是负载跑完后的补测
    assert all(
        report.first_start_sec <= probe.started_at <= report.finished_at for probe in probes
    ), "控制探测必须发生在负载窗口内（满载下仍可服务）"
