"""[B-01] 瞬时并发受理的有界队列/显式拒绝与排空（TASK-005）。

真实边界：真实四单元栈 + 入口代理 + 真实 PG/Redis + 真实模型探针。
本文件用 100 用户**瞬时**（ramp=0）提交，验证：

- 不出现 5xx / pool timeout / 无界排队导致的驱动丢样本；
- 过载只允许显式的 `ADMISSION_REJECTED`（429/CHANNEL_BUSY 一类），不得静默吞请求；
- 所有已受理的 Run 都能在 PG 查到，并在模型返回后按 SLO 窗口排空；
- Runtime 进程不因突发而 OOM（RSS 预算内）。

1000 用户全量 release gate 仍由 S-01/E-09 在 staging 执行；本文件不冒充 1000 结论。
"""

from __future__ import annotations

import time

import pytest

from tests.acceptance.capacity.delivery_harness import set_probe_behavior
from tests.acceptance.capacity.stack import run_async
from tests.acceptance.capacity.test_capacity_smoke import (  # noqa: F401  (fixture 复用)
    SmokeModule,
    smoke_module,
)
from tests.capacity.driver import (
    FailureKind,
    LoadDriver,
    LoadScale,
    LoadTarget,
    RunKind,
)
from tests.capacity.metrics import PostgresMetrics

pytestmark = [pytest.mark.integration, pytest.mark.capacity_load]

BURST_USERS = 100


def _open_runs(stack: SmokeModule) -> int:
    metrics = PostgresMetrics(stack.stack.database_url, tenant_id=stack.stack.tenant_id)
    return int(metrics.snapshot().open_runs)


def test_b01_instant_burst_is_bounded_and_drains(
    smoke_module: SmokeModule,  # noqa: F811  (pytest fixture 注入，与导入名相同)
) -> None:
    stack, seeded = smoke_module.stack, smoke_module.seeded
    # 短流：突发只验证受理/队列，不让 200 帧流把排空窗口拉长。
    set_probe_behavior(
        stack.model_probe.url,
        {"latency_ms": 20, "chunk_interval_ms": 20, "deltas": ["突发", "完成"]},
    )
    driver = LoadDriver(
        target=LoadTarget(
            base_url=stack.proxy.url,
            entry_id=str(seeded.entry_id),
            sessions=smoke_module.load_sessions[:BURST_USERS],
            control_session=smoke_module.control_session,
            runtime_url=stack.url_of("agent-runtime"),
            internal_token=stack.internal_token,
            model_probe_url=stack.model_probe.url,
            request_timeout_sec=30.0,
            stream_timeout_sec=60.0,
            sample_interval_sec=0.5,
            control_start_sec=5.0,
            control_interval_sec=10.0,
            control_rounds=1,
            control_terminal_wait_sec=5.0,
            user_deadline_sec=120.0,
            notification_poll_sec=15.0,
            notification_page_size=20,
        ),
        profile=smoke_module.profile,
        run_kind=RunKind.VERIFICATION,
        # ramp=0：瞬时提交，专门压准入边界。
        scale=LoadScale(users=BURST_USERS, ramp_sec=0, steady_sec=60, turns_per_user=1),
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
    report = driver.run()
    assert report.effective_users == BURST_USERS
    assert report.planned_attempts == BURST_USERS
    assert report.attempts() == BURST_USERS, "突发不得让驱动丢样本"
    failures = [t for t in report.turns if t.failure is not None]
    # 允许**显式 429**（受理或建会话阶段）；其余一律算硬失败。
    allowed_overload = (FailureKind.ADMISSION_REJECTED, FailureKind.CONVERSATION_REJECTED)
    hard_failures = [
        turn
        for turn in failures
        if turn.failure not in allowed_overload or turn.http_status != 429
    ]
    assert not hard_failures, (
        "只允许显式 429 过载拒绝；实际失败账目 "
        f"{dict(report.failure_counts())}，样本 "
        f"{[(t.user_index, t.failure, t.http_status, (t.detail or '')[:160]) for t in hard_failures[:8]]}"
    )
    rejected = [turn for turn in failures if turn.failure in allowed_overload]
    assert all(turn.http_status == 429 for turn in rejected), (
        f"过载拒绝必须是 429：{[(t.http_status, t.detail) for t in rejected][:5]}"
    )
    assert report.pool_timeout_count == 0
    admitted = report.admitted()
    assert admitted, "瞬时突发至少要有受理样本"
    run_ids = [turn.run_id for turn in admitted if turn.run_id]
    rows = run_async(
        lambda: _count_runs(stack.database_url, stack.tenant_id, run_ids)
    )
    assert rows == len(run_ids), "每个已受理 Run 都必须能在 PG 查到"

    metrics = PostgresMetrics(stack.database_url, tenant_id=stack.tenant_id)
    assert metrics.snapshot().connections_total <= 200, "PG 连接不得被突发打穿预算"

    # 排空：短流应在预算内回到 0 个非终态 Run。
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if _open_runs(smoke_module) == 0:
            break
        time.sleep(1)
    assert _open_runs(smoke_module) == 0, "突发后未在预算内排空（有 Run 卡在队列/执行）"

    # 恢复：突发退去后，同一个入口/主体必须能重新建会话并提交（429 只约束过载窗口）。
    credential = smoke_module.load_sessions[0]
    recovered = _create_and_submit(
        stack.proxy.url,
        entry_id=str(seeded.entry_id),
        cookie=credential.cookie,
        context=credential.context,
    )
    assert recovered, "过载窗口结束后仍无法重新建会话/提交"

    for instance in stack.all_instances():
        if instance.unit != "agent-runtime":
            continue
        rss_mb = _rss_mb(instance)
        assert rss_mb < 8192, f"Runtime RSS {rss_mb:.0f}MB 超预算（疑似 OOM）"


async def _count_runs(database_url: str, tenant_id: str, run_ids: list[str]) -> int:
    import asyncpg

    connection = await asyncpg.connect(
        database_url.replace("postgresql+asyncpg://", "postgresql://"), timeout=10
    )
    try:
        return int(
            await connection.fetchval(
                "SELECT count(*) FROM runtime.run_record WHERE tenant_id=$1 "
                "AND id = ANY($2::uuid[]) AND is_deleted=false",
                tenant_id,
                run_ids,
            )
            or 0
        )
    finally:
        await connection.close()


def _create_and_submit(
    base_url: str, *, entry_id: str, cookie: str, context: str
) -> bool:
    import uuid

    import httpx

    with httpx.Client(
        base_url=base_url,
        timeout=30.0,
        trust_env=False,
        cookies={"muad_chat_session": cookie},
        headers={"X-Chat-Context": context},
    ) as client:
        created = client.post(
            f"/api/v1/webchat/entries/{entry_id}/conversations",
            headers={"Idempotency-Key": f"recover-{uuid.uuid4().hex}"},
        )
        if created.status_code != 201:
            return False
        conversation_id = str(created.json()["data"]["conversation_id"])
        submitted = client.post(
            f"/api/v1/webchat/entries/{entry_id}/messages",
            json={
                "conversation_id": conversation_id,
                "message_id": f"recover-{uuid.uuid4().hex}",
                "text": "恢复后的消息",
                "upload_ids": [],
            },
            headers={"Idempotency-Key": f"recover-{uuid.uuid4().hex}"},
        )
        return submitted.status_code == 202


def _rss_mb(instance: object) -> float:
    import subprocess

    pid = instance.pid
    output = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)], text=True)
    return int(output.strip() or 0) / 1024
