"""进程内指标注册表与 Prometheus 文本导出（最小落地，不引入新依赖）。

design 10-im-gateway §4.2（2026-09-23 决策）：仓库当前没有指标基础设施，由本模块提供
进程内注册表，并由各服务暴露真实 HTTP `GET /metrics`（Prometheus 文本格式），
OTel/监控系统经该端点抓取。标签由调用方约束，不得包含 Secret/凭据/消息正文。

**多 worker（design high-qps-remediation §4.1 / RISK-05）**：uvicorn `--workers N` 时每个
worker 是独立进程；只渲染本进程注册表会让 `/metrics` 每次抓取只命中一个 worker 的样本。
设置 `PROMETHEUS_MULTIPROC_DIR` 后启用**多进程聚合**（不引入 prometheus_client 依赖）：

- 每个进程每 `FLUSH_INTERVAL_SEC` 把自己的样本快照原子写进该目录（`muad_metrics_<pid>.json`）；
- `render()` 合并目录里**所有存活进程**的快照：counter 求和（全局事件数），gauge 保留
  **每进程一条**样本并追加 `pid` label（"active subscribers 总数 = 各 worker 之和"可按 pid 求和）。
  这与 Prometheus 多进程模式对 gauge 的 `all` 语义一致；
- 多进程模式下额外导出 `muad_process_up{pid=N} 1`：无流量的 worker 也可见（否则一个只做
  长连接的 worker 在指标上完全不存在）；
- 进程崩溃留下的快照超过 `STALE_SNAPSHOT_SEC` 未刷新即视为失效（清理并忽略），避免死进程的
  counter 永久重复计数。

未设置该环境变量时行为与改造前完全一致（进程内渲染、无 `pid` label）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
DEFAULT_KIND = "gauge"

#: 多进程快照目录（与 prometheus_client 同名，部署侧只学一个名字）。
MULTIPROC_ENV = "PROMETHEUS_MULTIPROC_DIR"
#: 每进程快照刷新间隔：抓取与进程归属的时效性由它决定。
FLUSH_INTERVAL_SEC = 2.0
#: 超过该时长没有刷新的快照视为失效进程（崩溃/已退出）。
STALE_SNAPSHOT_SEC = 30.0
PROCESS_UP_METRIC = "muad_process_up"

Labels = Mapping[str, str]
SampleKey = tuple[str, tuple[tuple[str, str], ...]]

logger = logging.getLogger(__name__)


def _label_key(labels: Labels | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(name), str(value)) for name, value in (labels or {}).items()))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _render_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    rendered = ",".join(f'{name}="{_escape(value)}"' for name, value in labels)
    return f"{{{rendered}}}"


def _format_value(value: float) -> str:
    return f"{value:g}"


def _render(
    samples: Mapping[SampleKey, float], kinds: Mapping[str, str], help_text: Mapping[str, str]
) -> str:
    lines: list[str] = []
    for name in sorted({key[0] for key in samples} | set(kinds)):
        if name in help_text:
            lines.append(f"# HELP {name} {_escape(help_text[name])}")
        lines.append(f"# TYPE {name} {kinds.get(name, DEFAULT_KIND)}")
        for (sample_name, labels), value in sorted(samples.items()):
            if sample_name == name:
                lines.append(f"{name}{_render_labels(labels)} {_format_value(value)}")
    return "\n".join(lines) + "\n" if lines else ""


class MetricsRegistry:
    """线程安全的最小指标注册表：gauge / counter + Prometheus 文本导出。"""

    def __init__(self, *, multiproc_dir: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._samples: dict[SampleKey, float] = {}
        self._kinds: dict[str, str] = {}
        self._help: dict[str, str] = {}
        self._pid = os.getpid()
        self._multiproc_dir = Path(multiproc_dir) if multiproc_dir else None
        self._snapshot_path: Path | None = None
        if self._multiproc_dir is not None:
            # 目录必须可写且存在：多进程模式的部署前提，失败即启动失败（不静默退化成单进程）。
            self._multiproc_dir.mkdir(parents=True, exist_ok=True)
            self._snapshot_path = self._multiproc_dir / f"muad_metrics_{self._pid}.json"
            thread = threading.Thread(
                target=self._flush_loop, name="metrics-multiproc-flush", daemon=True
            )
            thread.start()

    def set_gauge(
        self, name: str, value: float, labels: Labels | None = None, *, help: str = ""
    ) -> None:
        with self._lock:
            self._samples[(name, _label_key(labels))] = float(value)
            self._register(name, "gauge", help)

    def inc_counter(
        self, name: str, value: float = 1.0, labels: Labels | None = None, *, help: str = ""
    ) -> None:
        with self._lock:
            key = (name, _label_key(labels))
            self._samples[key] = self._samples.get(key, 0.0) + float(value)
            self._register(name, "counter", help)

    def declare(self, name: str, kind: str = DEFAULT_KIND, *, help: str = "") -> None:
        """只登记 HELP/TYPE、不写样本：让 `/metrics` 在无流量时也暴露完整指标目录。"""
        with self._lock:
            self._register(name, kind, help)

    def render(self) -> str:
        if self._multiproc_dir is None:
            with self._lock:
                samples = dict(self._samples)
                kinds = dict(self._kinds)
                help_text = dict(self._help)
            return _render(samples, kinds, help_text)
        self._write_snapshot()
        return self._render_multiproc()

    # ---- 多进程聚合 ---------------------------------------------------------------
    def _register(self, name: str, kind: str, help_text: str) -> None:
        self._kinds[name] = kind
        if help_text:
            self._help[name] = help_text

    def _flush_loop(self) -> None:
        while True:
            time.sleep(FLUSH_INTERVAL_SEC)
            try:
                self._write_snapshot()
            except OSError as exc:  # pragma: no cover - 目录权限/磁盘故障
                logger.warning("metrics_multiproc_flush_failed error=%s", type(exc).__name__)

    def _write_snapshot(self) -> None:
        with self._lock:
            payload = {
                "pid": self._pid,
                "written_at": time.time(),
                "samples": [
                    {"name": name, "labels": [list(pair) for pair in labels], "value": value}
                    for (name, labels), value in self._samples.items()
                ],
                "kinds": dict(self._kinds),
                "help": dict(self._help),
            }
        target = self._snapshot_path
        assert target is not None
        temporary = target.with_suffix(f".{self._pid}.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, target)

    def _read_snapshots(self) -> list[dict[str, Any]]:
        now = time.time()
        snapshots: list[dict[str, Any]] = []
        for path in sorted(self._multiproc_dir.glob("muad_metrics_*.json")):  # type: ignore[union-attr]
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue
            if age > STALE_SNAPSHOT_SEC:
                # 崩溃/已退出进程的残留：忽略并清理，避免 counter 被永久重复计数。
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # 正在写/损坏的文件：本轮忽略，下次抓取再来
            if isinstance(data, dict) and isinstance(data.get("pid"), int):
                snapshots.append(data)
        return snapshots

    def _render_multiproc(self) -> str:
        snapshots = self._read_snapshots()
        counter_help: dict[str, str] = {}
        counter_kind: dict[str, str] = {}
        counters: dict[SampleKey, float] = {}
        gauges: dict[SampleKey, float] = {}
        for snapshot in snapshots:
            pid = str(snapshot["pid"])
            kinds = snapshot.get("kinds", {})
            help_text = snapshot.get("help", {})
            for name, kind in kinds.items():
                counter_kind.setdefault(name, str(kind))
            for name, value in help_text.items():
                counter_help.setdefault(name, str(value))
            gauges[(PROCESS_UP_METRIC, _label_key({"pid": pid}))] = 1.0
            for sample in snapshot.get("samples", []):
                name = str(sample["name"])
                labels = tuple((str(key), str(value)) for key, value in sample["labels"])
                value = float(sample["value"])
                if counter_kind.get(name) == "counter":
                    counters[(name, labels)] = counters.get((name, labels), 0.0) + value
                else:
                    gauges[(name, tuple(sorted((*labels, ("pid", pid)))))] = value
        with self._lock:
            local_kinds = dict(self._kinds)
            local_help = dict(self._help)
        kinds = {**counter_kind, **local_kinds, PROCESS_UP_METRIC: "gauge"}
        help_text = {**counter_help, **local_help}
        help_text.setdefault(PROCESS_UP_METRIC, "Worker processes publishing metrics (multiproc)")
        merged: dict[SampleKey, float] = {**counters, **gauges}
        return _render(merged, kinds, help_text)


REGISTRY = MetricsRegistry(multiproc_dir=os.environ.get(MULTIPROC_ENV))


def set_gauge(
    name: str, value: float, labels: Labels | None = None, *, help: str = ""
) -> None:
    REGISTRY.set_gauge(name, value, labels, help=help)


def inc_counter(
    name: str, value: float = 1.0, labels: Labels | None = None, *, help: str = ""
) -> None:
    REGISTRY.inc_counter(name, value, labels, help=help)


def declare_metric(name: str, kind: str = DEFAULT_KIND, *, help: str = "") -> None:
    REGISTRY.declare(name, kind, help=help)


def render_metrics() -> str:
    return REGISTRY.render()


def install_metrics(app: FastAPI, registry: MetricsRegistry | None = None) -> MetricsRegistry:
    """注册真实 HTTP `GET /metrics`（Prometheus 文本格式，不引入新依赖）。"""
    target = registry or REGISTRY

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint() -> PlainTextResponse:
        return PlainTextResponse(target.render(), media_type=PROMETHEUS_CONTENT_TYPE)

    return target
