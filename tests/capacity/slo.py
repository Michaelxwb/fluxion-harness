"""容量 SLO 判定（TASK-001；S-16/S-17/S-18 / S-01 共用）。

输入是驱动已采集的真实施加样本（`LatencySummary`、PG 采样、错误账目）；本模块只做
逐项判定，不生成样本、不读环境变量、不改阈值。缺样本或越界都显式失败，避免把
「没有样本」误判成「零延迟」。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from tests.capacity.metrics import LatencySummary
from tests.capacity.profile import CapacityProfile

__all__ = [
    "SloCheck",
    "SloInput",
    "SloThresholds",
    "evaluate_slo",
    "min_delta_frames",
    "require_slo",
    "thresholds_from_profile",
]


@dataclass(frozen=True, slots=True)
class SloThresholds:
    """一次运行的阈值；来自 profile 或场景契约，不在用例里另立默认值。"""

    admission_p95_ms: float
    first_delta_p95_ms: float
    max_system_error_ratio: float
    admission_p99_ms: float | None = None
    max_connections_total: int | None = None
    max_idle_in_tx: int | None = None


@dataclass(frozen=True, slots=True)
class SloInput:
    """一次运行的真实样本投影（缺样本用 None，不用 0 冒充）。"""

    admission: LatencySummary | None
    first_delta: LatencySummary | None
    system_error_ratio: float
    pool_timeouts: int
    attempts: int
    planned_attempts: int
    steady_violations: int
    max_connections_total: int | None = None
    max_idle_in_tx: int | None = None


@dataclass(frozen=True, slots=True)
class SloCheck:
    """一条可复核的 SLO 判定：实际值、阈值、是否通过、可读细节。"""

    name: str
    actual: float | int | None
    limit: float | int | None
    ok: bool
    detail: str


def thresholds_from_profile(profile: CapacityProfile) -> SloThresholds:
    """锁定 profile 的正式 SLO；缺少 slo 段显式失败（不回落默认阈值）。"""
    slo = profile.slo
    if slo is None:
        raise ValueError("容量 profile 缺少 slo：不能给出正式 SLO 判定")
    return SloThresholds(
        admission_p95_ms=slo.admission_p95_ms,
        admission_p99_ms=slo.admission_p99_ms,
        first_delta_p95_ms=slo.framework_first_delta_p95_ms,
        max_system_error_ratio=slo.max_system_error_ratio,
    )


def _latency_check(
    name: str,
    summary: LatencySummary | None,
    limit: float,
    *,
    quantile: str,
) -> SloCheck:
    if summary is None:
        return SloCheck(
            name=name,
            actual=None,
            limit=limit,
            ok=False,
            detail=f"缺少 {quantile} 样本（没有样本不给 pass）",
        )
    actual = summary.p95 if quantile == "P95" else summary.p99
    return SloCheck(
        name=name,
        actual=actual,
        limit=limit,
        ok=actual <= limit,
        detail=f"{quantile}={actual:.1f}ms 阈值 {limit:g}ms（样本 {summary.count}）",
    )


def evaluate_slo(data: SloInput, thresholds: SloThresholds) -> tuple[SloCheck, ...]:
    """逐项判定；不因第一条失败提前返回（调用方可一次看到全部越界项）。"""
    checks: list[SloCheck] = [
        _latency_check(
            "admission_p95_ms", data.admission, thresholds.admission_p95_ms, quantile="P95"
        ),
        _latency_check(
            "first_delta_p95_ms",
            data.first_delta,
            thresholds.first_delta_p95_ms,
            quantile="P95",
        ),
        SloCheck(
            name="system_error_ratio",
            actual=data.system_error_ratio,
            limit=thresholds.max_system_error_ratio,
            ok=data.system_error_ratio <= thresholds.max_system_error_ratio,
            detail=(
                f"系统错误率 {data.system_error_ratio:.4f} "
                f"阈值 {thresholds.max_system_error_ratio:g}"
            ),
        ),
        SloCheck(
            name="pool_timeouts",
            actual=data.pool_timeouts,
            limit=0,
            ok=data.pool_timeouts == 0,
            detail=f"连接池等待超时 {data.pool_timeouts} 次（必须为 0）",
        ),
        SloCheck(
            name="attempts",
            actual=data.attempts,
            limit=data.planned_attempts,
            ok=data.attempts == data.planned_attempts,
            detail=f"实际尝试 {data.attempts} / 计划 {data.planned_attempts}",
        ),
        SloCheck(
            name="steady_violations",
            actual=data.steady_violations,
            limit=0,
            ok=data.steady_violations == 0,
            detail=f"超出 ramp+steady 窗口的提交 {data.steady_violations} 次",
        ),
    ]
    if thresholds.admission_p99_ms is not None:
        checks.append(
            _latency_check(
                "admission_p99_ms",
                data.admission,
                thresholds.admission_p99_ms,
                quantile="P99",
            )
        )
    if thresholds.max_connections_total is not None:
        actual = data.max_connections_total
        checks.append(
            SloCheck(
                name="max_connections_total",
                actual=actual,
                limit=thresholds.max_connections_total,
                ok=actual is not None and actual <= thresholds.max_connections_total,
                detail=f"PG 连接峰值 {actual} 阈值 {thresholds.max_connections_total}",
            )
        )
    if thresholds.max_idle_in_tx is not None:
        actual = data.max_idle_in_tx
        checks.append(
            SloCheck(
                name="max_idle_in_tx",
                actual=actual,
                limit=thresholds.max_idle_in_tx,
                ok=actual is not None and actual <= thresholds.max_idle_in_tx,
                detail=f"idle-in-tx 峰值 {actual} 阈值 {thresholds.max_idle_in_tx}",
            )
        )
    return tuple(checks)


def require_slo(checks: Sequence[SloCheck]) -> None:
    """任一越界即抛出带全部失败项的 AssertionError（不静默、不截断）。"""
    failed = [check for check in checks if not check.ok]
    if not failed:
        return
    details = "; ".join(f"{check.name}: {check.detail}" for check in failed)
    raise AssertionError(f"SLO 未通过（{len(failed)} 项）：{details}")


def min_delta_frames(stream_ms: float, flush_ms: float, tolerance: float = 0.8) -> int:
    """合并后的增量帧数下界：由 flush 窗口推导，不写死。

    运行时按 `flush_ms` 合并增量（首段立即 flush），`stream_ms` 的流合并后约有
    `stream_ms / flush_ms` 帧；留出首帧/尾帧余量后取下界。写死下界会在 flush 配置
    变化后与实现脱节（TASK-004 把窗口从 50ms 调到 300ms 时就是这样）。
    """
    if stream_ms <= 0 or flush_ms <= 0:
        raise ValueError("stream_ms 与 flush_ms 必须为正")
    if not 0 < tolerance <= 1:
        raise ValueError("tolerance 必须在 (0, 1] 之间")
    return max(1, math.floor(stream_ms / flush_ms * tolerance))
