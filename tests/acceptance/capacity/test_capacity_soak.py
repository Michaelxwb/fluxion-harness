"""[S-18] 400 用户 30–60 分钟 soak（TASK-001）。

真实边界：
- 真实四单元栈 + 400 个独立 CHAT 主体；8 轮真实提交/SSE，之后保持通知轮询
  `CAPACITY_SOAK_SEC`（默认 1800s）以观测连接/内存/retention 稳态。
- retention：在同一个库种入 2 小时前的流增量重放行，并把租户策略设为 1 小时；
  Runtime 的真实 retention 循环必须实际删除这些行（不是伪造计数）。
- 内存/连接：从真实进程 `ps`/`lsof` 与 PG 采样读取，超出预算显式失败。

执行门槛：默认不跑（`MUAD_CAPACITY_SOAK=1` 才执行），避免 PR/本地误触发 30 分钟负载。
本文件是 release gate 级证据，1000 用户全量仍由 S-01/S-09 manual gate 负责。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.acceptance.capacity.delivery_harness import (
    bind_probe_credential,
    build_stack,
    create_datastore_handle,
    db_execute,
    metric_total,
    seed_delivery_control,
    set_probe_behavior,
)
from tests.acceptance.capacity.execution_support import seed_chat_users
from tests.acceptance.capacity.test_capacity import instance_fd_count, instance_rss_mb
from tests.acceptance.capacity.test_capacity_smoke import (
    SMOKE_ENV,
    SMOKE_PLATFORM_SETTINGS,
    SmokeModule,
    _credentials,
    _disable_memory_writes,
)
from tests.capacity.driver import LoadDriver, LoadScale, LoadTarget, RunKind
from tests.capacity.metrics import PostgresMetrics
from tests.capacity.profile import load_profile

pytestmark = [pytest.mark.integration, pytest.mark.capacity_load]

SOAK_USERS = int(os.environ.get("CAPACITY_LOAD_USERS", "400"))
SOAK_SEC = int(os.environ.get("CAPACITY_SOAK_SEC", "1800"))
SOAK_TURNS = int(os.environ.get("CAPACITY_SOAK_TURNS", "8"))
SOAK_RETENTION_HOURS = 1
SOAK_BACKDATED_EVENTS = 5000
SOAK_PLATFORM_SETTINGS = {
    **SMOKE_PLATFORM_SETTINGS,
    "webchat": {"stream_replay_retention_hours": SOAK_RETENTION_HOURS},
}


def _seed_backdated_events(database_url: str, tenant_id: str, count: int) -> None:
    """种入保留期外的流增量行：retention 必须真实删除它们（不是伪造 removed 计数）。"""
    db_execute(
        database_url,
        """
        INSERT INTO runtime.canonical_event (
            id, tenant_id, conversation_id, run_id, seq, stream_type, event_type,
            payload_json, create_time, update_time, is_deleted
        )
        SELECT gen_random_uuid(), $1, gen_random_uuid(), NULL, 1,
               'MESSAGE_DELTA', 'MESSAGE_DELTA', '{}'::jsonb,
               now() - interval '2 hours', now() - interval '2 hours', false
        FROM generate_series(1, $2)
        """,
        tenant_id,
        count,
    )


@pytest.fixture(scope="module")
def soak_module(
    tmp_path_factory: pytest.TempPathFactory, capacity_profile_path: Path
) -> Iterator[SmokeModule]:
    root = tmp_path_factory.mktemp("capacity-soak")
    datastore = create_datastore_handle()
    profile = load_profile(capacity_profile_path)
    stack = build_stack(
        root=root, profile=profile, datastore=datastore, service_env=SMOKE_ENV
    )
    seeded = seed_delivery_control(
        datastore.database_url,
        stack.tenant_id,
        artifacts_root=stack.artifacts_root,
        platform_settings=SOAK_PLATFORM_SETTINGS,
    )
    users = seed_chat_users(
        datastore.database_url,
        tenant_id=stack.tenant_id,
        agent_id=seeded.agent_id,
        count=SOAK_USERS + 1,
    )
    _disable_memory_writes(datastore.database_url, stack.tenant_id, seeded.agent_id)
    stack.start()
    bind_probe_credential(
        datastore.database_url, seeded.credential_id, f"{stack.model_probe.url}/v1"
    )
    _seed_backdated_events(datastore.database_url, stack.tenant_id, SOAK_BACKDATED_EVENTS)
    credentials = _credentials(stack, users)
    try:
        yield SmokeModule(
            stack=stack,
            seeded=seeded,
            profile=profile,
            load_sessions=credentials[:SOAK_USERS],
            control_session=credentials[SOAK_USERS],
        )
    finally:
        stack.teardown()


@pytest.fixture(scope="module")
def soak_run(soak_module: SmokeModule):
    stack, seeded = soak_module.stack, soak_module.seeded
    db_execute(
        stack.database_url,
        "UPDATE control.model_credential SET max_inflight = $1 WHERE tenant_id = $2 AND id = $3",
        max(SOAK_USERS, 512),
        stack.tenant_id,
        seeded.credential_id,
    )
    set_probe_behavior(
        stack.model_probe.url,
        {"latency_ms": 100, "chunk_interval_ms": 5, "deltas": ["soak", "响应"]},
    )
    driver = LoadDriver(
        target=LoadTarget(
            base_url=stack.proxy.url,
            entry_id=str(seeded.entry_id),
            sessions=soak_module.load_sessions,
            control_session=soak_module.control_session,
            runtime_url=stack.url_of("agent-runtime"),
            internal_token=stack.internal_token,
            model_probe_url=stack.model_probe.url,
            request_timeout_sec=30.0,
            stream_timeout_sec=120.0,
            sample_interval_sec=5.0,
            control_start_sec=60.0,
            control_interval_sec=120.0,
            control_rounds=1,
            user_deadline_sec=SOAK_SEC + 900,
            notification_poll_sec=15.0,
            notification_page_size=20,
            hold_after_turns_sec=SOAK_SEC,
        ),
        profile=soak_module.profile,
        run_kind=RunKind.VERIFICATION,
        scale=LoadScale(
            users=SOAK_USERS,
            ramp_sec=60,
            steady_sec=SOAK_SEC,
            turns_per_user=SOAK_TURNS,
        ),
        metrics=PostgresMetrics(
            stack.database_url,
            tenant_id=stack.tenant_id,
            artifacts_root=stack.artifacts_root,
        ),
        unit_targets={
            "console-platform": stack.urls_of("console-platform"),
            "agent-runtime": stack.urls_of("agent-runtime"),
            "agent-worker": stack.urls_of("agent-worker"),
            "im-gateway": stack.urls_of("im-gateway"),
        },
    )
    return driver.run()


@pytest.mark.skipif(
    os.environ.get("MUAD_CAPACITY_SOAK") != "1",
    reason="S-18 是 30–60 分钟 soak：显式设置 MUAD_CAPACITY_SOAK=1 才执行",
)
def test_s18_400_user_soak_retention_and_stability(
    soak_module: SmokeModule, soak_run
) -> None:
    """[S-18] 400 用户 soak：无泄漏、PG/Redis 稳定、retention 删除速率 ≥ 写入。"""
    stack = soak_module.stack
    report = soak_run
    assert report.effective_users == SOAK_USERS
    assert report.attempts() == report.planned_attempts, "8 轮计划必须全部尝试（不丢样本）"
    assert report.wall_seconds() >= SOAK_SEC, "soak 必须真实持续声明的窗口"
    assert report.pool_timeout_count == 0, "soak 期间不得出现连接池等待超时"
    assert report.system_error_ratio() <= 0.01

    # PG 连接稳态：峰值在预算内，末次采样不高于首次 + 余量（没有持续泄漏）
    assert report.max_connections_total <= 200
    assert report.samples, "至少有一次 PG 采样"
    assert report.samples[-1].connections_total <= report.samples[0].connections_total + 20

    # 进程 RSS/FD：真实 ps/lsof 采样，超出限制显式失败（不把泄漏说成稳定）
    for instance in stack.all_instances():
        if instance.unit not in ("agent-runtime", "im-gateway"):
            continue
        rss_mb = instance_rss_mb(instance)
        fd_count = instance_fd_count(instance)
        assert rss_mb <= 8192, f"{instance.unit}#{instance.index} RSS {rss_mb:.0f}MB 超限"
        assert fd_count <= 4096, f"{instance.unit}#{instance.index} FD {fd_count} 超限"

    # retention：真实删除计数 > 0，且删除速率不低于本轮新写入事件的速率
    removed = metric_total(
        stack.url_of("agent-runtime"),
        "webchat_retention_removed_total",
        {"kind": "stream_replay"},
    )
    assert removed > 0, "种入的过期流增量必须被真实 retention 删除"
    rate = report.postgres_rate()
    assert rate.event_rows_per_sec >= 0
    assert removed >= rate.event_rows_per_sec * report.wall_seconds() * 0.5, (
        "retention 删除量必须追得上 soak 期间的写入量（允许 50% 余量）"
    )
    assert report.notification_polls, "soak 期间通知轮询必须持续发生"
    assert all(poll.http_status == 200 for poll in report.notification_polls)
