"""TASK-001：容量基线纯函数层 —— SLO 判定、PG 速率/状态对账。

真实边界：
- 本文件不连服务、不造伪造回执；输入是驱动已采集的真实样本结构
  （`LatencySummary` / `PostgresSnapshot`），输出是逐项可复核的 SLO 判定。
- 断言的阈值来自 profile 或场景契约（S-16/S-17/S-18），不在用例里另立默认值。
"""

from __future__ import annotations

import pytest

from tests.capacity.metrics import (
    LatencySummary,
    MetricsError,
    PostgresRate,
    PostgresSnapshot,
    postgres_rate,
)
from tests.capacity.profile import load_profile
from tests.capacity.slo import (
    SloInput,
    SloThresholds,
    evaluate_slo,
    require_slo,
    thresholds_from_profile,
)


def _latency(p95: float, p99: float | None = None) -> LatencySummary:
    return LatencySummary(
        count=100,
        p50=p95 / 2,
        p95=p95,
        p99=p99 if p99 is not None else p95 * 1.2,
        maximum=p95 * 2,
    )


def _input(**overrides: object) -> SloInput:
    values: dict[str, object] = {
        "admission": _latency(400, 1200),
        "first_delta": _latency(1000),
        "system_error_ratio": 0.0,
        "pool_timeouts": 0,
        "attempts": 100,
        "planned_attempts": 100,
        "steady_violations": 0,
        "max_connections_total": 80,
        "max_idle_in_tx": 4,
    }
    values.update(overrides)
    return SloInput(**values)  # type: ignore[arg-type]


PROFILE = load_profile(
    "tests/capacity/profiles/agent-capacity-webchat.example.yml"
)


def test_slo_evaluation_passes_when_every_real_sample_is_within_profile() -> None:
    """S-17 口径：profile 的 P95/P99/首字/错误率全部满足，且无 pool timeout。"""
    checks = evaluate_slo(_input(), thresholds_from_profile(PROFILE))
    assert checks, "至少产出一条可复核的 SLO 判定"
    assert all(check.ok for check in checks), [check for check in checks if not check.ok]
    require_slo(checks)


def test_slo_evaluation_reports_each_breach_instead_of_stopping_at_the_first() -> None:
    """S-16/S-18：任一指标越界都显式列出（不因第一条失败掩盖其余指标）。"""
    data = _input(
        admission=_latency(900, 2000),
        first_delta=_latency(2600),
        system_error_ratio=0.02,
        pool_timeouts=3,
        attempts=99,
        planned_attempts=100,
        steady_violations=1,
        max_connections_total=140,
        max_idle_in_tx=21,
    )
    checks = evaluate_slo(
        data,
        SloThresholds(
            admission_p95_ms=500,
            admission_p99_ms=1500,
            first_delta_p95_ms=1200,
            max_system_error_ratio=0.01,
            max_connections_total=120,
            max_idle_in_tx=20,
        ),
    )
    failed = {check.name for check in checks if not check.ok}
    assert {
        "admission_p95_ms",
        "admission_p99_ms",
        "first_delta_p95_ms",
        "system_error_ratio",
        "pool_timeouts",
        "attempts",
        "steady_violations",
        "max_connections_total",
        "max_idle_in_tx",
    } <= failed
    with pytest.raises(AssertionError, match="SLO"):
        require_slo(checks)


def test_slo_refuses_missing_latency_samples_instead_of_treating_them_as_zero() -> None:
    """没有真实样本就不给 pass：缺 admission/首字样本显式失败。"""
    checks = evaluate_slo(
        _input(admission=None, first_delta=None),
        thresholds_from_profile(PROFILE),
    )
    failed = {check.name for check in checks if not check.ok}
    assert {"admission_p95_ms", "first_delta_p95_ms"} <= failed


def test_postgres_rate_uses_real_elapsed_time_and_never_divides_by_zero() -> None:
    """commits/s 与 WAL/s 从两次真实采样差值 + 真实 elapsed 计算；零窗口显式失败。"""
    before = PostgresSnapshot(
        observed_at=10.0,
        connections_total=40,
        connections_active=4,
        locks_granted=10,
        locks_waiting=0,
        xact_commit=1000,
        deadlocks=0,
        temp_bytes=0,
        database_bytes=1024,
        wal_lsn="0/1000",
        runs_by_status={},
        waiting_runs=0,
        running_runs=0,
        event_rows=200,
        event_bytes=4096,
        connections_idle=30,
        connections_idle_in_transaction=2,
        connections_waiting=0,
    )
    after = PostgresSnapshot(
        observed_at=12.0,
        connections_total=42,
        connections_active=5,
        locks_granted=12,
        locks_waiting=1,
        xact_commit=1400,
        deadlocks=0,
        temp_bytes=0,
        database_bytes=2048,
        wal_lsn="0/2000",
        runs_by_status={"COMPLETED": 3},
        waiting_runs=0,
        running_runs=1,
        event_rows=260,
        event_bytes=8192,
        connections_idle=30,
        connections_idle_in_transaction=4,
        connections_waiting=1,
    )
    rate = postgres_rate(before, after, wal_growth_bytes=9000)
    assert rate == PostgresRate(
        elapsed_sec=2.0,
        commits_per_sec=200.0,
        wal_bytes_per_sec=4500.0,
        event_rows_per_sec=30.0,
        connections_delta=2,
    )

    with pytest.raises(MetricsError):
        postgres_rate(after, before, wal_growth_bytes=1)


def test_postgres_state_breakdown_is_explicit_and_total_consistent() -> None:
    """连接状态是四个互斥桶：idle + idle-in-tx + active + 其它 = total。"""
    snapshot = PostgresSnapshot(
        observed_at=1.0,
        connections_total=100,
        connections_active=10,
        locks_granted=1,
        locks_waiting=0,
        xact_commit=0,
        deadlocks=0,
        temp_bytes=0,
        database_bytes=1,
        wal_lsn="0/1",
        runs_by_status={},
        waiting_runs=0,
        running_runs=0,
        event_rows=0,
        event_bytes=0,
        connections_idle=80,
        connections_idle_in_transaction=5,
        connections_waiting=2,
    )
    assert snapshot.connections_idle_in_transaction == 5
    assert snapshot.connections_waiting == 2
    assert snapshot.idle_connections_outside_transaction == 80
    assert snapshot.state_buckets_total <= snapshot.connections_total
