"""pytest 插件：把 `smoke_run` 的 `LoadReport` 打成一行机器可读指标。

用途：做 25/50/100 用户阶梯时，即使 SLO 断言失败也能拿到本次真实数字（admission、
first-delta、系统错误、PG 连接/idle-in-tx、wall）。插件不改变测试结论，只旁路读取 fixture。

用法：

```bash
PYTHONPATH=scripts/capacity MUAD_SMOKE_METRICS_OUT=/tmp/ladder-25.json \
CAPACITY_LOAD_USERS=25 uv run pytest -q -p smoke_report \
  tests/acceptance/capacity/test_capacity_smoke.py
```
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _latency(summary: Any) -> dict[str, float] | None:
    if summary is None:
        return None
    return {
        "count": float(summary.count),
        "p50": round(float(summary.p50), 1),
        "p95": round(float(summary.p95), 1),
        "p99": round(float(summary.p99), 1),
        "max": round(float(summary.maximum), 1),
    }


def summarize(report: Any, *, include_unit_metrics: bool = False) -> dict[str, Any]:
    """从 `LoadReport` 取阶梯关心的字段；不重算、不补造缺失值。"""
    rate = report.postgres_rate()
    payload: dict[str, Any] = {
        "users": report.effective_users,
        "attempts": report.attempts(),
        "admitted": len(report.admitted()),
        "completed": len(report.completed()),
        "wall_seconds": round(float(report.wall_seconds()), 2),
        "admission_ms": _latency(report.admission_latency()),
        "first_delta_ms": _latency(report.first_delta_latency()),
        "notification_ms": _latency(report.notification_latency()),
        "system_error_ratio": round(float(report.system_error_ratio()), 4),
        "failures": dict(report.failure_counts()),
        "max_connections_total": report.max_connections_total,
        "max_idle_in_tx": report.max_idle_in_tx,
        "postgres": {
            "commits_per_sec": round(float(rate.commits_per_sec), 2),
            "event_rows_per_sec": round(float(rate.event_rows_per_sec), 2),
            "wal_bytes_per_sec": round(float(rate.wal_bytes_per_sec), 1),
        },
    }
    if include_unit_metrics:
        payload["unit_metrics"] = {
            unit: dict(metrics) for unit, metrics in report.unit_metric_deltas.items()
        }
    return payload


def _write(payload: Mapping[str, Any]) -> None:
    target = os.environ.get("MUAD_SMOKE_METRICS_OUT", "").strip()
    if not target:
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def pytest_runtest_call(item: Any) -> None:
    """在测试函数执行前读已建好的模块级 fixture；断言失败也不影响这份记录。"""
    report = getattr(item, "funcargs", {}).get("smoke_run")
    if report is None:
        report = getattr(item, "funcargs", {}).get("load_run")
    if report is None:
        return
    payload = summarize(report)
    print("SMOKE-METRICS " + json.dumps(payload, ensure_ascii=False, sort_keys=True))
    _write(summarize(report, include_unit_metrics=True))
