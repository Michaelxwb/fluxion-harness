"""Runtime 指标目录（design 11-audit-observability §3.5 / docs/09 §6.2）。

只导出 api-kit 的进程内注册表 → 真实 HTTP `GET /metrics`（Prometheus 文本，供 OTel/监控抓取）；
指标不进入业务 Console 渲染，也不作为业务审计事实源（权威数据在 PostgreSQL 的审计表）。
label 只含 Agent/Skill 键、工具名、provider/model 标识与状态码；不得包含 Secret、凭据、
消息正文或资源 ID（UUID）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from fastapi import FastAPI
from muad_api import declare_metric, inc_counter, install_metrics

AGENT_RUNS_METRIC: Final = "agent_runs_total"
MODEL_INVOCATIONS_METRIC: Final = "model_invocations_total"
TOOL_CALLS_METRIC: Final = "tool_calls_total"
SKILL_LOAD_METRIC: Final = "skill_load_total"
EGRESS_CALLS_METRIC: Final = "egress_calls_total"
ARTIFACT_BYTES_METRIC: Final = "artifact_bytes_total"
RUN_RECLAIM_METRIC: Final = "run_reclaim_total"

COUNTER: Final = "counter"
GAUGE: Final = "gauge"

# 目录：(指标名, 类型, label 名, help)。安装时声明，使 `/metrics` 无流量时也暴露完整目录。
CATALOG: Final[tuple[tuple[str, str, tuple[str, ...], str], ...]] = (
    (AGENT_RUNS_METRIC, COUNTER, ("agent", "status"), "Agent runs by agent key and outcome"),
    (
        MODEL_INVOCATIONS_METRIC,
        COUNTER,
        ("provider", "model", "status"),
        "Model invocations by provider, model and status",
    ),
    (TOOL_CALLS_METRIC, COUNTER, ("kind", "tool", "status"), "Tool calls by kind, tool and status"),
    (SKILL_LOAD_METRIC, COUNTER, ("skill", "status"), "Skill package loads by skill key and status"),
    (EGRESS_CALLS_METRIC, COUNTER, ("platform", "status"), "Egress calls by platform and status"),
    (ARTIFACT_BYTES_METRIC, COUNTER, ("type",), "Artifact bytes written by artifact type"),
    (RUN_RECLAIM_METRIC, COUNTER, (), "Abandoned runs reaped back by the run reaper"),
)


def record_counter(
    name: str, amount: float = 1.0, labels: Mapping[str, str] | None = None
) -> None:
    """累加一次计数（非结局语义的指标，如 artifact 字节数）。"""
    inc_counter(name, amount, labels)


def record_outcome(name: str, status: str, labels: Mapping[str, str] | None = None) -> None:
    """按 `{status}` 记一次结果；status 取审计表口径（`OK`/`FAILED`）或 catalog 错误码。"""
    inc_counter(name, 1, {**(labels or {}), "status": status})


def install_runtime_metrics(app: FastAPI) -> None:
    """声明目录并注册真实 HTTP `GET /metrics`（复用 api-kit 进程内注册表）。"""
    for name, kind, _labels, help_text in CATALOG:
        declare_metric(name, kind, help=help_text)
    install_metrics(app)
