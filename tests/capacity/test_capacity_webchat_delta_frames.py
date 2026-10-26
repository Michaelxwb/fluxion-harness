"""[TASK-001 侧] 增量帧数下界必须由 flush 窗口推导，防止断言与实现脱节。

S-16 曾把下界写死成 150：TASK-004 把合并窗口从 50ms 调到 300ms 后，10 秒流的正确
合并结果只有约 33 帧，断言与实现事实冲突（系统没坏，指标口径过期）。本文件把
「下界 = f(流时长, flush 窗口)」固化成可执行约束。
"""

from __future__ import annotations

import pytest

from tests.capacity.slo import min_delta_frames


def test_bound_follows_the_flush_window() -> None:
    assert min_delta_frames(10_000, 50) == 160
    assert min_delta_frames(10_000, 100) == 80
    assert min_delta_frames(10_000, 300) == 26


def test_bound_never_drops_below_one_frame() -> None:
    assert min_delta_frames(100, 1_000) == 1


@pytest.mark.parametrize(
    ("stream_ms", "flush_ms", "tolerance"),
    [
        (0, 300, 0.8),
        (-1, 300, 0.8),
        (10_000, 0, 0.8),
        (10_000, -1, 0.8),
        (10_000, 300, 0),
        (10_000, 300, 1.5),
    ],
)
def test_invalid_inputs_fail_loudly(stream_ms: float, flush_ms: float, tolerance: float) -> None:
    with pytest.raises(ValueError):
        min_delta_frames(stream_ms, flush_ms, tolerance)


def test_smoke_bound_is_derived_from_its_own_stack_config() -> None:
    """S-16 的下界必须是 SMOKE_ENV 中 flush 窗口的推导值，写死即失败。"""
    from tests.acceptance.capacity import test_capacity_smoke as smoke

    assert smoke.SMOKE_FLUSH_INTERVAL_MS == int(
        smoke.SMOKE_ENV["agent-runtime"]["STREAM_DELTA_FLUSH_INTERVAL_MS"]
    )
    assert smoke.MIN_SMOKE_DELTA_FRAMES == min_delta_frames(
        smoke.SMOKE_STREAM_MS, smoke.SMOKE_FLUSH_INTERVAL_MS
    )
