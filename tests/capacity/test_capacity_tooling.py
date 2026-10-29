"""容量工具脚本（`scripts/capacity/`）的单元契约。

真实边界：Prometheus 文本按生产线格式构造/解析（不 mock 解析器），ps 行取自真实 `ps` 输出形状。
不触碰数据库与网络。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.capacity.metrics import (
    MetricsError,
    PrometheusSample,
    UnitMetricsSnapshot,
    aggregate_metric_deltas,
)

ROOT = Path(__file__).resolve().parents[2]
BOUNDS = (0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)


def _load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


diag = _load_script("capacity_diag_script", "scripts/capacity/diag.py")
loadgen = _load_script("capacity_loadgen_script", "scripts/capacity/loadgen.py")


def _suffix(bound: float) -> str:
    if bound < 1.0:
        micros = round(bound * 1_000_000)
        return f"{micros // 1000}ms" if micros % 1000 == 0 else f"{micros}us"
    return f"{bound:g}s"


def _histogram_text(family: str, observations: list[float], labels: str = "") -> str:
    """按生产线格式写一组累计桶（`<family>_le_<上界>` + `_sum` + `_count`）。"""
    lines = [f"# TYPE {family} histogram"]
    for bound in BOUNDS:
        count = sum(1 for value in observations if value <= bound)
        lines.append(f"{family}_le_{_suffix(bound)}{labels} {count}")
    lines.append(f"{family}_count{labels} {len(observations)}")
    lines.append(f"{family}_sum{labels} {sum(observations)}")
    return "\n".join(lines) + "\n"


def test_histogram_summary_recomputes_percentiles_from_cumulative_buckets() -> None:
    """累计桶 → P50/P95/mean：分位数取“累计 ≥ q×n 的最小上界”。"""
    observations = [0.005, 0.03, 0.7, 3.0]
    samples = diag.parse_metrics(_histogram_text("run_consume_seconds", observations))
    summary = diag.histogram_summary(samples, "run_consume_seconds")
    assert summary is not None
    assert summary.count == 4
    assert summary.p50 == pytest.approx(0.05)
    assert summary.p95 == pytest.approx(5.0)
    assert summary.mean == pytest.approx(sum(observations) / len(observations))


def test_histogram_summary_filters_by_label_and_rejects_ambiguous_dimensions() -> None:
    """多维度必须显式过滤：跨维度相加会把不同口径的桶混在一起。"""
    text = (
        _histogram_text("run_consume_seconds", [0.01, 0.02], '{stage="persist"}')
        + _histogram_text("run_consume_seconds", [1.0, 2.0], '{stage="wait"}')
    )
    samples = diag.parse_metrics(text)
    with pytest.raises(diag.DiagError, match="维度"):
        diag.histogram_summary(samples, "run_consume_seconds")
    persist = diag.histogram_summary(samples, "run_consume_seconds", labels={"stage": "persist"})
    wait = diag.histogram_summary(samples, "run_consume_seconds", labels={"stage": "wait"})
    assert persist is not None and wait is not None
    assert persist.p95 == pytest.approx(0.02)
    assert wait.p95 == pytest.approx(2.0)


def test_histogram_summary_returns_none_without_samples() -> None:
    """窗口内没有样本就返回 None，不拿 0 冒充。"""
    samples = diag.parse_metrics("some_other_metric 3\n")
    assert diag.histogram_summary(samples, "run_consume_seconds") is None


def test_parse_metrics_is_strict() -> None:
    """空文本、坏行、重复序列都显式失败（诊断工具不静默回 0）。"""
    with pytest.raises(diag.DiagError, match="为空"):
        diag.parse_metrics("  \n# only comments\n")
    with pytest.raises(diag.DiagError, match="不是合法样本行"):
        diag.parse_metrics("this is not a sample\n")
    duplicated = "x_count 1\nx_count 1\n"
    with pytest.raises(diag.DiagError, match="重复"):
        diag.parse_metrics(duplicated)


def test_parse_ps_line_maps_uvicorn_processes_to_units() -> None:
    """真实 `ps -eo pid,pcpu,rss,etime,command` 输出形状：只认本栈 uvicorn 进程。"""
    line = (
        "  42012  60.2  92528       00:00 /repo/.venv/bin/python3 -m uvicorn "
        "muad_agent_runtime.main:app --host 127.0.0.1 --port 49450 --log-level error"
    )
    assert diag.parse_ps_line(line) == (42012, 60.2, "runtime")
    assert diag.parse_ps_line("  1  0.0  100 00:01 /bin/zsh -c grep -E uvicorn") is None
    assert diag.parse_ps_line("  PID %CPU RSS ELAPSED COMMAND") is None


def _config(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "base_url": "http://127.0.0.1:8003",
        "entry_id": "0b8e3a52-3f6e-4a5a-9c62-0a7b1f2f0d11",
        "runtime_url": "http://127.0.0.1:8001",
        "internal_token": "internal",
        "database_url": "postgresql+asyncpg://muad:pw@127.0.0.1:5432/muad",
        "tenant_id": "tenant-a",
        "users": [
            {"user_id": "6f1f0f36-6a36-4a3f-9f4d-8f0a1f6b2c01", "token": "t1"},
            {"user_id": "6f1f0f36-6a36-4a3f-9f4d-8f0a1f6b2c02", "token": "t2"},
        ],
        "scale": {"users": 2, "ramp_sec": 0, "steady_sec": 10, "turns_per_user": 1},
    }
    return {**base, **overrides}


def _write(tmp_path: Path, payload: dict[str, object]) -> Path:
    target = tmp_path / "load.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _metric_snapshot(base_url: str, admissions: float) -> UnitMetricsSnapshot:
    return UnitMetricsSnapshot(
        unit="agent-runtime",
        base_url=base_url,
        scraped_at=0.0,
        samples=(
            PrometheusSample(
                name="run_admission_total",
                labels={"status": "ACCEPTED"},
                value=admissions,
            ),
        ),
    )


def test_aggregate_metric_deltas_sums_replica_counters() -> None:
    """多副本各自计数：增量必须按副本相加，才等于入口分流后的总受理数。"""
    before = [_metric_snapshot("http://runtime-0:8001", 3), _metric_snapshot("http://runtime-1:8001", 5)]
    after = [_metric_snapshot("http://runtime-0:8001", 8), _metric_snapshot("http://runtime-1:8001", 9)]
    deltas = aggregate_metric_deltas(before, after)
    assert deltas['run_admission_total{status="ACCEPTED"}'] == pytest.approx(9.0)


def test_aggregate_metric_deltas_rejects_replica_set_change() -> None:
    """副本集合在前后两次抓取间变化（换端口/增减副本）时显式失败，不静默漏算。"""
    before = [_metric_snapshot("http://runtime-0:8001", 3)]
    after = [
        _metric_snapshot("http://runtime-0:8001", 4),
        _metric_snapshot("http://runtime-1:8001", 1),
    ]
    with pytest.raises(MetricsError, match="副本集合"):
        aggregate_metric_deltas(before, after)


def test_normalize_unit_targets_accepts_single_and_multiple_replicas() -> None:
    """配置边界：单地址写字符串、多副本写列表；空列表/非 http 显式失败。"""
    assert loadgen.normalize_unit_targets({"agent-runtime": "http://runtime-0:8001"}) == {
        "agent-runtime": ("http://runtime-0:8001",)
    }
    assert loadgen.normalize_unit_targets(
        {"agent-runtime": ["http://runtime-0:8001", "http://runtime-1:8001"]}
    ) == {"agent-runtime": ("http://runtime-0:8001", "http://runtime-1:8001")}
    with pytest.raises(loadgen.LoadgenError, match="地址"):
        loadgen.normalize_unit_targets({"agent-runtime": []})
    with pytest.raises(loadgen.LoadgenError, match="地址"):
        loadgen.normalize_unit_targets({"agent-runtime": ["runtime:8001"]})


def test_load_config_accepts_minimal_valid_payload(tmp_path: Path) -> None:
    """合法配置原样通过：凭据只来自文件，不引入仓库内默认值。"""
    config = loadgen.load_config(_write(tmp_path, _config()))
    assert config["scale"]["users"] == 2
    assert len(config["users"]) == 2


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"tenant_id": None}, "tenant_id"),
        ({"users": None, "sessions": None}, "users 与 sessions"),
        (
            {"users": [{"user_id": "6f1f0f36-6a36-4a3f-9f4d-8f0a1f6b2c01", "token": "t"}]},
            "凭据只有",
        ),
        (
            {"users": [{"user_id": "not-a-uuid", "token": "t"}, {"user_id": "x", "token": "t"}]},
            "UUID",
        ),
        ({"scale": {"users": 2, "ramp_sec": 0, "steady_sec": 0, "turns_per_user": 1}}, "steady_sec"),
    ],
)
def test_load_config_rejects_invalid_payloads(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    """配置缺陷显式失败：不会带着错误规模/凭据发起一次“看似有效”的负载。"""
    payload = _config(**overrides)
    if overrides.get("tenant_id", "") is None:
        payload.pop("tenant_id")
    with pytest.raises(loadgen.LoadgenError, match=message):
        loadgen.load_config(_write(tmp_path, payload))
