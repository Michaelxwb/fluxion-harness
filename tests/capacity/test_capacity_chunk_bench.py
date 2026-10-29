"""P0-1/P0-2 诊断工具的单元契约：微基准走真实 provider/emitter，指标摘要不重算。

真实边界：`chunk_bench` 用合成 OpenAI SSE 帧喂真实 `_parse_stream`（不启栈、不连 DB）；
`smoke_report` 只读取 `LoadReport`，缺失值保持 None。两者都不触碰数据库与网络。
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


chunk_bench = _load_script("capacity_chunk_bench_script", "scripts/capacity/chunk_bench.py")
smoke_report = _load_script("capacity_smoke_report_script", "scripts/capacity/smoke_report.py")


def test_chunk_bench_measures_parse_and_emitter_paths() -> None:
    """四个场景都产生正数微秒；emitter 路径必须真的发生窗口 flush。"""
    report = asyncio.run(
        chunk_bench.run_bench(chunks=5, repeats=1, chunk_interval_ms=50, measure_settings=False)
    )
    rows = {row.scenario: row for row in report.rows}
    assert set(rows) == {"parse_only", "parse_window", "emitter_realistic", "emitter_flush_each"}
    assert all(row.us_per_chunk > 0 for row in rows.values())
    assert rows["parse_window"].events_per_run == 0
    # 真实窗口下至少首块 + 终态 flush；flush_each 每块一次。
    assert rows["emitter_realistic"].events_per_run >= 2
    assert rows["emitter_flush_each"].events_per_run == 5
    assert rows["emitter_flush_each"].us_per_chunk > rows["parse_only"].us_per_chunk


def test_chunk_bench_rejects_invalid_inputs() -> None:
    """空 chunks / 负节拍显式失败，不产出误导性数字。"""
    with pytest.raises(ValueError, match="chunks"):
        asyncio.run(chunk_bench.run_bench(chunks=0, repeats=1, measure_settings=False))
    with pytest.raises(ValueError, match="interval"):
        asyncio.run(
            chunk_bench.run_bench(chunks=1, repeats=1, chunk_interval_ms=-1, measure_settings=False)
        )


class _Summary:
    count = 2
    p50 = 10.0
    p95 = 20.0
    p99 = 25.0
    maximum = 30.0


class _Rate:
    commits_per_sec = 91.5
    event_rows_per_sec = 38.7
    wal_bytes_per_sec = 1234.5


def test_smoke_report_summarize_reads_load_report_without_recomputing() -> None:
    """摘要字段与 LoadReport 同口径；缺失的延迟保持 None，不拿 0 冒充。"""
    report = SimpleNamespace(
        effective_users=25,
        attempts=lambda: 25,
        admitted=lambda: (object(),) * 25,
        completed=lambda: (object(),) * 24,
        wall_seconds=lambda: 41.5,
        admission_latency=lambda: _Summary(),
        first_delta_latency=lambda: None,
        notification_latency=lambda: None,
        system_error_ratio=lambda: 0.0,
        failure_counts=lambda: {},
        max_connections_total=55,
        max_idle_in_tx=12,
        postgres_rate=lambda: _Rate(),
        unit_metric_deltas={"agent-runtime": {"run_persist_event_seconds_sum": 1.0}},
    )
    payload = smoke_report.summarize(report)
    assert payload["users"] == 25
    assert payload["admitted"] == 25
    assert payload["completed"] == 24
    assert payload["admission_ms"] == {
        "count": 2.0,
        "p50": 10.0,
        "p95": 20.0,
        "p99": 25.0,
        "max": 30.0,
    }
    assert payload["first_delta_ms"] is None
    assert payload["notification_ms"] is None
    assert payload["postgres"]["commits_per_sec"] == 91.5
    assert "unit_metrics" not in payload

    full = smoke_report.summarize(report, include_unit_metrics=True)
    assert full["unit_metrics"]["agent-runtime"]["run_persist_event_seconds_sum"] == 1.0
