"""运行时诊断探针：事件循环 lag 与 GC 停顿只测量、不改业务语义。"""

from __future__ import annotations

import asyncio
import time

import pytest
from muad_agent_runtime import main


@pytest.fixture(autouse=True)
def _reset_gc_buffers() -> None:
    """探针缓冲是进程级定长数组；每个用例从零开始，避免样本串场。"""
    main._GC_RING[:] = [0.0] * main._GC_RING_SIZE
    main._GC_RING_WRITE[0] = 0
    main._GC_STARTED[:] = [0.0, 0.0, 0.0]
    main._GC_TOTALS[:] = [0, 0, 0]
    yield


@pytest.mark.asyncio
async def test_loop_lag_measure_is_non_negative() -> None:
    lag = await main._measure_loop_lag(0.005)
    assert lag >= 0.0


@pytest.mark.asyncio
async def test_loop_lag_loop_records_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[tuple[str, float]] = []
    monkeypatch.setattr(
        main, "record_latency", lambda name, value: recorded.append((name, value))
    )
    task = asyncio.create_task(main._loop_lag_loop(0.002))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert recorded
    assert all(name == main.EVENT_LOOP_LAG_SECONDS for name, _ in recorded)
    assert all(value >= 0.0 for _, value in recorded)


def test_gc_callback_never_calls_metric_writers(monkeypatch: pytest.MonkeyPatch) -> None:
    """回调在 stop-the-world 窗口内运行：取 metrics 锁会与挂起线程互锁。"""

    def _fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("GC 回调不得调用指标写入（会取注册表锁）")

    monkeypatch.setattr(main, "record_latency", _fail)
    monkeypatch.setattr(main, "record_counter", _fail)
    on_gc = main._gc_probe_callback()
    on_gc("start", {"generation": 1})
    on_gc("stop", {"generation": 1})


def test_gc_callback_writes_pause_and_generation() -> None:
    on_gc = main._gc_probe_callback()
    on_gc("start", {"generation": 1})
    time.sleep(0.005)
    on_gc("stop", {"generation": 1})
    assert main._GC_RING_WRITE[0] == 1
    assert main._GC_TOTALS[1] == 1
    assert main._GC_RING[0] >= 0.005
    assert main._GC_STARTED[1] == 0.0
    on_gc("stop", {"generation": 9})
    assert main._GC_RING_WRITE[0] == 1


@pytest.mark.asyncio
async def test_gc_flush_loop_records_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    latencies: list[tuple[str, float]] = []
    counters: list[tuple[str, float, dict[str, str] | None]] = []
    monkeypatch.setattr(
        main, "record_latency", lambda name, value: latencies.append((name, value))
    )
    monkeypatch.setattr(
        main,
        "record_counter",
        lambda name, amount=1.0, labels=None: counters.append((name, amount, labels)),
    )
    main._GC_RING[0] = 0.123
    main._GC_RING_WRITE[0] = 1
    main._GC_TOTALS[2] = 3
    task = asyncio.create_task(main._gc_flush_loop(0.005))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (main.GC_PAUSE_SECONDS, 0.123) in latencies
    assert (main.GC_COLLECTIONS_TOTAL, 3.0, {"generation": "2"}) in counters
