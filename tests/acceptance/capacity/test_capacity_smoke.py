"""[S-16] 100 用户 capacity smoke（TASK-001）。

真实边界（不 mock 业务服务）：
- 真实四单元独立栈 + 真实入口代理 + 真实 Gateway → Runtime → PostgreSQL/Redis；
- 模型侧是真实 HTTP 流式探针：20 chunks/s × 10s（200 个 delta），不是空跑；
- 100 个不同用户各自一份真实 CHAT 主体，负载期间按浏览器同形节奏轮询通知（15s）；
- SLO 在本用例内直接断言，不依赖外部 report harness。

本文件是 PR 级 smoke，标记 `capacity_load`：默认 `make acceptance` 不跑，容量环境用
`CAPACITY_LOAD_USERS=100 uv run pytest -q tests/acceptance/capacity/test_capacity_smoke.py`。
1000 用户全量结论仍由 S-01/S-09 的 manual release gate 负责，本文件不冒充。

与旧 `test_capacity.py` 的 6 用户驱动不同：本文件使用生产节拍（租约/心跳 30s/5s），
因为 20 chunks/s × 10s 的真实流必须活过租约窗口；加速节拍只适合短流恢复用例。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.acceptance.capacity.delivery_harness import (
    SeededDelivery,
    bind_probe_credential,
    build_stack,
    create_datastore_handle,
    db_execute,
    exchange_chat_session,
    seed_delivery_control,
    set_probe_behavior,
)
from tests.acceptance.capacity.execution_support import seed_chat_users
from tests.acceptance.capacity.stack import CapacityStack, run_async
from tests.capacity.driver import (
    ChatCredential,
    LoadDriver,
    LoadScale,
    LoadTarget,
    RunKind,
)
from tests.capacity.metrics import PostgresMetrics
from tests.capacity.profile import CapacityProfile, load_profile
from tests.capacity.slo import SloInput, SloThresholds, evaluate_slo, require_slo

pytestmark = [pytest.mark.integration, pytest.mark.capacity_load]

SMOKE_USERS = int(os.environ.get("CAPACITY_LOAD_USERS", "100"))
#: 20 chunks/s × 10s = 200 个真实增量帧。
SMOKE_CHUNKS = tuple(f"容量分片{index:03d}-" for index in range(200))
SMOKE_CHUNK_INTERVAL_MS = 50
SMOKE_MODEL_LATENCY_MS = 50
#: S-16 的 smoke 阈值（场景契约；不替代 profile 的正式 SLO）。
SMOKE_THRESHOLDS = SloThresholds(
    admission_p95_ms=1000,
    admission_p99_ms=None,
    first_delta_p95_ms=2500,
    max_system_error_ratio=0.0,
    max_connections_total=120,
    max_idle_in_tx=20,
)
#: 生产节拍：10s 真实流必须活过租约窗口；加速节拍只用于短流恢复用例。
SMOKE_ENV: dict[str, dict[str, str]] = {
    "agent-runtime": {
        "RUN_LEASE_SEC": "30",
        "RUN_HEARTBEAT_SEC": "5",
        "RUN_REAPER_INTERVAL_SEC": "15",
        "MODEL_CALL_LEASE_SEC": "30",
        "MODEL_CALL_HEARTBEAT_SEC": "5",
        "RUN_EVENT_HEARTBEAT_SEC": "5",
        "RUN_EVENT_FALLBACK_POLL_SEC": "2",
        # TASK-004 的生产窗口是 300ms；smoke 旧值 50ms 会在 100×20 delta/s 下制造
        # 20 commits/s/Run 的写放大（首段仍立即 flush，不影响首字）。
        "STREAM_DELTA_FLUSH_INTERVAL_MS": "300",
        "STREAM_DELTA_FLUSH_BYTES": "8192",
        "CHANNEL_DELIVERY_POLL_SEC": "2",
        "CHANNEL_DELIVERY_RETRY_BASE_SEC": "2",
        "RUNTIME_EXECUTION_CONCURRENCY": "128",
        "MODEL_HTTP_MAX_CONNECTIONS": "128",
        # 本地 PG max_connections=100：runtime 2×30 + worker 2×5 + console 1×20 = 90，
        # 留出测试/维护余量；生产按 profile 的 pg_connection_budget 预算，不照抄本值。
        "DB_POOL_SIZE": "20",
        "DB_MAX_OVERFLOW": "10",
    },
    "im-gateway": {
        "DB_POOL_SIZE": "5",
        "DB_MAX_OVERFLOW": "5",
        # 单 Gateway 承载 100/1000 用户：业务出站池必须覆盖并发会话/状态/通知请求。
        # 本机 .env 的开发值 16 是给单机调试的，不能用于容量结论。
        "EGRESS_HTTP_MAX_CONNECTIONS": "1024",
        "EGRESS_HTTP_MAX_KEEPALIVE": "256",
    },
    "console-platform": {
        "DB_POOL_SIZE": "10",
        "DB_MAX_OVERFLOW": "10",
    },
    "agent-worker": {
        "DB_POOL_SIZE": "3",
        "DB_MAX_OVERFLOW": "2",
        "WORKER_POLL_INTERVAL_SEC": "1",
        "TASK_HEARTBEAT_SEC": "5",
        "TASK_LEASE_SEC": "30",
        "TASK_CANCEL_CHECK_SEC": "2",
        "TASK_DEADLINE_SWEEP_INTERVAL_SEC": "5",
        "RUN_REAPER_INTERVAL_SEC": "5",
        "DELIVERY_POLL_INTERVAL_SEC": "2",
        "SCHEDULER_POLL_INTERVAL_SEC": "2",
        "WORKER_EXECUTION_CONCURRENCY": "16",
    },
}
SMOKE_PLATFORM_SETTINGS = {
    "model_pool": {"dispatch_epoch_sec": 3600},
    "capacity": {"queue_wait_timeout_sec": 300, "model_wait_timeout_sec": 120},
}


@dataclass(frozen=True, slots=True)
class SmokeModule:
    stack: CapacityStack
    seeded: SeededDelivery
    profile: CapacityProfile
    load_sessions: tuple[ChatCredential, ...]
    control_session: ChatCredential


def _disable_memory_writes(database_url: str, tenant_id: str, agent_id: uuid.UUID) -> None:
    """关闭后台记忆提炼：容量 smoke 只测交互推理，不让提炼 job 抢额度。"""

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


def _credentials(
    stack: CapacityStack, users: tuple[object, ...]
) -> tuple[ChatCredential, ...]:
    return tuple(
        ChatCredential(cookie=session.cookie, context=session.context, user_id=str(session.user_id))
        for session in (
            exchange_chat_session(stack.proxy.url, user.token, user.user_id)  # type: ignore[attr-defined]
            for user in users
        )
    )


@pytest.fixture(scope="module")
def smoke_module(
    tmp_path_factory: pytest.TempPathFactory, capacity_profile_path: Path
) -> Iterator[SmokeModule]:
    """生产节拍的真实栈：100 个负载主体 + 1 个控制主体。"""
    debug_root = os.environ.get("MUAD_SMOKE_DEBUG_ROOT")
    root = Path(debug_root) if debug_root else tmp_path_factory.mktemp("capacity-smoke")
    root.mkdir(parents=True, exist_ok=True)
    datastore = create_datastore_handle()
    profile = load_profile(capacity_profile_path)
    stack = build_stack(
        root=root, profile=profile, datastore=datastore, service_env=SMOKE_ENV
    )
    seeded = seed_delivery_control(
        datastore.database_url,
        stack.tenant_id,
        artifacts_root=stack.artifacts_root,
        platform_settings=SMOKE_PLATFORM_SETTINGS,
    )
    users = seed_chat_users(
        datastore.database_url,
        tenant_id=stack.tenant_id,
        agent_id=seeded.agent_id,
        count=SMOKE_USERS + 1,
    )
    _disable_memory_writes(datastore.database_url, stack.tenant_id, seeded.agent_id)
    stack.start()
    bind_probe_credential(
        datastore.database_url, seeded.credential_id, f"{stack.model_probe.url}/v1"
    )
    credentials = _credentials(stack, users)
    try:
        yield SmokeModule(
            stack=stack,
            seeded=seeded,
            profile=profile,
            load_sessions=credentials[:SMOKE_USERS],
            control_session=credentials[SMOKE_USERS],
        )
    finally:
        if os.environ.get("MUAD_SMOKE_DEBUG_ROOT"):
            keep = Path("/tmp/muad-smoke-logs")
            keep.mkdir(parents=True, exist_ok=True)
            for path in sorted((root / "logs").glob("*.log")):
                (keep / path.name).write_bytes(path.read_bytes())
        stack.teardown()


def _baseline_run(module: SmokeModule, *, users: int):
    stack, seeded = module.stack, module.seeded
    # 100 条 10s 流同时要模型槽：把物理凭据的在飞上限提到覆盖本轮用户数，
    # 否则测到的是 16 槽模型的排队，而不是 100 用户容量（不改变探针/流式形状）。
    db_execute(
        stack.database_url,
        "UPDATE control.model_credential SET max_inflight = $1 WHERE tenant_id = $2 AND id = $3",
        max(users, 128),
        stack.tenant_id,
        seeded.credential_id,
    )
    set_probe_behavior(
        stack.model_probe.url,
        {
            "latency_ms": SMOKE_MODEL_LATENCY_MS,
            "chunk_interval_ms": SMOKE_CHUNK_INTERVAL_MS,
            "deltas": list(SMOKE_CHUNKS),
        },
    )
    driver = LoadDriver(
        target=LoadTarget(
            base_url=stack.proxy.url,
            entry_id=str(seeded.entry_id),
            sessions=module.load_sessions[:users],
            control_session=module.control_session,
            runtime_url=stack.url_of("agent-runtime"),
            internal_token=stack.internal_token,
            model_probe_url=stack.model_probe.url,
            request_timeout_sec=30.0,
            stream_timeout_sec=120.0,
            sample_interval_sec=0.5,
            control_interval_sec=30.0,
            control_start_sec=60.0,
            control_rounds=1,
            user_deadline_sec=180.0,
            notification_poll_sec=15.0,
            notification_page_size=20,
        ),
        profile=module.profile,
        run_kind=RunKind.VERIFICATION,
        scale=LoadScale(
            users=users,
            ramp_sec=10,
            steady_sec=120,
            turns_per_user=1,
        ),
        metrics=PostgresMetrics(
            stack.database_url,
            tenant_id=stack.tenant_id,
            artifacts_root=stack.artifacts_root,
        ),
        unit_targets={
            "console-platform": stack.url_of("console-platform"),
            "agent-runtime": stack.url_of("agent-runtime"),
            "agent-worker": stack.url_of("agent-worker"),
            "im-gateway": stack.url_of("im-gateway"),
        },
    )
    return driver.run()


@pytest.fixture(scope="module")
def smoke_run(smoke_module: SmokeModule):
    """S-16 的一次真实 smoke 负载：100 用户 × 1 轮 × 20 chunks/s + 通知轮询。"""
    return _baseline_run(smoke_module, users=SMOKE_USERS)


def test_s16_100_user_capacity_smoke(smoke_run, smoke_module: SmokeModule) -> None:
    """[S-16] 100 用户 smoke：admission/first-delta/错误/PG/通知轮询都在本用例内判定。"""
    report = smoke_run
    assert report.effective_users == SMOKE_USERS
    assert report.effective_turns == 1
    assert report.run_kind is RunKind.VERIFICATION
    assert report.scale is not None and report.scale.users == SMOKE_USERS, (
        "受限 smoke 必须显式登记规模，不得冒充 1000 用户"
    )
    assert report.planned_attempts == SMOKE_USERS
    assert report.attempts() == SMOKE_USERS, "每个用户都尝试了一次真实提交（不丢样本）"
    failure_samples = [
        (turn.user_index, turn.failure, turn.http_status, (turn.detail or "")[:120])
        for turn in report.turns
        if turn.failure is not None
    ][:5]
    assert len(report.admitted()) == SMOKE_USERS, (
        "每一轮都拿到真实 202；"
        f"失败账目 {dict(report.failure_counts())}，样本 {failure_samples}"
    )
    assert report.steady_violations == (), "所有提交都在 ramp+steady 窗口内"

    checks = evaluate_slo(
        SloInput(
            admission=report.admission_latency(),
            first_delta=report.first_delta_latency(),
            system_error_ratio=report.system_error_ratio(),
            pool_timeouts=report.pool_timeout_count,
            attempts=report.attempts(),
            planned_attempts=report.planned_attempts,
            steady_violations=len(report.steady_violations),
            max_connections_total=report.max_connections_total,
            max_idle_in_tx=report.max_idle_in_tx,
        ),
        SMOKE_THRESHOLDS,
    )
    if os.environ.get("MUAD_SMOKE_DEBUG_ROOT"):
        from collections import Counter

        seen = Counter(
            query
            for sample in report.samples
            for query, _age in sample.idle_in_tx_queries
        )
        ages = {
            query: max(
                age
                for sample in report.samples
                for candidate, age in sample.idle_in_tx_queries
                if candidate == query
            )
            for query in seen
        }
        print("SMOKE-IDLE-IN-TX top queries:")
        for query, count in seen.most_common(10):
            print(f"  {count}x max_age={ages[query]:.2f}s :: {query[:300]}")
        print(
            "SMOKE-PG max_total=",
            report.max_connections_total,
            "idle_tx_peak=",
            report.max_idle_in_tx,
            "rate=",
            report.postgres_rate(),
        )
    load_failures = [turn for turn in report.turns if turn.failure is not None]
    assert not load_failures, (
        "smoke 负载不得有失败轮次："
        f"失败账目 {dict(report.failure_counts())}，"
        "样本 "
        f"{[(t.user_index, t.failure, t.http_status, (t.detail or '')[:200]) for t in load_failures[:8]]}"
    )
    require_slo(checks)

    # 真实流式形状：20 chunks/s × 10s（探针真实逐帧下发，SSE 真实读取）
    completed = report.completed()
    assert completed, "至少一轮到达终态"
    delta_counts = [turn.delta_frames for turn in completed]
    # 运行时按 50ms 窗口合并增量，帧数可少于 200；下界仍须证明 20 chunks/s 的真实流形状。
    assert min(delta_counts) >= 150, (
        f"每轮至少 150 个真实增量帧（约 20/s × 10s），实际 {min(delta_counts)}"
    )

    assert report.pool_timeout_count == 0, "不得出现连接池等待超时"

    # 通知轮询：真实 200、带 page_size、游标单调
    assert report.notification_polls, "100 个用户至少各轮询一次通知"
    assert all(poll.http_status == 200 for poll in report.notification_polls)
    assert all(poll.error == "" for poll in report.notification_polls)
    assert {poll.page_size for poll in report.notification_polls} == {20}
    assert report.notification_latency() is not None
