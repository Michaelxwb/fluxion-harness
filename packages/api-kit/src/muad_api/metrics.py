"""进程内指标注册表与 Prometheus 文本导出（最小落地，不引入新依赖）。

design 10-im-gateway §4.2（2026-09-23 决策）：仓库当前没有指标基础设施，由本模块提供
进程内注册表，并由各服务暴露真实 HTTP `GET /metrics`（Prometheus 文本格式），
OTel/监控系统经该端点抓取。标签由调用方约束，不得包含 Secret/凭据/消息正文。
"""

from __future__ import annotations

import threading
from collections.abc import Mapping

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
DEFAULT_KIND = "gauge"

Labels = Mapping[str, str]
SampleKey = tuple[str, tuple[tuple[str, str], ...]]


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


class MetricsRegistry:
    """线程安全的最小指标注册表：gauge / counter + Prometheus 文本导出。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: dict[SampleKey, float] = {}
        self._kinds: dict[str, str] = {}
        self._help: dict[str, str] = {}

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

    def render(self) -> str:
        with self._lock:
            samples = dict(self._samples)
            kinds = dict(self._kinds)
            help_text = dict(self._help)
        lines: list[str] = []
        for name in sorted({key[0] for key in samples}):
            if name in help_text:
                lines.append(f"# HELP {name} {_escape(help_text[name])}")
            lines.append(f"# TYPE {name} {kinds.get(name, DEFAULT_KIND)}")
            for (sample_name, labels), value in sorted(samples.items()):
                if sample_name == name:
                    lines.append(f"{name}{_render_labels(labels)} {_format_value(value)}")
        return "\n".join(lines) + "\n" if lines else ""

    def _register(self, name: str, kind: str, help_text: str) -> None:
        self._kinds[name] = kind
        if help_text:
            self._help[name] = help_text


REGISTRY = MetricsRegistry()


def set_gauge(
    name: str, value: float, labels: Labels | None = None, *, help: str = ""
) -> None:
    REGISTRY.set_gauge(name, value, labels, help=help)


def inc_counter(
    name: str, value: float = 1.0, labels: Labels | None = None, *, help: str = ""
) -> None:
    REGISTRY.inc_counter(name, value, labels, help=help)


def render_metrics() -> str:
    return REGISTRY.render()


def install_metrics(app: FastAPI, registry: MetricsRegistry | None = None) -> MetricsRegistry:
    """注册真实 HTTP `GET /metrics`（Prometheus 文本格式，不引入新依赖）。"""
    target = registry or REGISTRY

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint() -> PlainTextResponse:
        return PlainTextResponse(target.render(), media_type=PROMETHEUS_CONTENT_TYPE)

    return target
